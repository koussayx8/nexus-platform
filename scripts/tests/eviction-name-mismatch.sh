#!/usr/bin/env bash
# eviction-name-mismatch.sh — one question on the envtest harness (boundary review F12, 2026-11-15):
# does the API server admit an Eviction whose body names a different pod than the URL?
#
# K3 (platform/policies/k3-l2-approval.yaml) matches an eviction on request.object.metadata.name,
# the name in the BODY. The pod that is evicted is the one in the URL. If the API server admitted
# a body name that differs from the URL name, K3 would check one pod and another would go. This
# script shows that the API server refuses such a request (HTTP 400) before admission, so the two
# names K3 could see are always the same.
#
# Cases (the harness has no scheduler: the pods stay Pending, which an eviction does not mind):
#   control-dry-run   POST pods/<a>/eviction, body name <a>, dryRun All      admitted
#   mismatch          POST pods/<a>/eviction, body name <b>                  refused, 400
#   mismatch-dry-run  the same with dryRun All                               refused, 400
#   both-still-there  <a> and <b> exist after the three requests
#   control-real      POST pods/<a>/eviction, body name <a>                  admitted, <a> gone, <b> kept
#
# Needs a running harness (NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent envtest.sh up). It uses
# only the harness's admin kubeconfig, after `envtest.sh check`, and the harness's own kubectl.
# Names carry a per-run suffix. It never touches the k3s cluster and is never run by CI.
#
# Usage: NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent eviction-name-mismatch.sh
# Exit codes: 0 every case as expected; 1 otherwise; 2 usage or guard error.
set -euo pipefail

die() { echo "eviction-name-mismatch: $*" >&2; exit 2; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
harness=$here/envtest.sh
[[ $# == 0 ]] || die "usage: eviction-name-mismatch.sh"
[[ -z ${CI:-} && -z ${GITHUB_ACTIONS:-} ]] || die "refusing to run in CI; run by hand"
[[ ${KUBECONFIG-} == /nonexistent ]] || die "run with KUBECONFIG=/nonexistent (offline rule)"
[[ -n ${NEXUS_ENVTEST_DIR:-} ]] || die "set NEXUS_ENVTEST_DIR to the harness directory"
dir=$(realpath -m -- "$NEXUS_ENVTEST_DIR")
admin=$dir/admin.kubeconfig
"$harness" status > /dev/null || die "the harness is not up: run envtest.sh up first"
"$harness" check "$admin" > /dev/null || die "the harness guard rejected $admin"

k() { "$dir/bin/kubectl" --kubeconfig "$admin" "$@"; }

s=$(date +%s)$RANDOM
ns=evict-$s
a=sample-api-a-$s
b=sample-api-b-$s
failed=0
record() {  # record <case id> <ok 0|1> <detail>
  if [[ $2 == 1 ]]; then echo "PASS $1${3:+: $3}"; else echo "FAIL $1: $3"; failed=1; fi
}

pod() {  # pod <name>
  cat <<EOF
apiVersion: v1
kind: Pod
metadata: {name: $1, namespace: $ns}
spec:
  automountServiceAccountToken: false
  containers:
    - {name: pause, image: registry.k8s.io/pause:3.10}
EOF
}

# eviction <url pod> <body pod> [dry]: prints the API server's answer; exit code is kubectl's.
eviction() {
  local opts='{}'
  [[ ${3:-} == dry ]] && opts='{"dryRun":["All"]}'
  k create --raw "/api/v1/namespaces/$ns/pods/$1/eviction" -f - <<EOF
{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"$2","namespace":"$ns"},"deleteOptions":$opts}
EOF
}

exists() { k get pod "$1" -n "$ns" -o name > /dev/null 2>&1; }

k create namespace "$ns" > /dev/null
# no controller-manager on the harness: the default ServiceAccount is not made for us
k get serviceaccount default -n "$ns" > /dev/null 2>&1 || k create serviceaccount default -n "$ns" > /dev/null
pod "$a" | k create -f - > /dev/null
pod "$b" | k create -f - > /dev/null

out=$(eviction "$a" "$a" dry 2>&1) && rc=0 || rc=$?
record control-dry-run $(( rc == 0 )) "exit $rc $(tr '\n' ' ' <<< "$out" | cut -c1-160)"

for mode in "" dry; do
  id=mismatch${mode:+-dry-run}
  out=$(eviction "$a" "$b" $mode 2>&1) && rc=0 || rc=$?
  flat=$(tr '\n' ' ' <<< "$out" | cut -c1-240)
  if [[ $rc != 0 && $out == *"does not match"* && $out == *BadRequest* ]]; then
    record "$id" 1 "$flat"
  else
    record "$id" 0 "want a 400 naming the mismatch, got exit $rc: $flat"
  fi
done

if exists "$a" && exists "$b"; then record both-still-there 1 ""; else record both-still-there 0 "a pod is gone after refused requests"; fi

out=$(eviction "$a" "$a" 2>&1) && rc=0 || rc=$?
gone=0
for _ in $(seq 1 20); do exists "$a" || { gone=1; break; }; sleep 0.5; done
if [[ $rc == 0 && $gone == 1 ]] && exists "$b"; then
  record control-real 1 "the URL's pod is gone, the other is kept"
else
  record control-real 0 "exit $rc, a gone=$gone: $(tr '\n' ' ' <<< "$out" | cut -c1-160)"
fi

k delete namespace "$ns" --wait=false > /dev/null 2>&1 || true
exit $failed
