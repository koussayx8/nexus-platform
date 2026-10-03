#!/usr/bin/env bash
# run-envtest.sh — the NEXUS Operator's integration test on the envtest harness (M1b-8; plan
# M1b-8 rev 3; ADR-023, ADR-025). It replaces the 6b spike's run-spike.sh.
#
# Setup, with the harness's admin kubeconfig: namespaces nexus-system, nexus-dev (label
# nexus.io/autonomy-level "0") and nexus-prod ("1"); the Incident CRD (platform/crds); the staged
# RBAC (platform/rbac, nothing more); ConfigMaps nexus-killswitch (state: active) and
# nexus-operator-config (the frozen timers, alertmanagerURL = a local fake Alertmanager). Kopf then
# runs the operator (nexus_operator.main plus tests/envtest/envtest_hooks.py) as the operator
# identity: --standalone, --namespace nexus-system, status persistence, no events or finalizers.
#
# Run 1  A  admin-created, nexus-dev (L0): SIGKILL to Kopf's process group 2 s into test_hold
# Run 2  A  resumed from status;  C  admin-created, nexus-dev (L0), the P5 race;
#        B  admin-created, nexus-prod (L1), the Detected timeout;
#        E1 alert on nexus-dev, startsAt with nanoseconds -> one Incident, Recorded; still firing
#           for 3 more polls -> no second Incident
# Run 3  (a restart, same alerts) -> no duplicate of E1; the liveness endpoint answers 200;
#        kill switch set to halted -> logged, and nothing changes:
#        E2 same fingerprint as E1, new startsAt -> a second Incident;
#        S  smoke alert (nexus_smoke="true") -> an Incident labelled nexus.io/smoke=true;
#        Y, Z two alerts on nexus-prod -> Y creates, Z is absorbed and logged; once Y is
#           Escalated, Z gets its own Incident (owner, rev 3);
#        X  ignored alerts: Watchdog (no nexus_target), a target outside C3, a target that
#           differs from the namespace label -> no Incident
#
# Pass criteria:
#   P1  progress landed in status: at the kill, A's intake_level is recorded as succeeded; A ends
#       Recorded / level_observe with the diff-base in status.kopf
#   P2  zero patch or update calls by the operator identity on incidents outside /status (denied
#       attempts count), zero deletes; every Incident ends with no finalizers and no annotations
#   P3  Kopf resumed A: intake_level ran once over both runs; test_hold started in runs 1 and 2
#       and finished only in run 2
#   P4  every 403 is listed (audit log and Kopf logs). Reported, never granted
#   P5  no lost updates: the loop's first write on C got 409, was re-read and retried; C ends with
#       both effects. Single writer: no Kopf status patch carries phase or reason, no loop write
#       carries autonomyLevel or kopf
#   P6  B and Y end Escalated / evidence_error 20-28 s after creation (detectedTimeoutSeconds 20
#       + reconcileSeconds 5 + margin 3, owner); A, C, E1, E2 and S end Recorded. With
#       P6_MODE=order (set when CI=true), only the order is checked: Detected, then Escalated, at
#       or after 20 s (owner, point 3)
#   E   episodes: E1 one Incident across 3 polls after Recorded and across the restart; E2 a second;
#       spec.detectedAt equals the alert's startsAt; the fingerprint label on every poller Incident
#   S   smoke: only S's Incident carries nexus.io/smoke=true
#   AB  absorb: Z logged as absorbed into Y's Incident with alertname, fingerprint, startsAt; Z's
#       own Incident created only after Y's turned terminal
#   X   no Incident for the ignored alerts; exactly the expected set of Incidents exists
#   T   no write after the loop's terminal write, on every Incident
#   L   liveness: GET /healthz answers 200 in run 3
#   K   the kill switch change is logged and changes no decision (E2, S, Y, Z as above)
#
# Needs: a running harness (NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent
# scripts/tests/envtest.sh up), jq, curl, and the operator's requirements.txt in a scratch venv.
# Usage:
#   NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent OPERATOR_VENV=<venv> \
#     TEST_OUT=<dir outside the repository> operator/tests/envtest/run-envtest.sh
# Output in TEST_OUT: kopf-run-{1,2,3}.log, fake-am.log, a-at-kill.json, end/*.json and
# operator-audit.jsonl; the scored summary on stdout. Never run by CI (the harness refuses).
# Exit codes: 0 every criterion passes; 1 any fails; 2 usage or guard error.
set -euo pipefail
umask 077

die() { echo "run-envtest: $*" >&2; exit 2; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd "$here/../../.." && pwd -P)
harness=$root/scripts/tests/envtest.sh
crd=$root/platform/crds/incidents.nexus.io.yaml
rbac=$root/platform/rbac/nexus-operator.yaml
OPERATOR_USER=system:serviceaccount:nexus-system:nexus-operator
NS=nexus-system
P6_LO=20 P6_HI=28
p6_mode=${P6_MODE:-window}
[[ ${CI:-} == true ]] && p6_mode=order

# envtest.sh refuses CI too; wiring this into CI means lifting both guards (P6 already switches).
[[ -z ${CI:-} && -z ${GITHUB_ACTIONS:-} ]] || die "refusing to run in CI; run by hand"
[[ ${KUBECONFIG-} == /nonexistent ]] || die "run with KUBECONFIG=/nonexistent (offline rule)"
[[ -n ${NEXUS_ENVTEST_DIR:-} ]] || die "set NEXUS_ENVTEST_DIR to the harness directory"
[[ -x ${OPERATOR_VENV:-}/bin/kopf ]] || die "set OPERATOR_VENV to a scratch venv with requirements.txt"
[[ -n ${TEST_OUT:-} ]] || die "set TEST_OUT to a directory outside the repository"
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
mkdir "$out/end"

k() { "$dir/bin/kubectl" --kubeconfig "$admin" "$@"; }

# free_port: a free TCP port on 127.0.0.1 (for the liveness endpoint).
free_port() { "$py" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

# start_group <log> <pgid file> <command...>: in its own process group; the leader writes its PID.
start_group() {
  local log=$1 pgidf=$2 i
  shift 2
  # shellcheck disable=SC2016  # the inner script expands its own positional arguments
  setsid sh -c 'f=$1; shift; echo $$ > "$f"; exec "$@"' sh "$pgidf" "$@" > "$log" 2>&1 < /dev/null &
  for i in $(seq 1 20); do [[ -s $pgidf ]] && return 0; sleep 0.5; done
  die "no PGID in $pgidf"
}

# stop_group <pgid file> <TERM|KILL> <label>: signal the group, wait up to 20 s, then SIGKILL.
stop_group() {
  local pgid i
  pgid=$(cat "$1")
  [[ $pgid =~ ^[0-9]+$ ]] || die "bad PGID in $1"
  kill -"$2" -- "-$pgid" 2>/dev/null || true
  for i in $(seq 1 20); do
    kill -0 -- "-$pgid" 2>/dev/null || { echo "$3: stopped by SIG$2 after ${i}x0.5 s"; return 0; }
    sleep 0.5
  done
  kill -KILL -- "-$pgid" 2>/dev/null || true
  sleep 1
  ! kill -0 -- "-$pgid" 2>/dev/null || die "$3 (PGID $pgid) survived SIGKILL"
  echo "$3: SIG$2 was not enough; sent SIGKILL"
}

# start_kopf <run> <hold Incident or ""> <hold s> <race Incident or ""> [liveness URL]
start_kopf() {
  local live=${5:-}
  start_group "$out/kopf-run-$1.log" "$out/kopf-run-$1.pgid" \
    env PYTHONPATH="$root/operator" NEXUS_TEST_KUBECONFIG="$opkc" NEXUS_ENVTEST_DIR="$dir" \
    KUBECONFIG=/nonexistent TEST_HOLD="$2" TEST_HOLD_S="$3" TEST_RACE="$4" \
    "$kopf" run --standalone --namespace "$NS" --verbose ${live:+"--liveness=$live"} \
    "$here/envtest_hooks.py"
}

stop_kopf() { stop_group "$out/kopf-run-$1.pgid" "$2" "kopf run $1"; }

# wait_log <run> <fixed string> <seconds>
wait_log() {
  local i
  for i in $(seq 1 "$3"); do
    grep -qF -- "$2" "$out/kopf-run-$1.log" && return 0
    sleep 1
  done
  return 1
}

inc_json() { k get incident -n "$NS" "$1" -o json 2>/dev/null || echo '{}'; }
phase_of() { inc_json "$1" | jq -r '.status.phase // "-"'; }

# wait_terminal <name> <seconds>: until the Incident exists and is Recorded or Escalated.
wait_terminal() {
  local i p=-
  for i in $(seq 1 "$2"); do
    p=$(phase_of "$1")
    [[ $p == Recorded || $p == Escalated ]] && { echo "$1: $p after ${i}s of waiting"; return 0; }
    sleep 1
  done
  echo "$1: not terminal after $2 s (phase $p)"
}

# create_incident <name> <target namespace>: an admin-created Incident (no fingerprint label).
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

# alert <fingerprint> <startsAt> <namespace label> <nexus_target or ""> <alertname> [extra labels JSON]
alert() {
  local extra=${6:-}
  [[ -n $extra ]] || extra='{}'
  jq -nc --arg fp "$1" --arg s "$2" --arg ns "$3" --arg t "$4" --arg n "$5" --argjson x "$extra" '
    {labels: ({alertname: $n, namespace: $ns, severity: "warning"}
              + (if $t == "" then {} else {nexus_target: $t} end) + $x),
     annotations: {}, fingerprint: $fp, startsAt: $s, endsAt: "2099-01-01T00:00:00Z",
     updatedAt: $s, generatorURL: "", receivers: [{name: "null"}],
     status: {state: "active", silencedBy: [], inhibitedBy: []}}'
}

# serve <alert JSON lines...>: atomically replace what the fake Alertmanager returns.
serve() {
  printf '%s\n' "$@" | jq -s '.' > "$out/alerts.json.tmp"
  mv "$out/alerts.json.tmp" "$out/alerts.json"
}

# name_of <fingerprint> <epoch>: the poller's deterministic Incident name.
name_of() { echo "inc-$1-$2"; }

echo "== setup ($(date -u +%H:%M:%SZ))"
serve
start_group "$out/fake-am.log" "$out/fake-am.pgid" "$py" "$here/fake_alertmanager.py" \
  "$out/alerts.json" "$out/fake-am.port"
for _ in $(seq 1 20); do [[ -s $out/fake-am.port ]] && break; sleep 0.5; done
am_port=$(cat "$out/fake-am.port")
[[ $am_port =~ ^[0-9]+$ ]] || die "the fake Alertmanager wrote no port"
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
k apply --server-side -f "$rbac" > /dev/null
k apply --server-side --force-conflicts -f - > /dev/null <<EOF
apiVersion: v1
kind: ConfigMap
metadata: {name: nexus-killswitch, namespace: $NS}
data: {state: active}
---
apiVersion: v1
kind: ConfigMap
metadata: {name: nexus-operator-config, namespace: $NS}
data:
  alertPollSeconds: "10"
  reconcileSeconds: "5"
  detectedTimeoutSeconds: "20"
  alertmanagerURL: http://127.0.0.1:$am_port
  advisoryChecks: "on"
  approvalTTL: 15m
  breakerEpoch: "0"
  reasonerEndpoint: ""
EOF
sleep 3   # the API server (re)initializes a new CRD's storage asynchronously
offset=$(wc -l < "$audit")
stamp=$(date -u +%H%M%S)
A=test-a-$stamp B=test-b-$stamp C=test-c-$stamp
fpE=e1e1$stamp fpS=5e5e$stamp fpY=a1a1$stamp fpZ=b2b2$stamp
echo "kopf $("$kopf" --version | awk '{print $NF}'); Incidents A=$A B=$B C=$C; fake Alertmanager :$am_port; audit offset $offset"

echo "== run 1: create A, SIGKILL 2 s into test_hold"
start_kopf 1 "$A" 30 ""
wait_log 1 "Initial authentication has finished" 30 || echo "run 1: no authentication line after 30 s"
sleep 3
create_incident "$A" nexus-dev
killed=0
if wait_log 1 "TEST start test_hold $A " 60; then
  sleep 2
  stop_kopf 1 KILL
  killed=1
else
  echo "run 1: test_hold did not start within 60 s; phase $(phase_of "$A")"
  stop_kopf 1 TERM
fi
inc_json "$A" > "$out/a-at-kill.json"

echo "== run 2: resume A; C (race); B (L1, timeout); E1 (alert, nanosecond startsAt)"
start_kopf 2 "$A" 2 "$C"
wait_terminal "$A" 40
create_incident "$C" nexus-dev
wait_terminal "$C" 40
create_incident "$B" nexus-prod
wait_terminal "$B" 45
e1_epoch=$(date -u +%s)
e1_starts=$(date -u -d "@$e1_epoch" +%Y-%m-%dT%H:%M:%S).123456789Z
E1=$(name_of "$fpE" "$e1_epoch")
serve "$(alert "$fpE" "$e1_starts" nexus-dev nexus-dev/sample-api NexusErrorRateAnomaly)"
wait_terminal "$E1" 40
echo "E1 still firing: waiting 3 polls (32 s)"
sleep 32
stop_kopf 2 TERM

echo "== run 3: restart with E1 still firing; liveness; kill switch halted; E2, S, Y, Z, X"
live_port=$(free_port)
start_kopf 3 "" 0 "" "http://127.0.0.1:$live_port/healthz"
wait_log 3 "Initial authentication has finished" 30 || echo "run 3: no authentication line after 30 s"
echo "E1 after the restart: waiting 2 polls (22 s)"
sleep 22
health=$(curl -s -o "$out/healthz.json" -w '%{http_code}' "http://127.0.0.1:$live_port/healthz" || true)
echo "liveness: HTTP $health $(cat "$out/healthz.json" 2>/dev/null)"
k patch configmap -n "$NS" nexus-killswitch --type merge -p '{"data":{"state":"halted"}}' > /dev/null
now=$(date -u +%s)
e2_epoch=$((now - 1)) s_epoch=$((now - 2)) y_epoch=$((now - 3)) z_epoch=$((now))
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
E2=$(name_of "$fpE" "$e2_epoch") S=$(name_of "$fpS" "$s_epoch")
Y=$(name_of "$fpY" "$y_epoch") Z=$(name_of "$fpZ" "$z_epoch")
serve \
  "$(alert "$fpE" "$(iso "$e2_epoch")" nexus-dev nexus-dev/sample-api NexusErrorRateAnomaly)" \
  "$(alert "$fpS" "$(iso "$s_epoch")" nexus-dev nexus-dev/sample-api NexusSmoke '{"nexus_smoke":"true"}')" \
  "$(alert "$fpY" "$(iso "$y_epoch")" nexus-prod nexus-prod/sample-api NexusCpuAnomaly)" \
  "$(alert "$fpZ" "$(iso "$z_epoch")" nexus-prod nexus-prod/sample-api NexusLatencyAnomaly)" \
  "$(alert ffff0001 "$(iso "$now")" monitoring "" Watchdog)" \
  "$(alert ffff0002 "$(iso "$now")" kube-system kube-system/sample-api NexusCpuAnomaly)" \
  "$(alert ffff0003 "$(iso "$now")" nexus-dev nexus-prod/sample-api NexusCpuAnomaly)"
wait_terminal "$E2" 40
wait_terminal "$S" 40
wait_terminal "$Y" 45
wait_terminal "$Z" 90
sleep 12
stop_kopf 3 TERM
stop_group "$out/fake-am.pgid" TERM "fake Alertmanager"

all=("$A" "$B" "$C" "$E1" "$E2" "$S" "$Y" "$Z")
for n in "${all[@]}"; do inc_json "$n" > "$out/end/$n.json"; done
k get incidents -n "$NS" -o json > "$out/end/_all.json"

tail -n +"$((offset + 1))" "$audit" \
  | jq -c --arg u "$OPERATOR_USER" 'select(.user.username == $u)' > "$out/operator-audit.jsonl"

echo "== operator requests in the audit log (count agent verb resource[/subresource] code)"
jq -r '"\(.userAgent // "-" | split("/")[0] | split(" ")[0]) \(.verb) \(.objectRef.resource // .requestURI)\(if .objectRef.subresource then "/" + .objectRef.subresource else "" end) \(.responseStatus.code)"' \
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
# kopf_logs: all runs' logs without the leading [timestamp], so its digits never match "403".
kopf_logs() { sed -E 's/^\[[^]]*\] //' "$out"/kopf-run-{1,2,3}.log; }
end() { echo "$out/end/$1.json"; }

at_kill=$(jq -c '{phase: .status.phase, progress: (.status.kopf.progress // {} | with_entries(.value |= {success, retries}))}' "$out/a-at-kill.json")
a_end=$(jq -c '{phase: .status.phase, reason: .status.reason, diffbase: (.status.kopf["last-handled-configuration"] != null), progress: (.status.kopf.progress // null)}' "$(end "$A")")
ok=0
[[ $killed == 1 ]] \
  && jq -e '.status.kopf.progress.intake_level.success == true' "$out/a-at-kill.json" > /dev/null \
  && jq -e '.status.phase == "Recorded" and .status.reason == "level_observe" and .status.kopf["last-handled-configuration"] != null' "$(end "$A")" > /dev/null \
  && ok=1
verdict P1 "$ok" "A at kill $at_kill; A end $a_end"

writes=$(jq -s '[.[] | select(.objectRef.resource == "incidents" and (.objectRef.subresource // "") != "status" and (.verb == "patch" or .verb == "update"))] | length' "$out/operator-audit.jsonl")
deletes=$(jq -s '[.[] | select(.verb == "delete" or .verb == "deletecollection")] | length' "$out/operator-audit.jsonl")
ok=0
[[ $writes == 0 && $deletes == 0 ]] \
  && jq -e 'all(.items[]; (.metadata.finalizers // [] | length) == 0 and (.metadata.annotations // {} | length) == 0)' "$out/end/_all.json" > /dev/null \
  && ok=1
verdict P2 "$ok" "non-status patch/update calls on incidents: $writes; deletes: $deletes; Incidents with finalizers or annotations: $(jq '[.items[] | select((.metadata.finalizers // [] | length) > 0 or (.metadata.annotations // {} | length) > 0)] | length' "$out/end/_all.json")"

d1=$(c "intake $A " 1 2)
s1=$(c "TEST start test_hold $A " 1); s2=$(c "TEST start test_hold $A " 2)
f1=$(c "TEST done test_hold $A" 1); f2=$(c "TEST done test_hold $A" 2)
ok=0
[[ $killed == 1 && $d1 == 1 && $s1 == 1 && $s2 == 1 && $f1 == 0 && $f2 == 1 ]] && ok=1
verdict P3 "$ok" "A: intake_level done x$d1; test_hold start run1 x$s1 run2 x$s2, done run1 x$f1 run2 x$f2; killed=$killed"

n403=$(jq -s '[.[] | select(.responseStatus.code == 403)] | length' "$out/operator-audit.jsonl")
echo "P4 403s in the audit log: $n403"
jq -r 'select(.responseStatus.code == 403) | "  \(.verb) \(.requestURI)"' "$out/operator-audit.jsonl" | sort | uniq -c
echo "P4 403 or Forbidden lines in Kopf's logs: $(kopf_logs | grep -ciE '\b403\b|forbidden' || true)"
kopf_logs | grep -iE '\b403\b|forbidden' | cut -c1-240 | sort | uniq -c || true

c409=$(jq -s --arg n "$C" '[.[] | select(.objectRef.name == $n and .objectRef.subresource == "status" and .verb == "patch" and .responseStatus.code == 409)] | length' "$out/operator-audit.jsonl")
cconf=$(c "loop conflict $C " 2)
c_end=$(jq -c '{autonomyLevel: .status.autonomyLevel, phase: .status.phase, reason: .status.reason, detected: .status.timestamps.detected, terminal: .status.timestamps.terminal}' "$(end "$C")")
kopf_bad=$(kopf_logs | grep -F 'Merge-patching the status with:' | grep -cE "'(phase|reason)':" || true)
loop_bad=$(kopf_logs | grep -F 'loop write ' | grep -cE '"(autonomyLevel|kopf)"' || true)
kopf_n=$(kopf_logs | grep -cF 'Merge-patching the status with:' || true)
loop_n=$(kopf_logs | grep -cF 'loop write ' || true)
ok=0
[[ $c409 -ge 1 && $cconf -ge 1 && $kopf_bad == 0 && $loop_bad == 0 ]] \
  && jq -e '.status.autonomyLevel == 0 and .status.phase == "Recorded" and .status.reason == "level_observe" and .status.timestamps.detected != null and .status.timestamps.terminal != null' "$(end "$C")" > /dev/null \
  && ok=1
verdict P5 "$ok" "C: 409s in audit x$c409, loop conflicts logged x$cconf; C end $c_end; Kopf status patches x$kopf_n (with phase/reason: $kopf_bad); loop writes x$loop_n (with autonomyLevel/kopf: $loop_bad)"

# dt <name>: creation -> terminal, seconds.
dt() { jq -r '((.status.timestamps.terminal // empty | fromdate) - (.metadata.creationTimestamp | fromdate))' "$(end "$1")"; }
p6_one() {  # p6_one <name>: 1 if it passes in the current mode
  local t
  t=$(dt "$1")
  jq -e '.status.phase == "Escalated" and .status.reason == "evidence_error" and .status.timestamps.detected != null' "$(end "$1")" > /dev/null || { echo 0; return; }
  [[ -n $t ]] || { echo 0; return; }
  if [[ $p6_mode == window ]]; then ((t >= P6_LO && t <= P6_HI)) && echo 1 || echo 0
  else ((t >= P6_LO)) && echo 1 || echo 0; fi
}
ok=0
[[ $(p6_one "$B") == 1 && $(p6_one "$Y") == 1 ]] \
  && jq -s -e 'all(.[]; .status.phase == "Recorded" and .status.reason == "level_observe")' \
       "$(end "$A")" "$(end "$C")" "$(end "$E1")" "$(end "$E2")" "$(end "$S")" > /dev/null \
  && ok=1
verdict P6 "$ok" "mode $p6_mode; B creation->Escalated $(dt "$B") s, Y $(dt "$Y") s (want $P6_LO-$P6_HI); A, C, E1, E2, S $(jq -s -c 'map(.status.phase)' "$(end "$A")" "$(end "$C")" "$(end "$E1")" "$(end "$E2")" "$(end "$S")")"

e1_count=$(jq --arg fp "$fpE" '[.items[] | select(.metadata.labels["nexus.io/fingerprint"] == $fp)] | length' "$out/end/_all.json")
e1_creates=$(c "created incident=$E1 " 2 3)
e1_run3=$(c "created incident=$E1 " 3)
e1_detected=$(jq -r '.spec.detectedAt' "$(end "$E1")")
nolabel=$(jq '[.items[] | select((.metadata.name | startswith("inc-")) and .metadata.labels["nexus.io/fingerprint"] == null)] | length' "$out/end/_all.json")
ok=0
[[ $e1_count == 2 && $e1_creates == 1 && $e1_run3 == 0 && $nolabel == 0 ]] \
  && [[ $(jq -r '.metadata.name' "$(end "$E2")") == "$E2" ]] \
  && "$py" -c 'import sys,datetime as d
def p(s):
    s=s.replace("Z","+00:00"); h,_,f=s.partition(".")
    if f: f,tz=f[:-6],f[-6:]; s=h+"."+f[:6].ljust(6,"0")+tz
    return d.datetime.fromisoformat(s)
sys.exit(0 if p(sys.argv[1])==p(sys.argv[2]) else 1)' "$e1_detected" "$e1_starts" \
  && ok=1
verdict E "$ok" "fingerprint $fpE: $e1_count Incidents (E1, E2); E1 created x$e1_creates (run 3 x$e1_run3); E1 detectedAt $e1_detected vs startsAt $e1_starts; poller Incidents without the fingerprint label: $nolabel"

smoke=$(jq -c '[.items[] | select(.metadata.labels["nexus.io/smoke"] == "true") | .metadata.name]' "$out/end/_all.json")
ok=0
[[ $smoke == "[\"$S\"]" ]] && ok=1
verdict S "$ok" "smoke-labelled: $smoke (want [$S])"

absorbed_line=$(kopf_logs | grep -F "absorbed alertname=NexusLatencyAnomaly fingerprint=$fpZ " | head -1 || true)
z_created=$(jq -r '.metadata.creationTimestamp | fromdate' "$(end "$Z")" 2>/dev/null || echo 0)
y_terminal=$(jq -r '.status.timestamps.terminal // empty | fromdate' "$(end "$Y")" 2>/dev/null || echo "")
ok=0
[[ -n $absorbed_line && $absorbed_line == *"startsAt=$(iso "$z_epoch") incident=$Y"* ]] \
  && [[ -n $y_terminal ]] && ((z_created >= y_terminal)) && ok=1
verdict AB "$ok" "absorbed: ${absorbed_line#*absorbed }; Z created $(date -u -d "@$z_created" +%H:%M:%S), Y terminal $([[ -n $y_terminal ]] && date -u -d "@$y_terminal" +%H:%M:%S)"

expected=$(printf '%s\n' "${all[@]}" | sort | jq -R . | jq -sc .)
actual=$(jq -c '[.items[].metadata.name] | sort' "$out/end/_all.json")
ignored=$(c "ignored alertname=" 3)
ok=0
[[ $expected == "$actual" && $ignored == 2 ]] && ok=1
verdict X "$ok" "Incidents $actual; ignored-alert log lines x$ignored (want 2: outside C3, namespace mismatch; Watchdog silent)"

echo "== per Incident: creation -> terminal, and writes after the loop's terminal write"
tw=0
for n in "${all[@]}"; do
  final_rv=$(jq -r '.metadata.resourceVersion' "$(end "$n")")
  terminal_rv=$(kopf_logs | grep -F "loop write $n " | grep -E '"phase": "(Recorded|Escalated)"' \
    | grep -oE -- '-> [0-9]+' | awk '{print $2}' | tail -1 || true)
  same=$([[ $final_rv == "$terminal_rv" ]] && echo yes || echo no)
  [[ $same == yes ]] || tw=$((tw + 1))
  echo "$n: $(jq -r '.status.phase + " / " + (.status.reason // "-")' "$(end "$n")"), creation -> terminal $(dt "$n") s; final rv $final_rv, terminal write -> ${terminal_rv:-none} (nothing after it: $same)"
done
verdict T "$([[ $tw == 0 ]] && echo 1 || echo 0)" "Incidents written after their terminal write: $tw"

verdict L "$([[ $health == 200 ]] && echo 1 || echo 0)" "GET /healthz in run 3: HTTP $health"

ks=$(c "kill switch halted (changes no transition in M1b-8)" 3)
ks_on=$(c "kill switch active (changes no transition in M1b-8)" 1 2 3)
verdict K "$([[ $ks == 1 && $ks_on == 3 ]] && echo 1 || echo 0)" "logged active at each start x$ks_on, halted x$ks; E2, S, Y, Z decided as above"

echo "loop: conflicts x$(c "loop conflict " 1 2 3), loops stopped cleanly in runs 2 and 3: $(c "loops stopped" 2 3)"
echo "fake Alertmanager requests: $(grep -c 'GET /api/v2/alerts' "$out/fake-am.log" || true); query: $(grep -oE 'GET /api/v2/alerts[^ ]*' "$out/fake-am.log" | sort -u | head -1)"

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo "P1-P3, P5, P6, E, S, AB, X, T, L, K pass; P4 lists $n403 403s"
