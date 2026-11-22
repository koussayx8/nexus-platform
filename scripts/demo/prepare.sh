#!/usr/bin/env bash
# prepare.sh <level> [--locust] — before the jury: pre-check, halt the Kill Switch and reset to the
# baseline at <level>, wait until Ready, settle, re-activate the Kill Switch; then stop. In front of
# the jury, run the scenario with NEXUS_DEMO_SKIP_RESET=1 (no reset, no settle, strict pre-check).
# --locust also restarts the baseline swarm (after S6 or a spike). Typed "yes" before every change.
# See docs/demo/README.md.
# shellcheck disable=SC2034  # D_RESET_ARGS is read by prepare_cluster in lib.sh
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_tools python3
level=${1:-}
[[ $level =~ ^[0-3]$ ]] || die "usage: scripts/demo/prepare.sh <level 0-3> [--locust]"
D_RESET_ARGS=()
case ${2:-} in
  '') ;;
  --locust) D_RESET_ARGS=(--locust "$LOCUST") ;;
  *) die "usage: scripts/demo/prepare.sh <level 0-3> [--locust]" ;;
esac
cd "$ROOT" || exit 1
rule
say "PREPARE — autonomy level $level, for a scenario run later with NEXUS_DEMO_SKIP_RESET=1"
say "Plan:     pre-check -> halt the Kill Switch and reset to the baseline -> wait until ready -> settle"
say "          -> re-activate the Kill Switch -> stop."
rule
preflight "$level" 0 || die "the cluster is not ready; fix the FAIL lines above, then run this again"
ref=$(baseline_ref) || exit 1
say; say "Baseline recorded: experiment/dev-state = $ref"
prepare_cluster "$level" "$ref"
rule
say "Ready for the inject at $(now) (level $level, Kill Switch active, no NEXUS alert, no open Incident)."
say "In front of the jury, for example:  NEXUS_DEMO_SKIP_RESET=1 scripts/demo/s1.sh"
