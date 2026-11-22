#!/usr/bin/env bash
# s5.sh — S5 dependency failure at L3: the app's DB role loses LOGIN; NEXUS must escalate, not act.
# Every cluster or Git change asks for a typed "yes". See docs/demo/README.md.
# shellcheck disable=SC2034  # the D_* variables are read by demo_run in lib.sh
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_tools
D_ID=S5 D_LEVEL=3
D_TITLE="dependency failure (database refuses the app's role)"
D_EXPECT="Escalated / proposed_escalation with dependency_investigation, no mutation, in about 2-3 min"
D_INJECT_WHAT="ALTER ROLE app_dev NOLOGIN and terminate its sessions, in the Dependency DB pod in nexus-data (kubectl exec)"
D_INJECT_ARGS=() D_RESET_ARGS=()
D_EXPECT_ALERTS=(NexusErrorRateAnomaly)
D_EXPECT_WHY="It fires after its for: 1m, about 1m40 after the inject."
demo_run
