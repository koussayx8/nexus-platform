#!/usr/bin/env bash
# run-spike.sh — runs the Kopf status-persistence spike (spike.py) on the envtest harness and
# scores it (TASKS.md M1b-6 6b; spec §4 runtime settings, §11 RBAC, §27 "Kopf status-based
# persistence and standalone mode").
#
# Setup, with the harness's admin kubeconfig: namespace nexus-system, the Incident CRD from
# platform/crds, and rbac.yaml (the staged M1b RBAC, nothing more). Kopf then runs as the operator
# identity: --standalone, --namespace nexus-system, progress and diff-base in status, event posting
# off, no delete handlers, a 5 s timer.
# Sequence: Kopf run 1; the admin creates one Incident; once record_observed has started, SIGKILL
# to Kopf's process group (a kill mid-handler); status snapshot; Kopf run 2; wait for Recorded;
# wait 12 s (two timer intervals); snapshot; stop Kopf.
#
# Pass criteria (plan M1b-6 6b):
#   P1  handlers ran and progress landed in status: at the kill, status.kopf.progress records
#       record_detected as succeeded; at the end, phase Recorded, reason level_observe, and the
#       diff-base in status.kopf
#   P2  the audit log shows zero patch or update calls by the operator identity on incidents
#       outside the status subresource (denied attempts count), and the Incident ends with no
#       finalizers and no annotations
#   P3  Kopf resumed from status after the kill: record_detected ran once across both runs;
#       record_observed started in both runs and finished only in run 2
#   P4  every 403 is listed, from Kopf's logs and from the audit log. A 403 is a request outside
#       the staged set: it is reported, never granted
#
# Needs: a running harness (NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent
# scripts/tests/envtest.sh up), jq, and Kopf installed from requirements.txt into a scratch venv.
# Usage:
#   NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent SPIKE_KOPF=<venv>/bin/kopf \
#     SPIKE_OUT=<dir outside the repository> run-spike.sh
# Output: kopf-run-{1,2}.log, incident-at-kill.json, incident-end.json and operator-audit.jsonl
# in SPIKE_OUT; the scored summary on stdout. Never run by CI.
# Exit codes: 0 P1-P3 pass; 1 any of P1-P3 fails; 2 usage or guard error.
set -euo pipefail
umask 077

die() { echo "run-spike: $*" >&2; exit 2; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd "$here/../../.." && pwd -P)
harness=$root/scripts/tests/envtest.sh
crd=$root/platform/crds/incidents.nexus.io.yaml
OPERATOR_USER=system:serviceaccount:nexus-system:nexus-operator
NS=nexus-system

[[ -z ${CI:-} && -z ${GITHUB_ACTIONS:-} ]] || die "refusing to run in CI; run by hand"
[[ ${KUBECONFIG-} == /nonexistent ]] || die "run with KUBECONFIG=/nonexistent (offline rule)"
[[ -n ${NEXUS_ENVTEST_DIR:-} ]] || die "set NEXUS_ENVTEST_DIR to the harness directory"
[[ -x ${SPIKE_KOPF:-} ]] || die "set SPIKE_KOPF to the kopf executable of a scratch venv"
[[ -n ${SPIKE_OUT:-} ]] || die "set SPIKE_OUT to a directory outside the repository"
command -v jq > /dev/null || die "jq must be on PATH"
dir=$(realpath -m -- "$NEXUS_ENVTEST_DIR")
out=$(realpath -m -- "$SPIKE_OUT")
case $out/ in "$root"/*) die "SPIKE_OUT is inside the repository: $out" ;; esac
admin=$dir/admin.kubeconfig
opkc=$dir/operator.kubeconfig
"$harness" status > /dev/null || die "the harness is not up: run envtest.sh up first"
"$harness" check "$admin" > /dev/null || die "the harness guard rejected $admin"
"$harness" check "$opkc" > /dev/null || die "the harness guard rejected $opkc"
audit=$(cat "$dir/run/current")/audit.log
mkdir -p "$out"
[[ -z $(ls -A "$out") ]] || die "SPIKE_OUT is not empty: $out"

k() { "$dir/bin/kubectl" --kubeconfig "$admin" "$@"; }

# start_kopf <run> <record_observed sleep, s>: Kopf in its own process group; the group leader
# writes its PID (the PGID) and execs kopf.
start_kopf() {
  local i
  # shellcheck disable=SC2016  # the inner script expands its own positional arguments
  SPIKE_STEP_SLEEP_S=$2 NEXUS_SPIKE_KUBECONFIG=$opkc NEXUS_ENVTEST_DIR=$dir KUBECONFIG=/nonexistent \
    setsid sh -c 'echo $$ > "$1"; exec "$2" run --standalone --namespace nexus-system --verbose "$3"' \
    kopf "$out/kopf-run-$1.pgid" "$SPIKE_KOPF" "$here/spike.py" \
    > "$out/kopf-run-$1.log" 2>&1 < /dev/null &
  for i in $(seq 1 20); do [[ -s $out/kopf-run-$1.pgid ]] && return 0; sleep 0.5; done
  die "Kopf run $1 wrote no PGID"
}

# stop_kopf <run> <TERM|KILL>: signal the group, wait up to 20 s, then SIGKILL as a last resort.
stop_kopf() {
  local pgid i
  pgid=$(cat "$out/kopf-run-$1.pgid")
  [[ $pgid =~ ^[0-9]+$ ]] || die "bad PGID for run $1"
  kill -"$2" -- "-$pgid" 2>/dev/null || true
  for i in $(seq 1 20); do
    kill -0 -- "-$pgid" 2>/dev/null || { echo "kopf run $1: stopped by SIG$2 after ${i}x0.5 s"; return 0; }
    sleep 0.5
  done
  kill -KILL -- "-$pgid" 2>/dev/null || true
  sleep 1
  ! kill -0 -- "-$pgid" 2>/dev/null || die "Kopf run $1 (PGID $pgid) survived SIGKILL"
  echo "kopf run $1: SIG$2 was not enough; sent SIGKILL"
}

# wait_log <run> <fixed string> <seconds>
wait_log() {
  local i
  for i in $(seq 1 "$3"); do
    grep -qF -- "$2" "$out/kopf-run-$1.log" && return 0
    sleep 1
  done
  return 1
}

# phase_of <name>: the Incident's status.phase, or "-".
phase_of() { k get incident -n "$NS" "$1" -o json | jq -r '.status.phase // "-"'; }

echo "== setup ($(date -u +%H:%M:%SZ))"
k get namespace "$NS" > /dev/null 2>&1 || k create namespace "$NS" > /dev/null
k apply --server-side --force-conflicts -f "$crd" > /dev/null
k wait --for=condition=Established crd/incidents.nexus.io --timeout=30s > /dev/null
k apply --server-side -f "$here/rbac.yaml" > /dev/null
offset=$(wc -l < "$audit")
name=spike-$(date -u +%H%M%S)
echo "kopf $("$SPIKE_KOPF" --version | awk '{print $NF}'); Incident $NS/$name; audit offset $offset"

echo "== run 1: create the Incident, SIGKILL once record_observed has started"
start_kopf 1 30
wait_log 1 "Initial authentication has finished" 30 || echo "run 1: no authentication line after 30 s"
sleep 3
k create -f - > /dev/null <<EOF
apiVersion: nexus.io/v1alpha1
kind: Incident
metadata:
  name: $name
  namespace: $NS
spec:
  source: {type: detector, alertname: NexusErrorRateAnomaly, fingerprint: 6b0c1f}
  target: {namespace: nexus-dev, kind: Deployment, name: sample-api}
  detectedAt: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
killed=0
if wait_log 1 "SPIKE start record_observed $name" 60; then
  sleep 2
  stop_kopf 1 KILL
  killed=1
else
  echo "run 1: record_observed did not start within 60 s; phase $(phase_of "$name")"
  stop_kopf 1 TERM
fi
k get incident -n "$NS" "$name" -o json > "$out/incident-at-kill.json"

echo "== run 2: resume"
start_kopf 2 2
for _ in $(seq 1 60); do [[ $(phase_of "$name") == Recorded ]] && break; sleep 1; done
sleep 12
k get incident -n "$NS" "$name" -o json > "$out/incident-end.json"
stop_kopf 2 TERM

tail -n +"$((offset + 1))" "$audit" \
  | jq -c --arg u "$OPERATOR_USER" 'select(.user.username == $u)' > "$out/operator-audit.jsonl"

echo "== operator requests in the audit log (count verb resource[/subresource] namespace code)"
jq -r '"\(.verb) \(.objectRef.resource // .requestURI)\(if .objectRef.subresource then "/" + .objectRef.subresource else "" end) \(.objectRef.namespace // "-") \(.responseStatus.code)"' \
  "$out/operator-audit.jsonl" | sort | uniq -c

echo "== results"
fails=()
verdict() {  # verdict <id> <ok 0|1> <detail>
  if [[ $2 == 1 ]]; then echo "PASS $1: $3"; else echo "FAIL $1: $3"; fails+=("$1"); fi
}
at_kill=$(jq -c '{phase: .status.phase, progress: (.status.kopf.progress // {} | with_entries(.value |= {success, failure, retries}))}' "$out/incident-at-kill.json")
end=$(jq -c '{phase: .status.phase, reason: .status.reason, diffbase: (.status.kopf["last-handled-configuration"] != null), progress: (.status.kopf.progress // null)}' "$out/incident-end.json")
ok=0
[[ $killed == 1 ]] \
  && jq -e '.status.kopf.progress.record_detected.success == true' "$out/incident-at-kill.json" > /dev/null \
  && jq -e '.status.phase == "Recorded" and .status.reason == "level_observe" and .status.kopf["last-handled-configuration"] != null' "$out/incident-end.json" > /dev/null \
  && ok=1
verdict P1 "$ok" "at kill $at_kill; end $end"

writes=$(jq -s '[.[] | select(.objectRef.resource == "incidents" and (.objectRef.subresource // "") != "status" and (.verb == "patch" or .verb == "update"))] | length' "$out/operator-audit.jsonl")
meta=$(jq -c '{finalizers: (.metadata.finalizers // []), annotations: (.metadata.annotations // {} | keys)}' "$out/incident-end.json")
ok=0
[[ $writes == 0 ]] && jq -e '(.metadata.finalizers // [] | length) == 0 and (.metadata.annotations // {} | length) == 0' "$out/incident-end.json" > /dev/null && ok=1
verdict P2 "$ok" "non-status patch/update calls on incidents: $writes; end metadata $meta"

# c <fixed string> <run>...: matching lines summed over the runs' Kopf logs.
c() {
  local s=$1 r n=0
  shift
  for r in "$@"; do n=$((n + $(grep -cF -- "$s" "$out/kopf-run-$r.log" || true))); done
  echo "$n"
}
# kopf_logs: both runs' logs without the leading [timestamp], so its digits never match "403".
kopf_logs() { sed -E 's/^\[[^]]*\] //' "$out"/kopf-run-{1,2}.log; }
d1=$(c "SPIKE done record_detected $name" 1 2)
s1=$(c "SPIKE start record_observed $name" 1); s2=$(c "SPIKE start record_observed $name" 2)
f1=$(c "SPIKE done record_observed $name" 1); f2=$(c "SPIKE done record_observed $name" 2)
ok=0
[[ $killed == 1 && $d1 == 1 && $s1 == 1 && $s2 == 1 && $f1 == 0 && $f2 == 1 ]] && ok=1
verdict P3 "$ok" "record_detected done x$d1; record_observed start run1 x$s1 run2 x$s2, done run1 x$f1 run2 x$f2; killed=$killed"

n403=$(jq -s '[.[] | select(.responseStatus.code == 403)] | length' "$out/operator-audit.jsonl")
echo "P4 403s in the audit log: $n403"
jq -r 'select(.responseStatus.code == 403) | "  \(.verb) \(.requestURI)"' "$out/operator-audit.jsonl" | sort | uniq -c
echo "P4 403 or Forbidden lines in Kopf's logs: $(kopf_logs | grep -ciE '\b403\b|forbidden' || true)"
kopf_logs | grep -iE '\b403\b|forbidden' | cut -c1-240 | sort | uniq -c || true
echo "timer ticks: run 1 x$(c "SPIKE tick reconcile_tick $name" 1), run 2 x$(c "SPIKE tick reconcile_tick $name" 2)"

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo "P1-P3 pass"
