#!/usr/bin/env bash
# audit-policy.sh — offline tests for the audit policy and log rotation that scripts/bootstrap.sh
# writes (ADR-036; spec §14). Run by .github/scripts/repo-checks.sh. Needs bash, yq and python3
# (standard library); no cluster, no sudo.
#
# The policy is extracted from bootstrap.sh with the same sed command the owner's restart window
# uses (ADR-036), converted to JSON by yq, and evaluated by a small model of the API server's rule
# matching: the first rule whose users, groups, verbs and resources all match decides the level.
# Asserted: the rule order (the Incident-read exemption first, the two RequestResponse rules, the
# noise rules, then the write rule and the catch-all); the operator's writes and the approvers'
# writes stay at RequestResponse; every other write stays at Metadata; maxbackup is 30.
#
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bootstrap=${AUDIT_POLICY_BOOTSTRAP:-$here/../bootstrap.sh}   # override: show the test fails on an older file
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

# The owner's extraction (ADR-036, "Applying it"): the heredoc between AUDIT_POLICY_YAML=' and '.
sed -n "/^AUDIT_POLICY_YAML='/,/^'\$/p" "$bootstrap" | sed "1s/^AUDIT_POLICY_YAML='//;\$d" > "$work/policy.yaml"
sed -n '/^K3S_CONFIG_YAML="/,/^"$/p' "$bootstrap" | sed '1s/^K3S_CONFIG_YAML="//;$d' > "$work/config.yaml"
[[ -s $work/policy.yaml && -s $work/config.yaml ]] || { echo "FAIL could not extract the policy or the config from bootstrap.sh"; exit 1; }
yq -o=json '.' "$work/policy.yaml" > "$work/policy.json"

python3 -I - "$work/policy.json" "$work/config.yaml" <<'PY'
import json
import sys

policy = json.load(open(sys.argv[1]))
config = open(sys.argv[2]).read()
OP = "system:serviceaccount:nexus-system:nexus-operator"
failed = 0


def level(user, verb, group, resource, groups=()):
    """The level of the first matching rule, and its index (API server semantics)."""
    for i, r in enumerate(policy["rules"]):
        if r.get("users") and user not in r["users"]:
            continue
        if r.get("userGroups") and not set(r["userGroups"]) & set(groups):
            continue
        if r.get("verbs") and verb not in r["verbs"]:
            continue
        if r.get("resources") and not any(
            rr.get("group", "") == group and resource in rr.get("resources", [])
            for rr in r["resources"]
        ):
            continue
        return r["level"], i
    return None, None


def case(name, want, *args, **kw):
    global failed
    got, idx = level(*args, **kw)
    if got == want:
        print(f"PASS {name}: {got} (rule {idx + 1})")
    else:
        print(f"FAIL {name}: {got} want {want}")
        failed = 1


def check(name, ok, detail=""):
    global failed
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""))
    failed |= 0 if ok else 1


# Rule order, explicitly.
levels = [r["level"] for r in policy["rules"]]
check("rule-levels-in-order", levels == ["None", "RequestResponse", "RequestResponse", "None", "None", "Metadata", "None"], str(levels))
first = policy["rules"][0]
check("first-rule-is-the-operator-incident-read-exemption",
      first.get("users") == [OP] and first.get("verbs") == ["get", "list", "watch"]
      and first.get("resources") == [{"group": "nexus.io", "resources": ["incidents", "incidents/status"]}])
check("catch-all-is-last-and-None", policy["rules"][-1] == {"level": "None"})
check("exactly-one-Metadata-rule-and-it-covers-every-write",
      [r.get("verbs") for r in policy["rules"] if r["level"] == "Metadata"] == [["create", "update", "patch", "delete", "deletecollection"]])
check("RequestReceived-omitted", policy.get("omitStages") == ["RequestReceived"])

# The operator: reads of Incidents are not logged; every write stays at RequestResponse.
for v in ("get", "list", "watch"):
    for res in ("incidents", "incidents/status"):
        case(f"operator-{v}-{res}", "None", OP, v, "nexus.io", res)
for v in ("create", "update", "patch", "delete"):
    case(f"operator-{v}-incidents", "RequestResponse", OP, v, "nexus.io", "incidents")
for v in ("update", "patch"):
    case(f"operator-{v}-incidents-status", "RequestResponse", OP, v, "nexus.io", "incidents/status")
    case(f"operator-{v}-deployments-scale", "RequestResponse", OP, v, "apps", "deployments/scale")
case("operator-get-deployments-scale-unchanged", "RequestResponse", OP, "get", "apps", "deployments/scale")
case("operator-create-pods-eviction", "RequestResponse", OP, "create", "", "pods/eviction")
case("operator-denied-write-is-logged-too", "RequestResponse", OP, "patch", "apps", "deployments/scale")

# The approvers: writes on Incidents stay at RequestResponse.
for v in ("patch", "update"):
    case(f"approver-{v}-incidents", "RequestResponse", "alice", v, "nexus.io", "incidents", groups=["nexus-approvers"])
case("non-approver-patch-incidents-is-Metadata", "Metadata", "alice", "patch", "nexus.io", "incidents", groups=["devs"])

# Noise, by any identity.
for who in ("system:serviceaccount:kyverno:kyverno-admission-controller", "system:node:koussay", OP):
    for v in ("update", "patch"):
        case(f"lease-{v}-by-{who.split(':')[-1]}", "None", who, v, "coordination.k8s.io", "leases")
for res in ("selfsubjectaccessreviews", "selfsubjectrulesreviews"):
    case(f"create-{res}", "None", "system:serviceaccount:kyverno:kyverno-cleanup-controller", "create", "authorization.k8s.io", res)
case("create-lease-is-still-a-write", "Metadata", "system:node:koussay", "create", "coordination.k8s.io", "leases")
case("delete-lease-is-still-a-write", "Metadata", "admin", "delete", "coordination.k8s.io", "leases")
case("create-subjectaccessreviews-is-still-a-write", "Metadata", "system:serviceaccount:kube-system:metrics-server", "create", "authorization.k8s.io", "subjectaccessreviews")

# Every other write stays at Metadata; reads stay at None.
for v, g, res in (("patch", "argoproj.io", "applications"), ("update", "", "configmaps"), ("delete", "apps", "deployments"),
                  ("create", "", "pods"), ("patch", "apps", "deployments/scale"), ("deletecollection", "", "pods")):
    case(f"admin-{v}-{res}-is-Metadata", "Metadata", "admin", v, g, res)
case("admin-patch-incidents-is-Metadata", "Metadata", "admin", "patch", "nexus.io", "incidents")
case("admin-list-incidents-is-None", "None", "admin", "list", "nexus.io", "incidents")
case("get-pods-is-None", "None", "admin", "get", "", "pods")

# Rotation: 30 backups (ADR-036); age and size as before.
for want in ("audit-log-maxbackup=30", "audit-log-maxage=30", "audit-log-maxsize=100"):
    check(f"config-{want}", f"  - {want}\n" in config)
check("config-has-no-maxbackup-10", "audit-log-maxbackup=10" not in config)
sys.exit(failed)
PY
