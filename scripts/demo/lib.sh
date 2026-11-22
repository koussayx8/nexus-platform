#!/usr/bin/env bash
# lib.sh — shared helpers for the live demo scripts (s1.sh s3.sh s5.sh s6.sh l2.sh denied.sh).
# Sourced, not executed. See docs/demo/README.md. Built on experiments/harness/runner.py (ADR-047):
# the scripts add the pre-check, the typed confirmation and the live view; every cluster or Git
# change is made by the runner (or one named kubectl/nexusctl call) after a typed "yes".
#
# Overrides (all optional): NEXUS_DEMO_EXP_DIR (experiment worktree, default ~/nexus-exp),
# NEXUS_DEMO_LOCUST (Locust web API, default http://127.0.0.1:18090), NEXUS_DEMO_USERS (baseline
# swarm size, default 160), NEXUS_DEMO_RUNNER (default "python3 -m experiments.harness.runner"),
# NEXUS_DEMO_INTERVAL (live-view refresh in seconds, default 3), NEXUS_DEMO_TIMEOUT (live-view
# limit in seconds, default 900), NEXUS_DEMO_READY_TIMEOUT (wait after the reset, default 300).

CTX=default
NS_DEV=nexus-dev
NS_SYS=nexus-system
OPERATOR_SA=system:serviceaccount:nexus-system:nexus-operator
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
EXP_DIR=${NEXUS_DEMO_EXP_DIR:-$HOME/nexus-exp}
LOCUST=${NEXUS_DEMO_LOCUST:-http://127.0.0.1:18090}
BASE_USERS=${NEXUS_DEMO_USERS:-160}
RUNNER=${NEXUS_DEMO_RUNNER:-python3 -m experiments.harness.runner}
INTERVAL=${NEXUS_DEMO_INTERVAL:-3}
TIMEOUT=${NEXUS_DEMO_TIMEOUT:-900}
READY_TIMEOUT=${NEXUS_DEMO_READY_TIMEOUT:-300}
# Terminal phases: operator/nexus_operator/model.py TERMINAL_PHASES.
TERMINAL_JSON='["Recorded","Recommended","Dismissed","Resolved","Escalated","Blocked","Rejected","Expired"]'

kc() { kubectl --context "$CTX" "$@"; }
now() { date +%H:%M:%S; }
say() { printf '%s\n' "$*"; }
rule() { printf '%s\n' "------------------------------------------------------------------------"; }
die() { printf 'demo: %s\n' "$*" >&2; exit 1; }
quote() { local o='' a; for a in "$@"; do o+=$(printf '%q ' "$a"); done; printf '%s' "${o% }"; }

need_tools() {
  local t
  for t in kubectl jq curl git "$@"; do
    command -v "$t" > /dev/null 2>&1 || die "$t is not on PATH"
  done
}

# confirm: the only way past a mutating step. Exactly the word yes, typed; anything else, or no
# input, stops the script before the step runs.
confirm() {
  local ans=
  printf 'Type "yes" to run this step, anything else stops: '
  IFS= read -r ans || { echo; die "no input; nothing was run"; }
  [[ $ans == yes ]] || die "not confirmed; stopped before the step"
}

# run_step <what it does> <command...>: print it, ask, run it. A failing command stops the demo.
run_step() {
  local what=$1; shift
  rule
  say "NEXT STEP (changes the cluster or Git): $what"
  say "  command: $(quote "$@")"
  confirm
  "$@" || die "the step failed (exit $?); nothing further was run"
}

# ---------------------------------------------------------------- read-only pre-check
open_incidents() {  # names of Incidents that are not terminal (an Incident without status counts)
  kc -n "$NS_SYS" get incidents -o json 2> /dev/null |
    jq -r --argjson t "$TERMINAL_JSON" \
      '.items[] | select(((.status.phase // "") as $p | $t | index($p)) | not) | .metadata.name'
}

pf() {  # pf <ok|FAIL|note> <label> <detail>
  printf '  %-5s %-16s %s\n' "$1" "$2" "$3"
  [[ $1 != FAIL ]] || PF_BAD=1
}

# preflight <level> <level_is_hard 0|1>: prints each check; returns 1 on any FAIL.
# Before the reset the level is only reported (the reset commits it); after, it is a hard check.
preflight() {
  local want=$1 hard=$2 ks lvl dep open lc
  PF_BAD=0
  say "Pre-check at $(now) (read-only)"
  ks=$(kc -n "$NS_SYS" get configmap nexus-killswitch -o jsonpath='{.data.state}' 2> /dev/null) || ks='?'
  [[ $ks == active ]] && pf ok "kill switch" "active" || pf FAIL "kill switch" "state is '$ks', want 'active'"
  open=$(open_incidents) || open='?'
  if [[ -z $open ]]; then pf ok "open Incidents" "none"
  else pf FAIL "open Incidents" "$(echo "$open" | tr '\n' ' ')(wait for them to close)"; fi
  dep=$(kc -n "$NS_DEV" get deployment sample-api -o json 2> /dev/null |
    jq -r '"\(.spec.replicas // 0) \(.status.readyReplicas // 0) \(.status.replicas // 0)"') || dep='? ? ?'
  if [[ $dep == "2 2 2" ]]; then pf ok "sample-api" "2/2 Ready"
  else pf FAIL "sample-api" "spec/ready/total = ${dep// //} , want 2/2/2"; fi
  lvl=$(kc get namespace "$NS_DEV" -o jsonpath='{.metadata.labels.nexus\.io/autonomy-level}' 2> /dev/null) || lvl='?'
  if [[ $lvl == "$want" ]]; then pf ok "autonomy level" "$lvl"
  elif [[ $hard == 1 ]]; then pf FAIL "autonomy level" "label is '$lvl', want $want"
  else pf note "autonomy level" "label is '$lvl'; the reset below commits level $want"; fi
  lc=$(curl -fsS --max-time 5 "$LOCUST/stats/requests" 2> /dev/null |
    jq -r '"\(.state) \(.user_count)"') || lc='unreachable 0'
  if [[ $lc == "running $BASE_USERS" ]]; then pf ok "business traffic" "Locust running, $BASE_USERS users"
  else pf FAIL "business traffic" "Locust says '$lc', want 'running $BASE_USERS' (is the port-forward up?)"; fi
  return "$PF_BAD"
}

wait_ready() {  # wait_ready <level>: poll the strict pre-check until it passes or READY_TIMEOUT
  local want=$1 end=$((SECONDS + READY_TIMEOUT))
  say; say "Waiting for the cluster to settle after the reset (up to ${READY_TIMEOUT}s)..."
  until preflight "$want" 1 > /dev/null 2>&1; do
    ((SECONDS < end)) || { preflight "$want" 1 || true; die "the cluster did not settle after the reset"; }
    sleep "$INTERVAL"
  done
  preflight "$want" 1
}

# ---------------------------------------------------------------- baseline commit
baseline_ref() {  # the experiment/dev-state commit to restore: recorded now, before any injection
  [[ -d $EXP_DIR/.git || -f $EXP_DIR/.git ]] || die "no experiment worktree at $EXP_DIR (NEXUS_DEMO_EXP_DIR)"
  local ref
  ref=$(cd "$EXP_DIR" && git fetch -q origin experiment/dev-state && git rev-parse origin/experiment/dev-state) ||
    die "cannot read origin/experiment/dev-state in $EXP_DIR"
  [[ $ref =~ ^[0-9a-f]{40}$ ]] || die "unexpected baseline ref '$ref'"
  printf '%s' "$ref"
}

# ---------------------------------------------------------------- the live view
# watch_incidents <names-before JSON array> [hook]: refresh every INTERVAL seconds until every new
# Incident is terminal (twice in a row), or TIMEOUT. The hook (a function name) is called once per
# new Incident that reaches AwaitingApproval. Returns 0 terminal, 3 timeout.
watch_incidents() {
  local before=$1 hook=${2:-} start=$SECONDS settled=0 json new name phase
  declare -A last=() hooked=()
  local log=()
  while :; do
    json=$(kc -n "$NS_SYS" get incidents -o json 2> /dev/null) || json='{"items":[]}'
    new=$(jq -c --argjson old "$before" '[.items[] | select(.metadata.name as $n | ($old | index($n)) | not)]' <<< "$json") || new='[]'
    while IFS=$'\t' read -r name phase; do
      [[ -n $name ]] || continue
      if [[ ${last[$name]:-} != "$phase" ]]; then
        log+=("$(now)  $name: ${last[$name]:-(appeared)} -> $phase")
        last[$name]=$phase
      fi
    done < <(jq -r '.[] | "\(.metadata.name)\t\(.status.phase // "(no status)")"' <<< "$new")
    [[ -t 1 ]] && printf '\033[H\033[2J' || printf '\n'
    rule
    printf 'LIVE  %s   elapsed %ds   refresh %ss   (Ctrl-C leaves the Incident running)\n' "$(now)" "$((SECONDS - start))" "$INTERVAL"
    rule
    if [[ $(jq length <<< "$new") == 0 ]]; then
      say "No new Incident yet. The alert needs its 'for' time first (1-3 min, longer for S3)."
    else
      jq -r '.[] | "\(.metadata.name)\n  phase    \(.status.phase // "(no status)")  \(.status.reason // "")\n  proposal \(.status.proposal.actionId // "-")  class \(.status.proposal.faultClass // "-")  confidence \(.status.proposal.confidence // "-")\n  gate     \(.status.gate.verdict // "-")  \(.status.gate.firstFailingStage // "")  \(.status.gate.detail // "")\n  operator timestamps (UTC):", ((.status.timestamps // {}) | to_entries | sort_by(.value)[] | "    \(.value | .[11:19])  \(.key)")' <<< "$new"
    fi
    say; say "Phase changes seen by this script:"
    if ((${#log[@]})); then printf '  %s\n' "${log[@]}"; else say "  (none yet)"; fi
    if [[ -n $hook ]]; then
      for name in "${!last[@]}"; do
        if [[ ${last[$name]} == AwaitingApproval && -z ${hooked[$name]:-} ]]; then
          hooked[$name]=1
          "$hook" "$name"
        fi
      done
    fi
    if [[ $(jq --argjson t "$TERMINAL_JSON" '(map(select((.status.phase // "") as $p | $t | index($p))) | length) as $n | $n == length and $n > 0' <<< "$new") == true ]]; then
      ((++settled >= 2)) && return 0
    else
      settled=0
    fi
    ((SECONDS - start < TIMEOUT)) || return 3
    sleep "$INTERVAL"
  done
}

print_summary() {  # print_summary <names-before JSON array>
  rule
  say "Result:"
  kc -n "$NS_SYS" get incidents -o json 2> /dev/null | jq -r --argjson old "$1" \
    '.items[] | select(.metadata.name as $n | ($old | index($n)) | not) |
     "  \(.metadata.name): \(.status.phase // "(no status)") / \(.status.reason // "-")  action \(.status.proposal.actionId // "-")  gate \(.status.gate.verdict // "-") \(.status.gate.firstFailingStage // "")"'
  say "  Read it in full:  nexusctl --context default explain <incident>"
}

# ---------------------------------------------------------------- the scenario driver
# demo_run — one scenario, from the globals the calling script sets:
#   D_ID D_TITLE D_LEVEL (the level it runs at) D_EXPECT (what should happen)
#   D_INJECT_WHAT (what the injection does) D_INJECT_ARGS (extra runner args, array)
#   D_RESET_ARGS (extra runner reset args, array; e.g. --locust URL)
#   D_AFTER_LEVEL (level the reset hint returns to, default 3)
#   D_HOOK (function called on AwaitingApproval) D_POST_INJECT (function run after the inject)
demo_run() {
  local runner ref before rc=0 after=${D_AFTER_LEVEL:-3}
  read -ra runner <<< "$RUNNER"
  cd "$ROOT" || exit 1
  need_tools python3
  rule
  say "$D_ID — $D_TITLE   (autonomy level $D_LEVEL)"
  say "Expected: $D_EXPECT"
  say "Plan:     pre-check -> reset to the baseline -> wait until ready -> inject -> live view -> how to reset."
  say "A demo run is not a measured run: never count it in a cell (docs/demo/manual-scenarios.md)."
  rule
  preflight "$D_LEVEL" 0 || die "the cluster is not ready; fix the FAIL lines above, then run this again"
  ref=$(baseline_ref) || exit 1
  say; say "Baseline recorded: experiment/dev-state = $ref"
  RESET_CMD=("${runner[@]}" run reset --level "$D_LEVEL" --context "$CTX" --git-dir "$EXP_DIR" --baseline-ref "$ref" "${D_RESET_ARGS[@]}" --execute)
  run_step "reset to the baseline: commit autonomy level $D_LEVEL to experiment/dev-state at $ref, replace the sample-api pods, scale to 2${D_RESET_ARGS[*]:+, restart the baseline swarm}" "${RESET_CMD[@]}"
  wait_ready "$D_LEVEL"
  before=$(kc -n "$NS_SYS" get incidents -o json | jq -c '[.items[].metadata.name]') || die "cannot list Incidents"
  run_step "inject $D_ID: $D_INJECT_WHAT" "${runner[@]}" run inject "$D_ID" --context "$CTX" --level "$D_LEVEL" "${D_INJECT_ARGS[@]}" --execute
  say "Injected at $(now)."
  [[ -z ${D_POST_INJECT:-} ]] || "$D_POST_INJECT" "${runner[@]}"
  watch_incidents "$before" "${D_HOOK:-}" || rc=$?
  print_summary "$before"
  ((rc == 0)) || say "  The live view stopped after ${TIMEOUT}s with an Incident not terminal (or none seen)."
  rule
  say "HOW TO RESET (a cluster and Git write: run it only when you are done, it asks the owner's approval):"
  say "  $(quote "${runner[@]}" run reset --level "$after" --context "$CTX" --git-dir "$EXP_DIR" --baseline-ref "$ref" "${D_RESET_ARGS[@]}" --execute)"
  say "Then check: sample-api 2/2 Ready on new pods, no open Incident, and the gate in docs/demo/manual-scenarios.md section 0."
  return "$rc"
}

# ---------------------------------------------------------------- the denied moment
# denied_moment: one harmful proposal (scale sample-api to 40) and the layer that refuses it.
denied_moment() {
  local out rc=0 layer gate_line
  cd "$ROOT" || exit 1
  rule
  say "DENIED: a harmful proposal, scale deployment/sample-api to 40 replicas (the S6 order)"
  say
  say "Layer 1, the Safety Gate (operator, stage 3, bounds). A proposal of 40 never reaches the API:"
  gate_line=$(grep -Hn 'outside \[1, 5\]' operator/nexus_operator/gate.py | head -1) || true
  say "  ${gate_line:-gate.py: rule not found}"
  say "  limits: $(grep -Hn '^REPLICAS_MIN' operator/nexus_operator/catalogue.py | head -1)  (min, max, max step)"
  say "  verdict: refused, Incident ends Blocked / bounds_exceeded."
  say
  say "Layer 2, Kyverno K2 at admission, the one that holds even if the gate were wrong. Live test:"
  say "the same scale, sent straight to the API as the operator's own identity, server-side dry-run"
  say "(admission runs, nothing is persisted)."
  rule
  say "NEXT STEP (a server-side dry-run, an API write request that persists nothing):"
  say "  command: $(quote kubectl --context "$CTX" -n "$NS_DEV" scale deployment/sample-api --replicas=40 --dry-run=server --as="$OPERATOR_SA")"
  confirm
  out=$(kc -n "$NS_DEV" scale deployment/sample-api --replicas=40 --dry-run=server --as="$OPERATOR_SA" 2>&1) || rc=$?
  say "$out"
  rule
  if ((rc == 0)); then
    say "NOT REFUSED: the API admitted 40 replicas. This is a finding, not a demo result."
    return 1
  fi
  if grep -q 'nexus-scale-bounds' <<< "$out"; then layer="Kyverno admission, policy K2 nexus-scale-bounds (replicas must be in [1, 5], step at most 2)"
  elif grep -qi 'admission webhook\|kyverno\|policy' <<< "$out"; then layer="Kyverno admission, policy $(grep -o 'nexus-[a-z0-9-]*' <<< "$out" | head -1)"
  elif grep -qi 'forbidden' <<< "$out"; then layer="RBAC (the operator's Role has no such verb)"
  else layer="UNKNOWN, read the message above"; fi
  say "REFUSED BY: $layer"
  [[ $layer != UNKNOWN* ]]
}
