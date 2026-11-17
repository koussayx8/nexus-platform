#!/usr/bin/env python3
"""Offline checks on platform/policies (spec §11 "Policy tests - required in CI").

Needs PyYAML only; no Kyverno, no cluster. It does not evaluate policies - `kyverno test` does that.
It guards what `kyverno test` cannot see:

  * every NEXUS policy is Enforce with failurePolicy Fail, admission only, and matches only
    Deployment/scale, Pod/eviction and Incident (verify-images is the one Audit exception);
  * every rule of K1-K6 has at least one test result, and every validating rule has both a
    `pass` and a `fail` result ("each policy carries both directions");
  * every test result names a policy and rule that exist, and a resource that exists;
  * approvers-rbac.yaml grants no wildcard, no delete and nothing on incidents/status.

Usage: python3 scripts/tests/policies-offline.py [policies-dir]
"""
import pathlib
import sys

import yaml

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "platform/policies")
ALLOWED_KINDS = {"Deployment/scale", "Pod/eviction", "nexus.io/v1alpha1/Incident"}
NEXUS_POLICIES = {
    "k1-autonomy-floor.yaml", "k2-scale-bounds.yaml", "k3-l2-approval.yaml",
    "k4-killswitch.yaml", "k5-incident-approval.yaml", "k6-incident-source.yaml",
}
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def load(path: pathlib.Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


# ---------------------------------------------------------------- the policies
rules_of: dict[str, dict[str, dict]] = {}  # policy name -> rule name -> rule
for fname in sorted(NEXUS_POLICIES):
    path = ROOT / fname
    if not path.exists():
        err(f"{fname}: missing")
        continue
    (doc,) = load(path)
    name = doc["metadata"]["name"]
    spec = doc["spec"]
    if doc.get("kind") != "ClusterPolicy":
        err(f"{fname}: kind is not ClusterPolicy")
    if spec.get("failurePolicy") != "Fail":
        err(f"{fname}: failurePolicy must be Fail")
    if spec.get("background", True) is not False:
        err(f"{fname}: background must be false (admission only)")
    if spec.get("validationFailureAction") not in (None, "Enforce"):
        err(f"{fname}: validationFailureAction must be Enforce")
    rules_of[name] = {}
    for rule in spec["rules"]:
        rules_of[name][rule["name"]] = rule
        kinds = {k for m in rule["match"]["any"] for k in m["resources"]["kinds"]}
        if not kinds <= ALLOWED_KINDS:
            err(f"{fname}/{rule['name']}: matches {sorted(kinds - ALLOWED_KINDS)}")
        if "validate" in rule and rule["validate"].get("failureAction") != "Enforce":
            err(f"{fname}/{rule['name']}: validate.failureAction must be Enforce")
        if "mutate" in rule and "validate" in rule:
            err(f"{fname}/{rule['name']}: mutate and validate in one rule")

# verify-images: Audit, never blocking.
(vi,) = load(ROOT / "verify-images.yaml")
if vi["spec"].get("failurePolicy") != "Ignore":
    err("verify-images.yaml: failurePolicy must be Ignore while in Audit")
for rule in vi["spec"]["rules"]:
    for item in rule.get("verifyImages", []):
        if item.get("failureAction") != "Audit":
            err("verify-images.yaml: every verifyImages entry must be Audit")
        if "keyless" not in item["attestors"][0]["entries"][0]:
            err("verify-images.yaml: expected a keyless attestor")

# ---------------------------------------------------------------- the tests
results: dict[tuple[str, str], list[str]] = {}
tests_dir = ROOT / "tests"
for tfile in sorted(tests_dir.rglob("kyverno-test.yaml")):
    (test,) = load(tfile)
    resources = {
        (d["kind"], d["metadata"]["name"])
        for r in test["resources"]
        for d in load(tfile.parent / r)
    }
    for pol in test["policies"]:
        if not (tfile.parent / pol).exists():
            err(f"{tfile}: policy file {pol} not found")
    for res in test["results"]:
        pol, rule = res["policy"], res["rule"]
        if pol not in rules_of or rule not in rules_of[pol]:
            err(f"{tfile}: unknown policy/rule {pol}/{rule}")
            continue
        for rname in res["resources"]:
            if (res["kind"], rname) not in resources:
                err(f"{tfile}: result for missing resource {res['kind']}/{rname}")
        if res["result"] not in ("pass", "fail", "skip"):
            err(f"{tfile}: bad result {res['result']}")
        if "patchedResources" in res and not (tfile.parent / res["patchedResources"]).exists():
            err(f"{tfile}: patchedResources {res['patchedResources']} not found")
        results.setdefault((pol, rule), []).append(res["result"])

for pol, rules in rules_of.items():
    for rname, rule in rules.items():
        got = results.get((pol, rname), [])
        if not got:
            err(f"{pol}/{rname}: no test result")
        elif "validate" in rule:
            for want in ("pass", "fail"):
                if want not in got:
                    err(f"{pol}/{rname}: no `{want}` case (both directions are required)")
        elif "mutate" in rule and "pass" not in got:
            err(f"{pol}/{rname}: no case where the mutation applies")

# ---------------------------------------------------------------- approvers RBAC
for doc in load(ROOT / "approvers-rbac.yaml"):
    for rule in doc.get("rules", []):
        verbs, res = set(rule["verbs"]), set(rule["resources"])
        if "*" in verbs or "*" in res:
            err(f"approvers-rbac.yaml: {doc['metadata']['name']}: wildcard")
        if verbs & {"delete", "deletecollection", "update", "escalate", "bind", "impersonate"}:
            err(f"approvers-rbac.yaml: {doc['metadata']['name']}: forbidden verb {sorted(verbs)}")
        if "incidents/status" in res:
            err(f"approvers-rbac.yaml: {doc['metadata']['name']}: grants incidents/status")

if errors:
    print("policies-offline: FAIL")
    for e in errors:
        print("  -", e)
    sys.exit(1)
n = sum(len(v) for v in results.values())
print(f"policies-offline: ok ({len(rules_of)} policies, {n} test results)")
