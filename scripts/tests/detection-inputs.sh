#!/usr/bin/env bash
# detection-inputs.sh — offline fixture tests for scripts/lib/detection-inputs.jq (M1b-6 6c).
# Needs only jq: no cluster, no network, no temporary files.
#
# The pass set (scripts/tests/fixtures/detection-inputs/pass/, one <id>.json per check) is
# synthetic: responses shaped by the Prometheus HTTP API v1 and the Alertmanager API v2, with the
# label sets the M1b-7 rules select on; hosts and versions are marked "fixture". Every check passes
# on it. Each negative case breaks one response with a jq edit and must fail exactly the check it
# names, with every other check still passing, and every check must have at least one such case.
# The live run (after the M1 exit) scores the real responses with the same filter.
#
# Exit codes: 0 every case as expected; 1 otherwise.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
filter=$here/../lib/detection-inputs.jq
pass=$here/fixtures/detection-inputs/pass

# The pass set as one object {<id>: <response>}, built as the live run builds it.
base=$(jq -n -c 'reduce inputs as $d ({}; . + {(input_filename | split("/") | last | rtrimstr(".json")): $d})' "$pass"/*.json)

# failing <object>: the ids of the checks that fail, space-separated and sorted.
failing() { jq --arg mode check -f "$filter" <<< "$1" | jq -r '[.checks[] | select(.ok | not) | .id] | sort | join(" ")'; }

failed=0
ok() { echo "PASS $1"; }
bad() { echo "FAIL $1: $2"; failed=1; }

# 1. The pass set: every check passes, nothing unexpected.
out=$(jq -c --arg mode check -f "$filter" <<< "$base")
if jq -e '.ok and (.checks | length) == 9 and .unexpected == []' <<< "$out" > /dev/null; then
  ok "pass-set"
else
  bad "pass-set" "$(jq -c '[.checks[] | select(.ok | not) | {id, why}], .unexpected' <<< "$out")"
fi

# 2. List mode: one path per check, the pass set has a file for each, queries URL-encoded.
list=$(jq -n -c --arg mode list -f "$filter")
if [[ $(jq -r '[.[].id] | sort | join(" ")' <<< "$list") == "$(jq -r 'keys | join(" ")' <<< "$base")" ]] \
  && jq -e 'all(.[]; (.path | startswith("/api/")) and (.path | test("[{}\" ]") | not))' <<< "$list" > /dev/null; then
  ok "list-mode"
else
  bad "list-mode" "$list"
fi

# 3. Negative cases: name | the one check that must fail | jq edit of the pass set.
cases=(
  'empty-vector|ksm-replicas-unavailable|.["ksm-replicas-unavailable"].data.result = []'
  'one-namespace-only|ksm-spec-replicas|.["ksm-spec-replicas"].data.result |= map(select(.metric.namespace == "nexus-dev"))'
  'inf-value|ksm-spec-replicas|.["ksm-spec-replicas"].data.result[0].value[1] = "+Inf"'
  'metadata-unknown|ksm-waiting-reason-metadata|.["ksm-waiting-reason-metadata"].data = {}'
  'metadata-wrong-type|ksm-waiting-reason-metadata|.["ksm-waiting-reason-metadata"].data[][0].type = "counter"'
  'nan-value|cadvisor-cpu|.["cadvisor-cpu"].data.result[2].value[1] = "NaN"'
  'prometheus-error|cadvisor-cpu|.["cadvisor-cpu"] = {status: "error", errorType: "bad_data", error: "parse error"}'
  'status-not-grouped|http-requests-labels|.["http-requests-labels"].data.result[2].metric.status = "503"'
  'no-handler-label|http-duration-bucket-labels|.["http-duration-bucket-labels"].data.result[0].metric |= del(.handler)'
  'no-version|am-status|.["am-status"] |= del(.versionInfo)'
  'missing-response|am-status|del(.["am-status"])'
  'no-watchdog|am-alerts|.["am-alerts"][0].labels.alertname = "Other"'
  'no-fingerprint|am-alerts|.["am-alerts"][0] |= del(.fingerprint)'
  'no-groups|am-alert-groups|.["am-alert-groups"] = []'
)
covered=()
for c in "${cases[@]}"; do
  IFS='|' read -r name want edit <<< "$c"
  got=$(failing "$(jq -c "$edit" <<< "$base")")
  if [[ $got == "$want" ]]; then ok "$name: fails $want only"; else bad "$name" "want $want, got '${got}'"; fi
  covered+=("$want")
done

# 4. Coverage: every check has a negative case.
uncovered=$(jq -r --argjson covered "$(printf '%s\n' "${covered[@]}" | jq -R . | jq -s .)" \
  '[.[].id] - $covered | join(" ")' <<< "$list")
if [[ -z $uncovered ]]; then ok "coverage: every check has a negative case"; else bad "coverage" "no negative case for: $uncovered"; fi

exit "$failed"
