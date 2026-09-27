#!/usr/bin/env bash
# apps-stable.sh — offline fixture tests for scripts/lib/apps-stable.jq (TASKS.md M1-3 commit 1).
# Run by .github/scripts/repo-checks.sh. Needs only jq; no cluster, no network.
#
# Each case feeds one fixture snapshot (scripts/tests/fixtures/apps-stable/) to the predicate and
# checks the boolean, then checks that detail mode reports the same result.
#
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
filter=$here/../lib/apps-stable.jq
fixtures=$here/fixtures/apps-stable
repo=https://github.com/koussayx8/nexus-platform.git
main=1111111111111111111111111111111111111111
devstate=2222222222222222222222222222222222222222
all="root kyverno observability sample-api-dev"

# case name | Application names | expected result
cases=(
  "all-at-expected|$all|true"
  "one-at-old-sha|$all|false"
  "chart-plus-git-multisource|kyverno observability|true"
  "revisions-shorter-than-sources|$all|false"
  "repourl-mismatch|$all|false"
)

failed=0
for c in "${cases[@]}"; do
  IFS='|' read -r name names want <<<"$c"
  args=(--arg names "$names" --arg repo "$repo" --arg main "$main" --arg devstate "$devstate")
  got=$(jq -f "$filter" "${args[@]}" "$fixtures/$name.json")
  detail=$(jq -c -f "$filter" "${args[@]}" --arg detail 1 "$fixtures/$name.json" | jq -r .stable)
  if [[ $got == "$want" && $detail == "$want" ]]; then
    echo "PASS $name: $got"
  else
    echo "FAIL $name: got=$got detail=$detail want=$want"
    failed=1
  fi
done
exit "$failed"
