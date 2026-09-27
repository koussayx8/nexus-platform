#!/usr/bin/env bash
# verify-state.sh — asserts the M0 target state and writes docs/CURRENT_STATE.md.
#
# Spec §25/§27, TASKS.md M0-5 item 2. Unlike capture-state.sh's check() (findings never fail
# the run), every verify() call here drives the script's own exit code: non-zero on any failure.
#
# M0 scope (TASKS.md M0-5): Application health, one default Grafana datasource, no
# Loki/Crossplane/sample-db, the sample-api digest and /metrics, namespace autonomy levels,
# pod readiness, the audit-log probe, and the Kill Switch. M1 (TASKS.md M1-3) adds the
# dependency-db Application and pod, and informational container restart counts; M1-5 adds the
# sample-api /items check against the Dependency DB. K1-K6, the
# Incident CRD/CEL, operator and Reasoner readiness and N1-N6 arrive with their milestones.
#
# Usage: scripts/verify-state.sh [--out PATH]
#   --out PATH   where the report is written (default: docs/CURRENT_STATE.md)
# Env: NEXUS_VERIFY_APPS_TIMEOUT (default 840) bounds the M1 retry window, in seconds.
#      NEXUS_VERIFY_ITEMS_NAMESPACES (default "nexus-dev nexus-prod") limits the M10 /items check;
#      any value outside those two is a script error.
#
# Exit codes: 0 every check passed; 1 at least one check failed; 2 script error.

set -uo pipefail

OUT_PATH=docs/CURRENT_STATE.md
args=("$@")
i=0
while (( i < ${#args[@]} )); do
  case ${args[$i]} in
    --out) i=$((i+1)); OUT_PATH=${args[$i]:-} ;;
    *) echo "verify-state: unknown argument ${args[$i]}" >&2; exit 2 ;;
  esac
  i=$((i+1))
done
[[ -n $OUT_PATH ]] || { echo "verify-state: --out needs a path" >&2; exit 2; }

ITEMS_NAMESPACES=${NEXUS_VERIFY_ITEMS_NAMESPACES:-nexus-dev nexus-prod}
[[ -n ${ITEMS_NAMESPACES//[[:space:]]/} ]] || { echo "verify-state: NEXUS_VERIFY_ITEMS_NAMESPACES names no namespace" >&2; exit 2; }
for ns in $ITEMS_NAMESPACES; do
  case $ns in
    nexus-dev|nexus-prod) ;;
    *) echo "verify-state: NEXUS_VERIFY_ITEMS_NAMESPACES: unknown namespace $ns (nexus-dev, nexus-prod)" >&2; exit 2 ;;
  esac
done

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "verify-state: not inside a git repository" >&2; exit 2; }
cd "$REPO_ROOT" || exit 2
command -v jq >/dev/null 2>&1 || { echo "verify-state: jq is required" >&2; exit 2; }

export GIT_OPTIONAL_LOCKS=0 GIT_PAGER=cat PAGER=cat
OUT=$(mktemp -d)   # scratch dir for the shared library's guard-violation marker only
trap 'rm -rf -- "$OUT"' EXIT

# shellcheck source=lib/readonly.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/readonly.sh"

REPORT=$(mktemp)
PASS_COUNT=0
FAIL_COUNT=0

log() { printf '%s\n' "$*" >> "$REPORT"; }

# verify <id> <description> <function> [args...]
# The function prints detail lines to stdout and returns 0 (pass) or non-zero (fail).
verify() {
  local id=$1 desc=$2 detail rc
  shift 2
  detail=$("$@" 2>&1); rc=$?
  if (( rc == 0 )); then
    printf '[PASS] %-4s %s\n' "$id" "$desc"
    log "### $id — $desc — PASS"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    printf '[FAIL] %-4s %s\n' "$id" "$desc"
    log "### $id — $desc — **FAIL**"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
  [[ -n $detail ]] && { printf '%s\n' "$detail" | sed 's/^/       /'; log '```'; log "$detail"; log '```'; }
}

# info <id> <description> <function> [args...]
# Informational only: reported like verify(), but never counted as a pass or a failure.
info() {
  local id=$1 desc=$2 detail
  shift 2
  detail=$("$@" 2>&1)
  printf '[INFO] %-4s %s\n' "$id" "$desc"
  log "### $id — $desc — INFO"
  [[ -n $detail ]] && { printf '%s\n' "$detail" | sed 's/^/       /'; log '```'; log "$detail"; log '```'; }
  return 0
}

# ---------------------------------------------------------------------------
# Expected commits. Each Application tracks a branch (ADR-013/ADR-017): sample-api-dev ->
# experiment/dev-state, every other one -> main. Reading the local checkout would check whatever
# happens to be checked out here, so M1 and M4 read origin/<branch> after one explicit fetch at the
# start of the run. This is the one deliberate exception to the shared g() wrapper's "never
# fetch": fetch only updates remote-tracking refs, it never touches the working tree.
# ---------------------------------------------------------------------------
FETCH_OK=0
git fetch origin main experiment/dev-state >/dev/null 2>&1 && FETCH_OK=1

# ---------------------------------------------------------------------------
# M1. Every Application Synced, Healthy and at the expected commit, in one snapshot, held for
# 60 s (TASKS.md M1-3 commit 7). Judged by the same predicate as bootstrap.sh step h,
# scripts/lib/apps-stable.jq: `Synced` alone is relative to the last revision ArgoCD fetched, so
# for up to ~180 s after a merge every app is Synced/Healthy at the old commit.
#
# Polls every 5 s and passes only after 60 s of consecutive true snapshots, never on the first
# success; any false snapshot resets the streak. At the bound it fails with the last snapshot.
# Every poll is written to the report. Read-only: no refresh annotation.
#
# Bound NEXUS_VERIFY_APPS_TIMEOUT, default 840 s, derived as additive terms (ADR-020 addendum):
#   reconcile delay 180 s  ArgoCD polls Git every timeout.reconciliation 120 s + up to 60 s jitter;
#                          argocd-server is ClusterIP with no Ingress, so no webhook (ADR-014)
#   rollout         600 s  dependency-db's first start: startupProbe 150 x 2 s + a 300 s image pull
#   stable window    60 s
# Not included: sync-retry backoff after a failed sync (an M1-4-style race needs 1000 s). Changing
# the startupProbe budget or the pull allowance means re-deriving all four ADR-020 values, this
# default and bootstrap.sh's dependency-db wait included.
# ---------------------------------------------------------------------------
EXPECTED_APPS=(root platform kyverno observability sample-api-dev sample-api-prod dependency-db)
REPO_URL=https://github.com/koussayx8/nexus-platform.git
APPS_STABLE_JQ=scripts/lib/apps-stable.jq   # relative to REPO_ROOT, the working directory
APPS_TIMEOUT=${NEXUS_VERIFY_APPS_TIMEOUT:-840}
STABLE_WINDOW=60
POLL_INTERVAL=5

v_applications() {
  local main devstate start elapsed streak streak_start=-1 snap detail stable
  (( FETCH_OK )) || { echo "git fetch origin main experiment/dev-state failed: the expected commits are unknown"; return 1; }
  main=$(git rev-parse origin/main) || return 1
  devstate=$(git rev-parse origin/experiment/dev-state) || return 1
  echo "expected: origin/main=$main origin/experiment/dev-state=$devstate"
  echo "bound ${APPS_TIMEOUT}s (NEXUS_VERIFY_APPS_TIMEOUT), stable window ${STABLE_WINDOW}s, poll every ${POLL_INTERVAL}s"
  start=$(date +%s)
  while :; do
    snap=$(k get applications.argoproj.io -n argocd -o json 2>/dev/null) || snap='{"items":[]}'
    detail=$(jq -c -f "$APPS_STABLE_JQ" --arg names "${EXPECTED_APPS[*]}" --arg repo "$REPO_URL" \
      --arg main "$main" --arg devstate "$devstate" --arg detail 1 <<<"$snap") \
      || { echo "evaluating $APPS_STABLE_JQ failed"; return 1; }
    stable=$(jq -r '.stable' <<<"$detail")
    elapsed=$(( $(date +%s) - start ))
    if [[ $stable == true ]]; then
      if (( streak_start < 0 )); then streak_start=$elapsed; fi
      streak=$(( elapsed - streak_start ))
    else
      streak_start=-1; streak=0
    fi
    printf '%s predicate=%s streak=%ss | %s\n' "$(date -u +%FT%TZ)" "$stable" "$streak" \
      "$(jq -r '[.apps[] | "\(.name) \(.sync // "-")/\(.health // "-") expected=\(.expected[0:7]) observed=\((.observed // ["-"]) | map(. // "-" | .[0:7]) | join(","))"] | join("; ")' <<<"$detail")"
    if [[ $stable == true ]] && (( streak >= STABLE_WINDOW )); then
      echo "stable at the expected commits for ${streak}s (after ${elapsed}s)"
      return 0
    fi
    if (( elapsed >= APPS_TIMEOUT )); then
      echo "not stable for ${STABLE_WINDOW}s within the ${APPS_TIMEOUT}s bound; last snapshot:"
      jq -r '.apps[] | "\(.name): sync=\(.sync // "-") health=\(.health // "-") expected=\(.expected) observed=\((.observed // ["-"]) | map(. // "-") | join(",")) ok=\(.ok)"' <<<"$detail"
      return 1
    fi
    sleep "$POLL_INTERVAL"
  done
}

# ---------------------------------------------------------------------------
# M2. Exactly one Grafana datasource with isDefault: true.
# ---------------------------------------------------------------------------
v_grafana_default() {
  local count
  count=$(k get cm -A -l grafana_datasource -o json 2>/dev/null \
    | jq -r '[.items[] | (.data // {})[] | scan("(?i)isDefault\"?\\s*:\\s*true")] | length')
  echo "isDefault:true count across labelled ConfigMaps = ${count:-0}"
  [[ ${count:-0} == 1 ]]
}

# ---------------------------------------------------------------------------
# M3. No Loki, no Crossplane, no sample-db.
# ---------------------------------------------------------------------------
v_no_removed() {
  local rc=0 hits
  hits=$(h list -A -a 2>/dev/null | awk 'NR>1 && tolower($0) ~ /loki|crossplane/')
  [[ -n $hits ]] && { echo "helm releases still present:"; echo "$hits"; rc=1; }
  hits=$(k get deploy,sts -A -o name 2>/dev/null | grep -iE 'loki|sample-db')
  [[ -n $hits ]] && { echo "workloads still present:"; echo "$hits"; rc=1; }
  hits=$(k get crd -o name 2>/dev/null | grep -c 'crossplane\.io')
  [[ ${hits:-0} -gt 0 ]] && { echo "$hits crossplane.io CRDs still present"; rc=1; }
  return $rc
}

# ---------------------------------------------------------------------------
# M4. sample-api: running digest matches Git; /metrics 200 with the two metric names.
# The digest is pinned per overlay (ADR-017), not in the base, and read from origin/<branch>
# after the fetch at the start of the run (see "Expected commits" above).
# ---------------------------------------------------------------------------
v_sample_api() {
  local rc=0
  local pairs=("nexus-dev experiment/dev-state dev" "nexus-prod main prod")
  local pair ns branch overlay git_digest
  for pair in "${pairs[@]}"; do
    read -r ns branch overlay <<<"$pair"
    git_digest=$(git show "origin/$branch:overlays/$overlay/kustomization.yaml" 2>/dev/null | grep -oE 'sha256:[0-9a-f]{64}' | head -n1)
    echo "$ns: Git-pinned digest from origin/$branch:overlays/$overlay/kustomization.yaml = ${git_digest:-NOT FOUND}"
    if [[ -z $git_digest ]]; then rc=1; continue; fi
    local ready
    ready=$(k get pods -n "$ns" -l app.kubernetes.io/name=sample-api -o json 2>/dev/null \
      | jq -r --arg d "$git_digest" '[.items[].status.containerStatuses[]? | select((.imageID // "" | contains($d)) and .ready == true)] | length')
    echo "$ns: ready pods running the pinned digest = ${ready:-0}"
    [[ ${ready:-0} -gt 0 ]] || rc=1
    v_metrics_probe "$ns" || rc=1
  done
  return $rc
}

v_metrics_probe() {   # <namespace> — temporary port-forward, stopped after, timeout-bounded
  local ns=$1 port lport body pid i code
  port=$(k get svc sample-api -n "$ns" -o jsonpath='{.spec.ports[0].port}' 2>/dev/null)
  [[ -n $port ]] || { echo "$ns: no sample-api Service"; return 1; }
  lport=$(( 20000 + RANDOM % 20000 ))
  body=$(mktemp)
  timeout 30 kubectl --request-timeout=15s port-forward -n "$ns" svc/sample-api "$lport:$port" --address 127.0.0.1 >/dev/null 2>&1 &
  pid=$!
  trap '[[ -n ${pid:-} ]] && kill "$pid" 2>/dev/null' RETURN
  for i in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; curl -s -o /dev/null "http://127.0.0.1:$lport/metrics" && break; sleep 0.2; done
  code=$(curl -s -o "$body" -w '%{http_code}' --max-time 8 "http://127.0.0.1:$lport/metrics")
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  echo "$ns: /metrics HTTP $code"
  local ok=0
  if [[ $code == 200 ]]; then
    grep -q '^http_requests_total' "$body" && grep -q '^http_request_duration_seconds' "$body" && ok=1
    [[ $ok == 1 ]] || echo "$ns: expected metric names not found"
  fi
  rm -f "$body"
  [[ $ok == 1 ]]
}

# ---------------------------------------------------------------------------
# M5. Namespace autonomy levels match ADR-018.
# ---------------------------------------------------------------------------
v_namespace_levels() {
  local rc=0 ns want got
  for ns_want in "nexus-prod:1" "nexus-data:0" "nexus-dev:0" "nexus-system:" "nexus-reasoner:" "nexus-load:"; do
    ns=${ns_want%%:*}; want=${ns_want#*:}
    if ! k get ns "$ns" >/dev/null 2>&1; then
      # A namespace that doesn't exist is not the same as one that exists unlabeled — even for
      # the three namespaces where "want" is empty, an absent namespace must still fail.
      echo "$ns: NAMESPACE NOT FOUND (want label=${want:-<unset>})"
      rc=1
      continue
    fi
    got=$(k get ns "$ns" -o jsonpath='{.metadata.labels.nexus\.io/autonomy-level}' 2>/dev/null)
    echo "$ns: label=${got:-<unset>} want=${want:-<unset>}"
    [[ ${got:-} == "$want" ]] || rc=1
  done
  return $rc
}

# ---------------------------------------------------------------------------
# M6. Pod readiness: every container ready, except pods in phase Succeeded (skipped, not
# failed); a pod in phase Failed still fails the check (round 3/round-4 fix).
# ---------------------------------------------------------------------------
v_pod_readiness() {
  local rc=0
  local bad
  bad=$(k get pods -A -o json 2>/dev/null | jq -r '
    .items[]
    | select(.status.phase != "Succeeded")
    | . as $p
    | if .status.phase == "Failed" then
        "\($p.metadata.namespace)/\($p.metadata.name) phase=Failed"
      elif ([$p.status.containerStatuses[]?.ready] | all) then empty
      else
        "\($p.metadata.namespace)/\($p.metadata.name) phase=\($p.status.phase) not-all-containers-ready"
      end')
  local skipped
  skipped=$(k get pods -A -o json 2>/dev/null | jq -r '[.items[] | select(.status.phase == "Succeeded")] | length')
  echo "Succeeded pods skipped: ${skipped:-0}"
  if [[ -n $bad ]]; then echo "$bad"; rc=1; else echo "every non-Succeeded pod: all containers ready"; fi
  return $rc
}

# ---------------------------------------------------------------------------
# M7. Audit probe: one identifiable, side-effect-free write, confirmed in the audit log by
# name, plus an apiserver_audit_event_total delta. Policy rule 3 (§14) logs every write,
# including routine lease renewals, so "the log is growing" alone proves nothing — this
# check looks for one specific, unique event instead.
#
# The one deliberate non-read call in this script: --dry-run=server never persists anything,
# but it is a real, audited `create` request, so it is issued directly rather than through the
# shared k() wrapper (which treats every `create` as mutating, dry-run or not).
# ---------------------------------------------------------------------------
AUDIT_LOG=/var/log/nexus-audit/audit.log
v_audit_probe() {
  local probe rc=0 before_m after_m ef create_rc
  probe="verify-state-probe-$(date +%s)-$RANDOM"
  if [[ ! -r $AUDIT_LOG ]]; then
    echo "audit log not readable at $AUDIT_LOG (absent, or not yet bootstrapped with the new path/ACL)"
    return 1
  fi
  before_m=$(k get --raw /metrics 2>/dev/null | awk '/^apiserver_audit_event_total/{s+=$NF} END{print s+0}')
  ef=$(mktemp)
  timeout 15 kubectl --request-timeout=10s create configmap "$probe" -n nexus-system \
    --from-literal=x=1 --dry-run=server -o yaml >/dev/null 2>"$ef"
  create_rc=$?
  if (( create_rc != 0 )); then
    echo "dry-run probe request failed: $(cat "$ef")"; rm -f "$ef"; return 1
  fi
  rm -f "$ef"
  sleep 1   # audit writes are buffered briefly
  after_m=$(k get --raw /metrics 2>/dev/null | awk '/^apiserver_audit_event_total/{s+=$NF} END{print s+0}')
  if grep -q "\"name\":\"$probe\"" "$AUDIT_LOG" 2>/dev/null; then
    echo "probe event '$probe' found in $AUDIT_LOG"
  else
    echo "probe event '$probe' NOT found in $AUDIT_LOG"
    rc=1
  fi
  echo "apiserver_audit_event_total: before=${before_m:-0} after=${after_m:-0}"
  (( ${after_m:-0} > ${before_m:-0} )) || { echo "apiserver_audit_event_total did not increase"; rc=1; }
  return $rc
}

# ---------------------------------------------------------------------------
# M8. Kill Switch active.
# ---------------------------------------------------------------------------
v_killswitch() {
  local state
  state=$(k get configmap nexus-killswitch -n nexus-system -o jsonpath='{.data.state}' 2>/dev/null)
  echo "nexus-killswitch state=${state:-<not found>}"
  [[ $state == active ]]
}

# ---------------------------------------------------------------------------
# M9. The Dependency DB pod is Ready (TASKS.md M1-3 commit 6; ADR-020). Its probes need TCP
# pg_isready and the init marker, so Ready means a completed init. The pod's uid and restartCount
# are printed for the S5 discard rule (change 9).
# ---------------------------------------------------------------------------
v_dependency_db() {
  local jf rc=0 phase ready uid restarts
  jf=$(mktemp)
  if ! k get pod dependency-db-0 -n nexus-data -o json >"$jf" 2>/dev/null; then
    echo "nexus-data/dependency-db-0: NOT FOUND"; rm -f "$jf"; return 1
  fi
  phase=$(jq -r '.status.phase // "-"' "$jf")
  ready=$(jq -r '[.status.containerStatuses[]? | select(.name == "dependency-db") | .ready][0] // false' "$jf")
  uid=$(jq -r '.metadata.uid // "-"' "$jf")
  restarts=$(jq -r '[.status.containerStatuses[]? | select(.name == "dependency-db") | .restartCount][0] // "-"' "$jf")
  echo "nexus-data/dependency-db-0: phase=$phase ready=$ready uid=$uid restartCount=$restarts"
  [[ $phase == Running && $ready == true ]] || rc=1
  rm -f "$jf"
  return $rc
}

# ---------------------------------------------------------------------------
# M10. sample-api /items reads the Dependency DB (TASKS.md M1-5, change 1): HTTP 200 with at least
# one row, per namespace in NEXUS_VERIFY_ITEMS_NAMESPACES. M1-5 run 1 sets nexus-prod only: until
# the forward-merge, nexus-dev still runs the image without /items. A skipped namespace is named in
# the report. Only the status, the row count and the app's own error code are printed, never rows.
# ---------------------------------------------------------------------------
v_items() {
  local rc=0 ns
  for ns in nexus-dev nexus-prod; do
    if [[ " $ITEMS_NAMESPACES " != *" $ns "* ]]; then
      echo "$ns: skipped (NEXUS_VERIFY_ITEMS_NAMESPACES=$ITEMS_NAMESPACES)"
      continue
    fi
    v_items_probe "$ns" || rc=1
  done
  return $rc
}

v_items_probe() {   # <namespace> — temporary port-forward, stopped after, timeout-bounded
  local ns=$1 port lport body pid i code rows err ok=0
  port=$(k get svc sample-api -n "$ns" -o jsonpath='{.spec.ports[0].port}' 2>/dev/null)
  [[ -n $port ]] || { echo "$ns: no sample-api Service"; return 1; }
  lport=$(( 20000 + RANDOM % 20000 ))
  body=$(mktemp)
  timeout 30 kubectl --request-timeout=15s port-forward -n "$ns" svc/sample-api "$lport:$port" --address 127.0.0.1 >/dev/null 2>&1 &
  pid=$!
  trap '[[ -n ${pid:-} ]] && kill "$pid" 2>/dev/null' RETURN
  for i in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; curl -s -o /dev/null "http://127.0.0.1:$lport/health" && break; sleep 0.2; done
  code=$(curl -s -o "$body" -w '%{http_code}' --max-time 8 "http://127.0.0.1:$lport/items")
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  if [[ $code == 200 ]]; then
    rows=$(jq -r '.items | length' "$body" 2>/dev/null)
    echo "$ns: /items HTTP 200, rows=${rows:-unparseable}"
    [[ $rows =~ ^[0-9]+$ ]] && (( rows > 0 )) && ok=1
  else
    err=$(jq -r '.error // empty' "$body" 2>/dev/null | grep -xE '[a-z_]{1,40}')
    echo "$ns: /items HTTP $code${err:+ error=$err}"
  fi
  rm -f "$body"
  [[ $ok == 1 ]]
}

# ---------------------------------------------------------------------------
# I1. Container restart counts, informational (TASKS.md M1-3 commit 4): spots flapping pods
# across a rebuild without turning a transient restart into a failure.
# ---------------------------------------------------------------------------
v_restart_counts() {
  k get pods -A -o json 2>/dev/null | jq -r '
    [.items[] | {pod: "\(.metadata.namespace)/\(.metadata.name)",
                 c: [.status.containerStatuses[]? | {name, restarts: .restartCount}]}] as $pods
    | ($pods[] | "\(.pod): " + ([.c[] | "\(.name)=\(.restarts)"] | join(" "))),
      "\($pods | length) pods; \([$pods[].c[] | select(.restarts > 0)] | length) containers restarted at least once"'
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
log "# NEXUS — Current State"
log ""
log "Generated $(date -u +%FT%TZ) by \`scripts/verify-state.sh\`. Never hand-edited (spec §25)."
log ""

echo "verify-state: M1 polls every ${POLL_INTERVAL}s until every Application is stable at the expected commit for ${STABLE_WINDOW}s (bound ${APPS_TIMEOUT}s)"
verify M1 "Applications stable at the expected commits"  v_applications
verify M2 "Exactly one default Grafana datasource"       v_grafana_default
verify M3 "No Loki, Crossplane, or sample-db"            v_no_removed
verify M4 "sample-api digest and /metrics"               v_sample_api
verify M5 "Namespace autonomy levels (ADR-018)"          v_namespace_levels
verify M6 "Pod readiness (Succeeded pods skipped)"       v_pod_readiness
verify M7 "Audit log probe (§14)"                        v_audit_probe
verify M8 "Kill Switch active"                           v_killswitch
verify M9 "Dependency DB pod Ready"                      v_dependency_db
verify M10 "sample-api /items reads the Dependency DB"   v_items
info   I1 "Container restart counts (informational)"   v_restart_counts

log ""
log "## Summary"
log ""
log "$PASS_COUNT passed, $FAIL_COUNT failed."

mkdir -p -- "$(dirname -- "$OUT_PATH")" 2>/dev/null
cp -- "$REPORT" "$OUT_PATH"
rm -f -- "$REPORT"

echo
echo "verify-state: $PASS_COUNT passed, $FAIL_COUNT failed; report written to $OUT_PATH"
(( FAIL_COUNT == 0 )) && exit 0 || exit 1
