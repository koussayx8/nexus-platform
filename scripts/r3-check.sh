#!/usr/bin/env bash
# r3-check.sh — one call to the Reasoner's own endpoint with a fixed fixture bundle. The owner runs it.
#
#   bash scripts/r3-check.sh [--plan]
#
# The by-hand check of m4-protocol §4, "one successful call to the R3 model, r3-primary on NVIDIA NIM":
#   1. GET  /healthz              (read-only) the arm R3 is served by the config `r3-primary` and the model
#                                 id experiments/harness/arms.py expects
#   2. POST /v1/propose           one call: mode llm, arm R3, level L3, the fixture bundle below
#   3. checks: HTTP 200; proposal/v1 valid by the operator's own validator (operator/nexus_operator/
#      proposal.py, as the Reasoner uses it); metadata.modelConfig r3-primary; metadata.model the arm's id;
#      metadata.mode llm; the wall-clock latency against the arm's 300 s budget.
# `--plan` stops after step 1 and prints what step 2 would send. Nothing is sent to the model.
#
# Both requests go through the API server's service proxy, as precheck.reasoner_healthz does:
#   /api/v1/namespaces/nexus-reasoner/services/http:nexus-reasoner:8080/proxy/{healthz,v1/propose}
# so the Reasoner's NetworkPolicy and the cluster-admin credential of the owner's kubeconfig are the only
# things involved. The context is always named: NEXUS_CTX, default `default`.
#
# Why it opens no Incident and changes nothing: an Incident is created by the operator's poller from an
# Alertmanager alert (operator/nexus_operator/poller.py), never by a call to the Reasoner. /v1/propose is a
# pure function of its request (reasoner/nexus_reasoner/service.py `Reasoner.propose`): it retrieves
# runbooks from its baked index, builds the prompt, calls the model and returns the body. The Reasoner
# has no Kubernetes credential (`automountServiceAccountToken: false`, reasoner/k8s/deployment.yaml) and no
# database client, so it cannot write to the cluster or the Flight Recorder; the operator stores proposals,
# and the operator is not in this path. `incidentUid` in the request is a label echoed in the metadata.
# What does change: one call is spent on the NVIDIA NIM endpoint (the Reasoner's own key; none appears here),
# and the API audit log records one `create` on services/proxy by the owner (not an operator mutation, so
# audit-join ignores it). Nothing under reasoner/ is read for writing or changed by this script.
#
# The fixture is experiments/harness/tests/fixtures/evidence-v1.1-raw.json (the Reasoner's own test bundle),
# with redaction.counts added and `hash` set to the canonical hash, exactly as reasoner/tests/helpers.py
# builds it. It is a thin bundle: the check is of the arm, the schema and the latency, not of the diagnosis.
#
# UNTESTED UNTIL THE OWNER'S FIRST RUN: written offline; the POST through the service proxy has not been
# sent from this machine (only GET /healthz is known to work that way). Exit 0 all checks pass, 1 a check
# failed, 2 usage or the Reasoner unreachable.
set -euo pipefail

PLAN=0
case "${1:-}" in
  "") ;;
  --plan) PLAN=1 ;;
  *) echo "usage: $0 [--plan]" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo "usage: $0 [--plan]" >&2; exit 2; }

CTX=${NEXUS_CTX:-default}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROXY=/api/v1/namespaces/nexus-reasoner/services/http:nexus-reasoner:8080/proxy
FIXTURE=$REPO/experiments/harness/tests/fixtures/evidence-v1.1-raw.json
[[ -f $FIXTURE ]] || { echo "r3-check: fixture missing: $FIXTURE" >&2; exit 2; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "r3-check: context $CTX"
if ! kubectl --context "$CTX" get --raw "$PROXY/healthz" > "$tmp/health.json" 2> "$tmp/health.err"; then
  echo "r3-check: GET /healthz failed: $(head -c 300 "$tmp/health.err")" >&2
  exit 2
fi

# Step 1 and the request body. The arm's expectations come from arms.py, the one place they are written.
step1=0
python3 -I - "$REPO" "$FIXTURE" "$tmp" <<'PY' || step1=$?
import hashlib, json, sys
repo, fixture, tmp = sys.argv[1:4]
sys.path.insert(0, repo)
from experiments.harness import arms

arm = arms.ARMS["R3"]
want_cfg, want_model = arm["model_config"], arm["model_id"]
health = json.load(open(f"{tmp}/health.json"))
active = (health.get("active") or {}).get("R3") or {}
ok = (
    health.get("status") == "ready"
    and active.get("modelConfig") == want_cfg
    and active.get("model") == want_model
)
print(f"{'PASS' if ok else 'FAIL'} healthz: status={health.get('status')} R3={active.get('modelConfig')}"
      f" / {active.get('model')} (arms.py expects {want_cfg} / {want_model})")
print(f"info healthz: promptVersion={health.get('promptVersion')} scoreFloor={health.get('scoreFloor')}"
      f" corpusHash={health.get('corpusHash')}")

b = json.load(open(fixture))
b["redaction"] = {"counts": {"token": 0}}
b.pop("hash", None)
# proposal.hash_of: sha256 of the canonical JSON of the body without `hash`
b["hash"] = "sha256:" + hashlib.sha256(
    json.dumps({k: v for k, v in b.items() if k != "hash"}, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
req = {"bundle": b, "context": {"mode": "llm", "level": "L3", "model": "R3",
                                "catalogueVersion": "1", "incidentUid": "r3-check"}}
json.dump(req, open(f"{tmp}/req.json", "w"))
print(f"info request: bundle {b['schema']} {b['hash'][:19]}…, {len(json.dumps(req))} bytes, arm R3, level L3")
sys.exit(0 if ok else 1)
PY

if [[ $PLAN -eq 1 ]]; then
  echo "r3-check: --plan: would POST $PROXY/v1/propose (one call to the model). Nothing sent."
  exit "$step1"
fi
[[ $step1 -eq 0 ]] || { echo "r3-check: stopping before the call: the arm is not served as expected" >&2; exit 1; }

start=$(date +%s.%N)
rc=0
kubectl --context "$CTX" --request-timeout=330s create --raw "$PROXY/v1/propose" -f "$tmp/req.json" \
  > "$tmp/resp.json" 2> "$tmp/resp.err" || rc=$?
end=$(date +%s.%N)

python3 -I - "$REPO" "$tmp" "$rc" "$start" "$end" <<'PY'
import json, sys
repo, tmp, rc, start, end = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
sys.path.insert(0, repo)
sys.path.insert(0, repo + "/operator")
from experiments.harness import arms
from nexus_operator import proposal

arm = arms.ARMS["R3"]
wall = end - start
fails = []

def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
    if not ok:
        fails.append(name)

if rc != 0:
    err = open(f"{tmp}/resp.err").read().strip()[:400]
    check("http", False, f"kubectl exit {rc}: {err}")
else:
    try:
        doc = json.load(open(f"{tmp}/resp.json"))
    except ValueError:
        doc = None
    check("http", isinstance(doc, dict) and "proposal" in doc, "200 with a proposal body" if doc else "not JSON")
    if isinstance(doc, dict) and "proposal" in doc:
        meta, prop = doc.get("metadata") or {}, doc["proposal"]
        errs = proposal.validate(prop)
        check("proposal/v1", not errs, "; ".join(errs) if errs else
              f"{prop['hypothesis']['faultClass']} / {prop['action']['id']} / confidence {prop['confidence']}")
        check("modelConfig", meta.get("modelConfig") == arm["model_config"], str(meta.get("modelConfig")))
        check("model", meta.get("model") == arm["model_id"], str(meta.get("model")))
        check("mode", meta.get("mode") == "llm", str(meta.get("mode")))
        print(f"info reasoner latencyMs={meta.get('latencyMs')} usage={meta.get('usage')} "
              f"promptTokensEstimate={meta.get('promptTokensEstimate')} promptVersion={meta.get('promptVersion')}")
check("latency", wall <= arm["timeout_s"], f"{wall:.1f} s wall clock (budget {arm['timeout_s']} s; "
      "six live calls on 2026-11-21: 14.3 to 21.9 s, median 16.4 s)")
print("r3-check: " + ("all checks passed" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
PY
