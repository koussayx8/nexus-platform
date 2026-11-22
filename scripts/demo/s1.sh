#!/usr/bin/env bash
# s1.sh — S1 gray failure at L3: hang every sample-api pod, watch detect -> propose -> act -> verify.
# Every cluster or Git change asks for a typed "yes". See docs/demo/README.md.
# shellcheck disable=SC2034  # the D_* variables are read by demo_run in lib.sh
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_tools
D_ID=S1 D_LEVEL=3
D_TITLE="gray failure (hung handlers, liveness green)"
D_EXPECT="Resolved / remediation_verified in about 4-7 min (Detected +1m50, restart_workload, 2 evictions, restart_v1)"
D_INJECT_WHAT="POST /fault/hang on every sample-api pod in nexus-dev (one kubectl exec per pod)"
D_INJECT_ARGS=() D_RESET_ARGS=()
demo_run
