#!/usr/bin/env bash
# verify-state.sh — asserts the M0 target state and writes docs/CURRENT_STATE.md.
#
# Spec §25/§27, TASKS.md M0-5 item 2. Unlike capture-state.sh's check() (findings never fail
# the run), every verify() call here drives the script's own exit code: non-zero on any failure.
#
# M0 scope only (TASKS.md M0-5): Application health, one default Grafana datasource, no
# Loki/Crossplane/sample-db, the sample-api digest and /metrics, namespace autonomy levels,
# pod readiness, the audit-log probe, and the Kill Switch. K1-K6, the Incident CRD/CEL, operator
# and Reasoner readiness and N1-N6 arrive with their milestones (TASKS.md "Later").
#
# Usage: scripts/verify-state.sh [--out PATH]
#   --out PATH   where the report is written (default: docs/CURRENT_STATE.md)
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

# ---------------------------------------------------------------------------
# M1. Every Application Synced and Healthy.
# ---------------------------------------------------------------------------
EXPECTED_APPS=(root platform kyverno observability sample-api-dev sample-api-prod)
v_applications() {
  local name sync health rc=0 jf ef
  jf=$(mktemp); ef=$(mktemp)
  for name in "${EXPECTED_APPS[@]}"; do
    if ! k get "applications.argoproj.io/$name" -n argocd -o json >"$jf" 2>"$ef"; then
      echo "$name: NOT FOUND ($(cat "$ef"))"; rc=1; continue
    fi
    sync=$(jq -r '.status.sync.status // "-"' "$jf")
    health=$(jq -r '.status.health.status // "-"' "$jf")
    echo "$name: sync=$sync health=$health"
    [[ $sync == Synced && $health == Healthy ]] || rc=1
  done
  rm -f "$jf" "$ef"
  return $rc
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
# The digest is pinned per overlay (ADR-017), not in the base. Each namespace's Application
# tracks a different branch (ADR-013/ADR-017): sample-api-dev -> experiment/dev-state,
# sample-api-prod -> main. Reading from the local checkout would check whatever happens to be
# checked out here, which is neither of those — so this reads origin/<branch> after an explicit
# fetch. This is the one deliberate exception to the shared g() wrapper's "never fetch": fetch
# only updates remote-tracking refs, it never touches the working tree.
# ---------------------------------------------------------------------------
v_sample_api() {
  local rc=0
  git fetch origin main experiment/dev-state >/dev/null 2>&1
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
# Run
# ---------------------------------------------------------------------------
log "# NEXUS — Current State"
log ""
log "Generated $(date -u +%FT%TZ) by \`scripts/verify-state.sh\`. Never hand-edited (spec §25)."
log ""

verify M1 "Applications Synced and Healthy"              v_applications
verify M2 "Exactly one default Grafana datasource"       v_grafana_default
verify M3 "No Loki, Crossplane, or sample-db"            v_no_removed
verify M4 "sample-api digest and /metrics"               v_sample_api
verify M5 "Namespace autonomy levels (ADR-018)"          v_namespace_levels
verify M6 "Pod readiness (Succeeded pods skipped)"       v_pod_readiness
verify M7 "Audit log probe (§14)"                        v_audit_probe
verify M8 "Kill Switch active"                           v_killswitch

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
