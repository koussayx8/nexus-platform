#!/usr/bin/env bash
# run-spike.sh — runs the Kopf status-persistence spike (spike.py) on the envtest harness and
# scores it (TASKS.md M1b-6 6b; spec §4 runtime settings, §7, §11 RBAC, §27 "Kopf status-based
# persistence and standalone mode").
#
# Setup, with the harness's admin kubeconfig: namespaces nexus-system, nexus-dev (label
# nexus.io/autonomy-level "0") and nexus-prod ("1"), the Incident CRD from platform/crds, and
# rbac.yaml (the staged M1b RBAC, nothing more). Kopf then runs as the operator identity:
# --standalone, --namespace nexus-system, progress and diff-base in status, event posting off,
# scanning disabled, no delete handlers, and the 5 s reconcile loop.
# Three Incidents, created by the admin:
#   A  nexus-dev, L0   Kopf run 1 gets SIGKILL (to its process group) 2 s into intake_stamp;
#                      run 2 resumes it
#   C  nexus-dev, L0   run 2, the race Incident: the loop reads it, the intake handler writes,
#                      then the loop writes with the resourceVersion it read
#   B  nexus-prod, L1  run 2: stays Detected until the 20 s timeout
#
# Pass criteria (plan M1b-6 6b: P1-P4; owner, 6b gate: P5, P6):
#   P1  handlers ran and progress landed in status: at the kill, status.kopf.progress records A's
#       intake_level as succeeded; at the end, A is Recorded / level_observe with the diff-base in
#       status.kopf
#   P2  zero patch or update calls by the operator identity on incidents outside the status
#       subresource (denied attempts count); A, B and C end with no finalizers and no annotations
#   P3  Kopf resumed A from status after the kill: intake_level ran once across both runs;
#       intake_stamp started in both runs and finished only in run 2
#   P4  every 403 is listed, from Kopf's logs and from the audit log. A 403 is a request outside
#       the staged set: it is reported, never granted
#   P5  no lost updates: the loop's first write on C got 409 and was re-read and retried, and C
#       ends with both effects (intake: autonomyLevel, timestamps.intake; loop: phase Recorded,
#       timestamps.detected and .terminal). Single writer: no Kopf status patch carries phase or
#       reason, and no loop write carries autonomyLevel, timestamps.intake or kopf
#   P6  the 20 s Detected timeout: B ends Escalated / evidence_error 20-26 s after creation (one
#       5 s loop interval plus 1 s of slack); A and C end Recorded
# Also reported: each Incident's creation-to-terminal time, and whether anything wrote to it after
# the loop's terminal write (§7: terminal Incidents are never modified).
#
# Needs: a running harness (NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent
# scripts/tests/envtest.sh up), jq, and Kopf installed from requirements.txt into a scratch venv.
# Usage:
#   NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent SPIKE_KOPF=<venv>/bin/kopf \
#     SPIKE_OUT=<dir outside the repository> run-spike.sh
# Output in SPIKE_OUT: kopf-run-{1,2}.log, a-at-kill.json, {a,b,c}-end.json and
# operator-audit.jsonl; the scored summary on stdout. Never run by CI.
# Exit codes: 0 P1-P3, P5 and P6 pass; 1 any of them fails; 2 usage or guard error.
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

# start_kopf <run> <intake_stamp sleep, s> <race Incident or "">: Kopf in its own process group;
# the group leader writes its PID (the PGID) and execs kopf.
start_kopf() {
  local i
  # shellcheck disable=SC2016  # the inner script expands its own positional arguments
  SPIKE_STEP_SLEEP_S=$2 SPIKE_RACE_INCIDENT=$3 NEXUS_SPIKE_KUBECONFIG=$opkc \
    NEXUS_ENVTEST_DIR=$dir KUBECONFIG=/nonexistent \
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

# wait_terminal <name> <seconds>: until the phase is Recorded or Escalated.
wait_terminal() {
  local i p
  for i in $(seq 1 "$2"); do
    p=$(phase_of "$1")
    [[ $p == Recorded || $p == Escalated ]] && { echo "$1: $p after ${i}s of waiting"; return 0; }
    sleep 1
  done
  echo "$1: not terminal after $2 s (phase $p)"
}

# create_incident <name> <target namespace>
create_incident() {
  k create -f - > /dev/null <<EOF
apiVersion: nexus.io/v1alpha1
kind: Incident
metadata:
  name: $1
  namespace: $NS
spec:
  source: {type: detector, alertname: NexusErrorRateAnomaly, fingerprint: 6b0c1f}
  target: {namespace: $2, kind: Deployment, name: sample-api}
  detectedAt: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
}

echo "== setup ($(date -u +%H:%M:%SZ))"
k apply --server-side -f - > /dev/null <<'EOF'
apiVersion: v1
kind: Namespace
metadata: {name: nexus-system}
---
apiVersion: v1
kind: Namespace
metadata: {name: nexus-dev, labels: {nexus.io/autonomy-level: "0"}}
---
apiVersion: v1
kind: Namespace
metadata: {name: nexus-prod, labels: {nexus.io/autonomy-level: "1"}}
EOF
k apply --server-side --force-conflicts -f "$crd" > /dev/null
k wait --for=condition=Established crd/incidents.nexus.io --timeout=30s > /dev/null
k apply --server-side -f "$here/rbac.yaml" > /dev/null
sleep 3   # the API server (re)initializes a new CRD's storage asynchronously
offset=$(wc -l < "$audit")
stamp=$(date -u +%H%M%S)
A=spike-a-$stamp B=spike-b-$stamp C=spike-c-$stamp
echo "kopf $("$SPIKE_KOPF" --version | awk '{print $NF}'); Incidents A=$A B=$B C=$C; audit offset $offset"

echo "== run 1: create A, SIGKILL 2 s into intake_stamp"
start_kopf 1 30 ""
wait_log 1 "Initial authentication has finished" 30 || echo "run 1: no authentication line after 30 s"
sleep 3
create_incident "$A" nexus-dev
killed=0
if wait_log 1 "SPIKE start intake_stamp $A " 60; then
  sleep 2
  stop_kopf 1 KILL
  killed=1
else
  echo "run 1: intake_stamp did not start within 60 s; phase $(phase_of "$A")"
  stop_kopf 1 TERM
fi
k get incident -n "$NS" "$A" -o json > "$out/a-at-kill.json"

echo "== run 2: resume A; then C (race); then B (L1, timeout)"
start_kopf 2 2 "$C"
wait_terminal "$A" 40
create_incident "$C" nexus-dev
wait_terminal "$C" 40
create_incident "$B" nexus-prod
wait_terminal "$B" 45
sleep 12
for x in a b c; do
  n=${x^^}
  k get incident -n "$NS" "${!n}" -o json > "$out/$x-end.json"
done
stop_kopf 2 TERM

tail -n +"$((offset + 1))" "$audit" \
  | jq -c --arg u "$OPERATOR_USER" 'select(.user.username == $u)' > "$out/operator-audit.jsonl"

echo "== operator requests in the audit log (count agent verb resource[/subresource] code)"
jq -r '"\(.userAgent // "-" | split("/")[0]) \(.verb) \(.objectRef.resource // .requestURI)\(if .objectRef.subresource then "/" + .objectRef.subresource else "" end) \(.responseStatus.code)"' \
  "$out/operator-audit.jsonl" | sort | uniq -c

echo "== results"
fails=()
verdict() {  # verdict <id> <ok 0|1> <detail>
  if [[ $2 == 1 ]]; then echo "PASS $1: $3"; else echo "FAIL $1: $3"; fails+=("$1"); fi
}
# c <fixed string> <run>...: matching lines summed over the runs' Kopf logs.
c() {
  local s=$1 r n=0
  shift
  for r in "$@"; do n=$((n + $(grep -cF -- "$s" "$out/kopf-run-$r.log" || true))); done
  echo "$n"
}
# kopf_logs: both runs' logs without the leading [timestamp], so its digits never match "403".
kopf_logs() { sed -E 's/^\[[^]]*\] //' "$out"/kopf-run-{1,2}.log; }

at_kill=$(jq -c '{phase: .status.phase, progress: (.status.kopf.progress // {} | with_entries(.value |= {success, retries}))}' "$out/a-at-kill.json")
a_end=$(jq -c '{phase: .status.phase, reason: .status.reason, diffbase: (.status.kopf["last-handled-configuration"] != null), progress: (.status.kopf.progress // null)}' "$out/a-end.json")
ok=0
[[ $killed == 1 ]] \
  && jq -e '.status.kopf.progress.intake_level.success == true' "$out/a-at-kill.json" > /dev/null \
  && jq -e '.status.phase == "Recorded" and .status.reason == "level_observe" and .status.kopf["last-handled-configuration"] != null' "$out/a-end.json" > /dev/null \
  && ok=1
verdict P1 "$ok" "A at kill $at_kill; A end $a_end"

writes=$(jq -s '[.[] | select(.objectRef.resource == "incidents" and (.objectRef.subresource // "") != "status" and (.verb == "patch" or .verb == "update"))] | length' "$out/operator-audit.jsonl")
meta=$(jq -s -c 'map({(.metadata.name): {finalizers: (.metadata.finalizers // []), annotations: (.metadata.annotations // {} | keys)}}) | add' "$out"/{a,b,c}-end.json)
ok=0
[[ $writes == 0 ]] && jq -s -e 'all(.[]; (.metadata.finalizers // [] | length) == 0 and (.metadata.annotations // {} | length) == 0)' "$out"/{a,b,c}-end.json > /dev/null && ok=1
verdict P2 "$ok" "non-status patch/update calls on incidents: $writes; end metadata $meta"

d1=$(c "SPIKE done intake_level $A " 1 2)
s1=$(c "SPIKE start intake_stamp $A " 1); s2=$(c "SPIKE start intake_stamp $A " 2)
f1=$(c "SPIKE done intake_stamp $A" 1); f2=$(c "SPIKE done intake_stamp $A" 2)
ok=0
[[ $killed == 1 && $d1 == 1 && $s1 == 1 && $s2 == 1 && $f1 == 0 && $f2 == 1 ]] && ok=1
verdict P3 "$ok" "A: intake_level done x$d1; intake_stamp start run1 x$s1 run2 x$s2, done run1 x$f1 run2 x$f2; killed=$killed"

n403=$(jq -s '[.[] | select(.responseStatus.code == 403)] | length' "$out/operator-audit.jsonl")
echo "P4 403s in the audit log: $n403"
jq -r 'select(.responseStatus.code == 403) | "  \(.verb) \(.requestURI)"' "$out/operator-audit.jsonl" | sort | uniq -c
echo "P4 403 or Forbidden lines in Kopf's logs: $(kopf_logs | grep -ciE '\b403\b|forbidden' || true)"
kopf_logs | grep -iE '\b403\b|forbidden' | cut -c1-240 | sort | uniq -c || true

c409=$(jq -s --arg n "$C" '[.[] | select(.objectRef.name == $n and .objectRef.subresource == "status" and .verb == "patch" and .responseStatus.code == 409)] | length' "$out/operator-audit.jsonl")
cconf=$(c "SPIKE loop conflict $C " 2)
c_end=$(jq -c '{autonomyLevel: .status.autonomyLevel, intake: .status.timestamps.intake, phase: .status.phase, reason: .status.reason, detected: .status.timestamps.detected, terminal: .status.timestamps.terminal}' "$out/c-end.json")
kopf_bad=$(kopf_logs | grep -F 'Merge-patching the status with:' | grep -cE "'(phase|reason)':" || true)
loop_bad=$(kopf_logs | grep -F 'SPIKE loop write ' | grep -cE '"(autonomyLevel|intake|kopf)"' || true)
kopf_n=$(kopf_logs | grep -cF 'Merge-patching the status with:' || true)
loop_n=$(kopf_logs | grep -cF 'SPIKE loop write ' || true)
ok=0
[[ $c409 -ge 1 && $cconf -ge 1 && $kopf_bad == 0 && $loop_bad == 0 ]] \
  && jq -e '.status.autonomyLevel == 0 and .status.timestamps.intake != null and .status.phase == "Recorded" and .status.reason == "level_observe" and .status.timestamps.detected != null and .status.timestamps.terminal != null' "$out/c-end.json" > /dev/null \
  && ok=1
verdict P5 "$ok" "C: 409s in audit x$c409, loop conflicts logged x$cconf; C end $c_end; Kopf status patches x$kopf_n (with phase/reason: $kopf_bad); loop writes x$loop_n (with intake/kopf fields: $loop_bad)"

b_dt=$(jq -r '(.status.timestamps.terminal // empty | fromdate) - (.metadata.creationTimestamp | fromdate)' "$out/b-end.json")
b_end=$(jq -c '{phase: .status.phase, reason: .status.reason, autonomyLevel: .status.autonomyLevel}' "$out/b-end.json")
ok=0
[[ -n $b_dt ]] && ((b_dt >= 20 && b_dt <= 26)) \
  && jq -e '.status.phase == "Escalated" and .status.reason == "evidence_error"' "$out/b-end.json" > /dev/null \
  && jq -s -e 'all(.[]; .status.phase == "Recorded")' "$out"/{a,c}-end.json > /dev/null \
  && ok=1
verdict P6 "$ok" "B creation->Escalated ${b_dt:-?} s (want 20-26); B end $b_end; A and C $(jq -s -c 'map(.status.phase)' "$out"/{a,c}-end.json)"

echo "== per Incident: creation -> terminal, and writes after the loop's terminal write"
for x in a b c; do
  n=${x^^}
  name=${!n}
  dt=$(jq -r '((.status.timestamps.terminal // empty | fromdate) - (.metadata.creationTimestamp | fromdate)) // "-"' "$out/$x-end.json")
  final_rv=$(jq -r '.metadata.resourceVersion' "$out/$x-end.json")
  terminal_rv=$(kopf_logs | grep -F "SPIKE loop write $name " | grep -E '"phase": "(Recorded|Escalated)"' \
    | grep -oE -- '-> [0-9]+' | awk '{print $2}' | tail -1 || true)
  echo "$n $name: creation -> terminal ${dt} s; final resourceVersion $final_rv, loop's terminal write -> ${terminal_rv:-none}$([[ $final_rv == "$terminal_rv" ]] && echo " (nothing wrote after it)")"
done
echo "loop ticks: run 1 x$(c "SPIKE loop tick " 1), run 2 x$(c "SPIKE loop tick " 2); loop stopped cleanly in run 2: $(c "SPIKE loop stopped" 2)"

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo "P1-P3, P5 and P6 pass; P4 lists $n403 403s"
