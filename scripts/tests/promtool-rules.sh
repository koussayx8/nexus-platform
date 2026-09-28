#!/usr/bin/env bash
# promtool-rules.sh — offline checks for the PrometheusRule manifests in platform/observability/alerts
# (TASKS.md M1b-7, ADR-024). Run by .github/scripts/repo-checks.sh. Needs promtool (pinned to the
# chart's Prometheus version) and yq; no cluster, no network.
#
# promtool reads plain rule files, so each PrometheusRule's .spec is extracted first. Then
# `promtool check rules` on every file, and `promtool test rules` on every unit-test file in
# platform/observability/tests/, which name the extracted files as <manifest-basename>.rules.yaml.
#
# Exit codes: 0 every check and test passed; 1 otherwise.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$here/../..
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

count=0
for f in "$root"/platform/observability/alerts/*.yaml; do
  [[ $(yq '.kind' "$f") == PrometheusRule ]] || continue
  yq '.spec' "$f" > "$work/$(basename "$f" .yaml).rules.yaml"
  count=$((count + 1))
done
(( count > 0 )) || { echo "FAIL: no PrometheusRule manifest found"; exit 1; }

promtool check rules "$work"/*.rules.yaml
cp "$root"/platform/observability/tests/*.test.yaml "$work/"
cd "$work"
promtool test rules ./*.test.yaml
