#!/usr/bin/env bash
# s5-pin-guard.sh — offline tests for scripts/lib/s5-pin-guard.sh (repo-checks step 9, ADR-025).
# Run by .github/scripts/repo-checks.sh. Needs only git; builds a throwaway repository in a temp
# directory, with no global or system git config, no network and no remote.
#
# Exit codes: 0 every case passed; 1 at least one case failed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
guard=$here/../lib/s5-pin-guard.sh
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.invalid

mkdir "$work/repo" && cd "$work/repo"
git init -q -b main .
mkdir -p alerts
echo a > alerts/a.yaml
echo b > alerts/b.yaml
git add . && git commit -q -m pin
pin=$(git rev-parse HEAD)

failed=0
# case name | setup run at a fresh HEAD (reset to the pin) | listed paths | expected exit code
cases=(
  "unchanged|:|alerts/a.yaml alerts/b.yaml|0"
  "absent-at-pin|:|alerts/a.yaml alerts/gone.yaml|1"
  "absent-at-pin-and-head-added|echo g > alerts/gone.yaml|alerts/a.yaml alerts/gone.yaml|1"
  "changed-at-head|echo changed > alerts/a.yaml|alerts/a.yaml alerts/b.yaml|1"
  "deleted-at-head|rm alerts/b.yaml|alerts/a.yaml alerts/b.yaml|1"
  "unlisted-change|echo c > alerts/c.yaml|alerts/a.yaml alerts/b.yaml|0"
)
for c in "${cases[@]}"; do
  IFS='|' read -r name setup paths want <<<"$c"
  git switch -q --detach "$pin"
  eval "$setup"
  git add -A && git commit -q --allow-empty -m "$name"
  # shellcheck disable=SC2086
  if bash "$guard" "$pin" $paths >"$work/out" 2>&1; then got=0; else got=$?; fi
  if [[ $got == "$want" ]]; then
    echo "PASS $name: exit $got"
  else
    echo "FAIL $name: exit $got, want $want"; sed 's/^/  | /' "$work/out"
    failed=1
  fi
done
exit "$failed"
