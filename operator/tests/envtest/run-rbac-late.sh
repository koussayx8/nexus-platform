#!/usr/bin/env bash
# run-rbac-late.sh — the operator started before its RBAC exists waits for it without restarting
# (M1b-8 PR B; ADR-025). In the cluster, the `nexus` Application (the operator) and the `platform`
# Application (platform/rbac) sync independently, so the pod can start first.
#
# Setup, as admin: namespaces, the Incident CRD, the two ConfigMaps, and NO RBAC. Kopf then runs the
# operator (nexus_operator.main via envtest_hooks.py) as the operator identity with a liveness
# endpoint. After WAIT_S seconds the admin applies platform/rbac/nexus-operator.yaml.
#
# Pass criteria:
#   R1  while RBAC is missing, the startup handler's only requests that fail are 403s on
#       get configmaps/nexus-operator-config, at least 2 of them (Kopf retries it every 60 s)
#   R2  nothing else runs before startup succeeds: no list or watch of incidents, no other 403
#   R3  /healthz is not served during the wait (Kopf starts it after startup), so in the cluster
#       the startupProbe, not the livenessProbe, covers the wait
#   R4  startup succeeds within 65 s of the RBAC (the 60 s retry plus margin): the "config
#       alertmanagerURL=" log line
#   R5  then /healthz answers 200, and the Kopf process never exited (same process group, alive)
#   R6  no 403 after startup succeeded
#
# Usage, with the harness up (as for run-envtest.sh):
#   NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent OPERATOR_VENV=<venv> \
#     TEST_OUT=<dir outside the repository> [WAIT_S=75] operator/tests/envtest/run-rbac-late.sh
# Needs a fresh harness (`envtest.sh up`): it refuses to run if the operator's Role already exists.
# Exit codes: 0 R1-R6 pass; 1 any fails; 2 usage or guard error. Never run by CI.
set -euo pipefail
umask 077

die() { echo "run-rbac-late: $*" >&2; exit 2; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd "$here/../../.." && pwd -P)
harness=$root/scripts/tests/envtest.sh
crd=$root/platform/crds/incidents.nexus.io.yaml
rbac=$root/platform/rbac/nexus-operator.yaml
OPERATOR_USER=system:serviceaccount:nexus-system:nexus-operator
NS=nexus-system
WAIT_S=${WAIT_S:-75}

[[ -z ${CI:-} && -z ${GITHUB_ACTIONS:-} ]] || die "refusing to run in CI; run by hand"
[[ ${KUBECONFIG-} == /nonexistent ]] || die "run with KUBECONFIG=/nonexistent (offline rule)"
[[ -n ${NEXUS_ENVTEST_DIR:-} ]] || die "set NEXUS_ENVTEST_DIR to the harness directory"
[[ -x ${OPERATOR_VENV:-}/bin/kopf ]] || die "set OPERATOR_VENV to a scratch venv with requirements.txt"
[[ -n ${TEST_OUT:-} ]] || die "set TEST_OUT to a directory outside the repository"
{ [[ $WAIT_S =~ ^[0-9]+$ ]] && ((WAIT_S >= 61)); } || die "WAIT_S must be at least 61 (two startup attempts)"
[[ ! -e /var/run/secrets/kubernetes.io/serviceaccount/token ]] \
  || die "an in-cluster ServiceAccount token exists here; this test runs only off-cluster"
command -v jq > /dev/null || die "jq must be on PATH"
command -v curl > /dev/null || die "curl must be on PATH"
kopf=$OPERATOR_VENV/bin/kopf
py=$OPERATOR_VENV/bin/python
dir=$(realpath -m -- "$NEXUS_ENVTEST_DIR")
out=$(realpath -m -- "$TEST_OUT")
case $out/ in "$root"/*) die "TEST_OUT is inside the repository: $out" ;; esac
admin=$dir/admin.kubeconfig
opkc=$dir/operator.kubeconfig
"$harness" status > /dev/null || die "the harness is not up: run envtest.sh up first"
"$harness" check "$admin" > /dev/null || die "the harness guard rejected $admin"
"$harness" check "$opkc" > /dev/null || die "the harness guard rejected $opkc"
audit=$(cat "$dir/run/current")/audit.log
mkdir -p "$out"
[[ -z $(ls -A "$out") ]] || die "TEST_OUT is not empty: $out"

k() { "$dir/bin/kubectl" --kubeconfig "$admin" "$@"; }
! k get role nexus-operator -n "$NS" > /dev/null 2>&1 \
  || die "the operator's Role already exists: start from a fresh harness (envtest.sh down; up)"
live_port=$("$py" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$live_port/healthz" || true; }
log=$out/kopf.log

echo "== setup, without RBAC ($(date -u +%H:%M:%SZ))"
k apply --server-side -f - > /dev/null <<'EOF'
apiVersion: v1
kind: Namespace
metadata: {name: nexus-system}
---
apiVersion: v1
kind: Namespace
metadata: {name: nexus-dev, labels: {nexus.io/autonomy-level: "0"}}
EOF
k apply --server-side --force-conflicts -f "$crd" > /dev/null
k wait --for=condition=Established crd/incidents.nexus.io --timeout=30s > /dev/null
k apply --server-side --force-conflicts -f - > /dev/null <<EOF
apiVersion: v1
kind: ConfigMap
metadata: {name: nexus-killswitch, namespace: $NS}
data: {state: active}
---
apiVersion: v1
kind: ConfigMap
metadata: {name: nexus-operator-config, namespace: $NS}
data: {alertmanagerURL: "http://127.0.0.1:9"}
EOF
sleep 3
offset=$(wc -l < "$audit")

echo "== start the operator; RBAC follows in ${WAIT_S} s"
# shellcheck disable=SC2016  # the inner script expands its own positional arguments
setsid sh -c 'f=$1; shift; echo $$ > "$f"; exec "$@"' sh "$out/kopf.pgid" \
  env PYTHONPATH="$root/operator" NEXUS_TEST_KUBECONFIG="$opkc" NEXUS_ENVTEST_DIR="$dir" \
  KUBECONFIG=/nonexistent "$kopf" run --standalone --namespace "$NS" --verbose \
  "--liveness=http://127.0.0.1:$live_port/healthz" "$here/envtest_hooks.py" \
  > "$log" 2>&1 < /dev/null &
for _ in $(seq 1 20); do [[ -s $out/kopf.pgid ]] && break; sleep 0.5; done
pgid=$(cat "$out/kopf.pgid")
[[ $pgid =~ ^[0-9]+$ ]] || die "no PGID"
started=$(date +%s)
during=()
while (( $(date +%s) - started < WAIT_S )); do
  during+=("$(health)")
  kill -0 -- "-$pgid" 2>/dev/null || break
  sleep 5
done
alive_before=$(kill -0 -- "-$pgid" 2>/dev/null && echo yes || echo no)
t_rbac=$(date -u +%s)
k apply --server-side -f "$rbac" > /dev/null
echo "RBAC applied at $(date -u -d "@$t_rbac" +%H:%M:%SZ), $((t_rbac - started)) s after start"
t_ok=""
for _ in $(seq 1 80); do
  if grep -qF "config alertmanagerURL=" "$log"; then t_ok=$(date -u +%s); break; fi
  sleep 1
done
sleep 12
after=$(health)
alive_after=$(kill -0 -- "-$pgid" 2>/dev/null && echo yes || echo no)
kill -TERM -- "-$pgid" 2>/dev/null || true
for _ in $(seq 1 20); do kill -0 -- "-$pgid" 2>/dev/null || break; sleep 0.5; done
kill -KILL -- "-$pgid" 2>/dev/null || true

tail -n +"$((offset + 1))" "$audit" \
  | jq -c --arg u "$OPERATOR_USER" 'select(.user.username == $u)' > "$out/operator-audit.jsonl"
ts() { jq -r '.stageTimestamp | sub("\\.[0-9]+Z$"; "Z") | fromdate'; }

echo "== operator requests (count agent verb resource/name code)"
jq -r '"\(.userAgent // "-" | split("/")[0] | split(" ")[0]) \(.verb) \(.objectRef.resource // .requestURI)/\(.objectRef.name // "") \(.responseStatus.code)"' \
  "$out/operator-audit.jsonl" | sort | uniq -c

echo "== results"
fails=()
verdict() { if [[ $2 == 1 ]]; then echo "PASS $1: $3"; else echo "FAIL $1: $3"; fails+=("$1"); fi; }

f403=$(jq -s '[.[] | select(.responseStatus.code == 403)]' "$out/operator-audit.jsonl")
n403=$(jq 'length' <<<"$f403")
n403_cm=$(jq '[.[] | select(.verb == "get" and .objectRef.resource == "configmaps" and .objectRef.name == "nexus-operator-config")] | length' <<<"$f403")
retries=$(grep -cF "will try again in 60 seconds" "$log" || true)
verdict R1 "$([[ $n403_cm -ge 2 && $n403_cm == "$n403" ]] && echo 1 || echo 0)" "403s: $n403, all get configmaps/nexus-operator-config: $n403_cm; Kopf 'will try again in 60 seconds' x$retries"

early=$(jq -s --argjson t "$t_rbac" '[.[] | select(.objectRef.resource == "incidents" and (.stageTimestamp | sub("\\.[0-9]+Z$"; "Z") | fromdate) < $t)] | length' "$out/operator-audit.jsonl")
verdict R2 "$([[ $early == 0 ]] && echo 1 || echo 0)" "incident requests before the RBAC: $early"

served=$(printf '%s\n' "${during[@]}" | grep -c '^200$' || true)
verdict R3 "$([[ $served == 0 ]] && echo 1 || echo 0)" "/healthz during the wait: ${during[*]} (000 = not served)"

dt=$([[ -n $t_ok ]] && echo $((t_ok - t_rbac)) || echo "")
verdict R4 "$([[ -n $dt ]] && ((dt <= 65)) && echo 1 || echo 0)" "startup succeeded ${dt:-never} s after the RBAC"

verdict R5 "$([[ $after == 200 && $alive_before == yes && $alive_after == yes ]] && echo 1 || echo 0)" "/healthz after: HTTP $after; Kopf process alive before the RBAC: $alive_before, after: $alive_after (PGID $pgid)"

late=$(jq -s --argjson t "${t_ok:-0}" '[.[] | select(.responseStatus.code == 403 and (.stageTimestamp | sub("\\.[0-9]+Z$"; "Z") | fromdate) > $t)] | length' "$out/operator-audit.jsonl")
verdict R6 "$([[ -n $t_ok && $late == 0 ]] && echo 1 || echo 0)" "403s after startup succeeded: $late"

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo "R1-R6 pass"
