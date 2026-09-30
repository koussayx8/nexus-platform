#!/usr/bin/env bash
# incident-crd.sh — tests for platform/crds/incidents.nexus.io.yaml on the envtest harness
# (TASKS.md M1b-6 6a; spec §8, §11): every CRD Validation rule C1-C4 in both directions, and a
# status update while the spec is frozen. With --negative it then removes each C-rule in turn,
# reruns the cases, and checks that exactly that rule's deny cases fail; the CRD from Git is
# applied again at the end.
#
# Needs a running harness (NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent envtest.sh up), jq and
# yq. It uses only the harness's admin kubeconfig, after `envtest.sh check`, and the harness's own
# kubectl: it applies the CRD and creates Incidents in nexus-system there. Names carry a per-run
# suffix, so reruns against the same harness do not collide. Never run by CI.
#
# Deny cases must fail for their own reason: C1-C3 by their message prefix, C4 by the API
# server's bounds message.
#
# Usage: NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent incident-crd.sh [--negative]
# Exit codes: 0 every case (and every control) as expected; 1 otherwise; 2 usage or guard error.
set -euo pipefail

die() { echo "incident-crd: $*" >&2; exit 2; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd "$here/../.." && pwd -P)
crd=$root/platform/crds/incidents.nexus.io.yaml
harness=$here/envtest.sh
negative=0
case ${1:-} in
  "") ;;
  --negative) negative=1 ;;
  *) die "usage: incident-crd.sh [--negative]" ;;
esac
[[ -z ${CI:-} && -z ${GITHUB_ACTIONS:-} ]] || die "refusing to run in CI; run by hand"
[[ ${KUBECONFIG-} == /nonexistent ]] || die "run with KUBECONFIG=/nonexistent (offline rule)"
[[ -n ${NEXUS_ENVTEST_DIR:-} ]] || die "set NEXUS_ENVTEST_DIR to the harness directory"
{ command -v jq && command -v yq; } > /dev/null || die "jq and yq must be on PATH"
dir=$(realpath -m -- "$NEXUS_ENVTEST_DIR")
admin=$dir/admin.kubeconfig
"$harness" status > /dev/null || die "the harness is not up: run envtest.sh up first"
"$harness" check "$admin" > /dev/null || die "the harness guard rejected $admin"

k() { "$dir/bin/kubectl" --kubeconfig "$admin" "$@"; }

REPLICAS='.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.approval.properties.parameters.properties.replicas'
NS=nexus-system

# apply_crd <file>: apply, wait until Established and until the served schema is the file's.
apply_crd() {
  local want got i
  k apply --server-side --force-conflicts -f "$1" > /dev/null
  k wait --for=condition=Established crd/incidents.nexus.io --timeout=30s > /dev/null
  want=$(yq -o json '.spec.versions[0].schema' "$1" | jq -S -c .)
  for i in $(seq 1 20); do
    got=$(k get crd incidents.nexus.io -o json | jq -S -c '.spec.versions[0].schema')
    [[ $got == "$want" ]] && break
    sleep 0.5
  done
  [[ $got == "$want" ]] || die "the served CRD schema does not match $1 after ${i} polls"
  sleep 2   # the API server swaps a changed CRD's validators asynchronously
}

# incident <name> <target-namespace> <target-kind> [replicas]: a manifest; with replicas, an approval.
incident() {
  cat <<EOF
apiVersion: nexus.io/v1alpha1
kind: Incident
metadata:
  name: $1
  namespace: $NS
spec:
  source: {type: detector, alertname: NexusCpuAnomaly, fingerprint: 3f9c7d}
  target: {namespace: $2, kind: $3, name: sample-api}
  detectedAt: "2026-10-14T09:12:03Z"
EOF
  if [[ -n ${4:-} ]]; then
    cat <<EOF
  approval: {decision: approved, actionId: scale_deployment, parameters: {replicas: $4}, proposalHash: "sha256:ab12", expiresAt: "2026-10-14T09:27:03Z"}
EOF
  fi
}

quiet=0
ran=()
failed=()
record() {  # record <case id> <ok 0|1> <detail>
  ran+=("$1")
  if [[ $2 == 1 ]]; then
    [[ $quiet == 1 ]] || echo "PASS $1"
  else
    failed+=("$1")
    [[ $quiet == 1 ]] || echo "FAIL $1: $3"
  fi
}

# expect <allow|deny> <case id> <reason marker> <kubectl args...>; a manifest may come on stdin.
expect() {
  local want=$1 id=$2 marker=$3 out rc
  shift 3
  out=$(k "$@" 2>&1) && rc=0 || rc=$?
  if [[ $want == allow && $rc == 0 ]] || [[ $want == deny && $rc != 0 && $out == *"$marker"* ]]; then
    record "$id" 1 ""
  else
    record "$id" 0 "want $want, got exit $rc: $(tr '\n' ' ' <<< "$out" | cut -c1-220)"
  fi
}

run_suite() {
  local s got
  s=$(date +%s)$RANDOM
  ran=()
  failed=()

  # C3: target kind and namespace, checked on create.
  expect allow C3-allow-nexus-dev - create -f - <<< "$(incident "c3a-$s" nexus-dev Deployment)"
  expect allow C3-allow-nexus-prod - create -f - <<< "$(incident "c3b-$s" nexus-prod Deployment)"
  expect allow C3-allow-nexus-data - create -f - <<< "$(incident "c3c-$s" nexus-data Deployment)"
  expect deny C3-deny-statefulset "C3:" create -f - <<< "$(incident "c3d-$s" nexus-data StatefulSet)"
  expect deny C3-deny-kube-system "C3:" create -f - <<< "$(incident "c3e-$s" kube-system Deployment)"
  expect deny C3-deny-default-ns "C3:" create -f - <<< "$(incident "c3f-$s" default Deployment)"

  # C1: source, target and detectedAt frozen after create; other updates pass.
  expect allow C1-create - create -f - <<< "$(incident "c1-$s" nexus-dev Deployment)"
  expect allow C1-allow-metadata - label incident "c1-$s" -n "$NS" nexus.io/test=c1
  expect deny C1-deny-source "C1:" patch incident "c1-$s" -n "$NS" --type=merge \
    -p '{"spec":{"source":{"alertname":"NexusLatencyAnomaly"}}}'
  expect deny C1-deny-target "C1:" patch incident "c1-$s" -n "$NS" --type=merge \
    -p '{"spec":{"target":{"name":"other-api"}}}'
  expect deny C1-deny-detectedAt "C1:" patch incident "c1-$s" -n "$NS" --type=merge \
    -p '{"spec":{"detectedAt":"2026-10-14T09:13:03Z"}}'

  # C2: the first approval is accepted, then it never changes or disappears.
  expect allow C2-create - create -f - <<< "$(incident "c2-$s" nexus-dev Deployment)"
  expect allow C2-allow-first - patch incident "c2-$s" -n "$NS" --type=merge \
    -p '{"spec":{"approval":{"decision":"approved","actionId":"scale_deployment","parameters":{"replicas":3},"proposalHash":"sha256:ab12","expiresAt":"2026-10-14T09:27:03Z"}}}'
  expect allow C2-allow-unchanged - label incident "c2-$s" -n "$NS" nexus.io/test=c2
  expect deny C2-deny-parameters "C2:" patch incident "c2-$s" -n "$NS" --type=merge \
    -p '{"spec":{"approval":{"parameters":{"replicas":4}}}}'
  expect deny C2-deny-decision "C2:" patch incident "c2-$s" -n "$NS" --type=merge \
    -p '{"spec":{"approval":{"decision":"rejected"}}}'
  expect deny C2-deny-remove "C2:" patch incident "c2-$s" -n "$NS" --type=json \
    -p '[{"op":"remove","path":"/spec/approval"}]'

  # C4: approval.parameters.replicas within [1, 5].
  expect allow C4-allow-1 - create -f - <<< "$(incident "c4a-$s" nexus-dev Deployment 1)"
  expect allow C4-allow-5 - create -f - <<< "$(incident "c4b-$s" nexus-dev Deployment 5)"
  expect deny C4-deny-0 "greater than or equal to 1" create -f - <<< "$(incident "c4c-$s" nexus-dev Deployment 0)"
  expect deny C4-deny-6 "less than or equal to 5" create -f - <<< "$(incident "c4d-$s" nexus-dev Deployment 6)"

  # Status while the spec is frozen: the status subresource accepts status and ignores spec.
  expect allow S-create - create -f - <<< "$(incident "st-$s" nexus-prod Deployment)"
  expect allow S-allow-status - patch incident "st-$s" -n "$NS" --subresource=status --type=merge \
    -p '{"status":{"phase":"Recorded","reason":"level_observe","autonomyLevel":0}}'
  expect allow S-allow-status-with-spec - patch incident "st-$s" -n "$NS" --subresource=status --type=merge \
    -p '{"spec":{"target":{"name":"other-api"}},"status":{"phase":"Escalated","reason":"proposed_escalation"}}'
  got=$(k get incident "st-$s" -n "$NS" -o jsonpath='{.spec.target.name}/{.status.phase}' 2>&1 || true)
  if [[ $got == sample-api/Escalated ]]; then record S-spec-ignored 1 ""; else
    record S-spec-ignored 0 "want sample-api/Escalated, got $got"; fi
  expect deny S-deny-bad-phase "Unsupported value" patch incident "st-$s" -n "$NS" --subresource=status \
    --type=merge -p '{"status":{"phase":"Bogus"}}'

  if [[ $quiet == 0 ]]; then
    echo "--- kubectl get inc (printer columns)"
    k get incidents -n "$NS" "c2-$s" "st-$s"
  fi
}

# mutate <rule> <out file>: the CRD from Git without that rule (C4: without the replicas bounds).
# A rule list left empty is dropped too: the API server does not store an empty list, and
# apply_crd compares the served schema with the file.
mutate() {
  if [[ $1 == C4 ]]; then
    yq "del($REPLICAS.minimum, $REPLICAS.maximum)" "$crd" > "$2"
  else
    yq "del(.. | select(tag == \"!!map\" and has(\"rule\") and (.message | test(\"^$1:\"))))
      | del(.. | select(tag == \"!!map\" and has(\"x-kubernetes-validations\"))
                | select(.[\"x-kubernetes-validations\"] | length == 0) | .[\"x-kubernetes-validations\"])" \
      "$crd" > "$2"
  fi
}

apply_crd "$crd"
k get namespace "$NS" > /dev/null 2>&1 || k create namespace "$NS" > /dev/null
echo "== cases against the CRD from Git"
run_suite
status=0
if (( ${#failed[@]} == 0 )); then
  echo "RESULT: PASS (${#ran[@]} cases)"
else
  echo "RESULT: FAIL (${#failed[@]} of ${#ran[@]}: ${failed[*]})"
  status=1
fi

if (( negative )); then
  echo "== negative controls: each C-rule removed in turn"
  for c in C1 C2 C3 C4; do
    tmp=$(mktemp "$dir/crd-without-$c-XXXXXX.yaml")
    mutate "$c" "$tmp"
    apply_crd "$tmp"
    quiet=1
    run_suite
    quiet=0
    want=$(printf '%s\n' "${ran[@]}" | grep -E "^$c-deny-" | sort | paste -sd' ' - || true)
    got=$( ((${#failed[@]})) && printf '%s\n' "${failed[@]}" | sort | paste -sd' ' - || true)
    if [[ -n $want && $got == "$want" ]]; then
      echo "control $c: OK, exactly its deny cases failed: $got"
    else
      echo "control $c: NOT OK; failed: ${got:-none}; expected: $want"
      status=1
    fi
    rm -f "$tmp"
  done
  apply_crd "$crd"
  echo "== the CRD from Git is applied again"
fi
exit "$status"
