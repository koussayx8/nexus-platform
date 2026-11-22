#!/usr/bin/env bash
# l2.sh — S1 at L2: the same fault, but the fix waits for a human approval (K5): from the phone
# (Telegram bot, the first option) or with nexusctl.
# Sets level 2 through Git (the reset), injects S1, and at AwaitingApproval offers the approval: type phone or yes.
# Typed "yes" before every change; the approval then asks you to type the target name.
# See docs/demo/README.md. Afterwards the printed reset returns to L3.
# shellcheck disable=SC2034  # the D_* variables are read by demo_run in lib.sh
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_tools

NEXUSCTL=$(command -v nexusctl || true)
[[ -n $NEXUSCTL || ! -x $ROOT/.venv/bin/nexusctl ]] || NEXUSCTL=$ROOT/.venv/bin/nexusctl
[[ -n $NEXUSCTL ]] || die "nexusctl not found: . .venv/bin/activate && pip install -e libs/nexus-client -e nexusctl"

approve() {
  local ans=
  rule
  say "The Incident $1 is AwaitingApproval: the proposal waits for a human (K5, pinned to its hash)."
  say "Read it first in another terminal:  nexusctl --context $CTX explain $1"
  say
  say "Approve how?"
  say "  phone  approve on Telegram (@nexus_pfe_bot); this script only keeps watching, the bot writes the approval"
  say "  yes    approve here with nexusctl as koussay-approver (fallback if the phone is not at hand)"
  say "  other  stop"
  printf 'Type "phone" or "yes": '
  IFS= read -r ans || { echo; die "no input; nothing was run"; }
  case $ans in
    phone)
      say "Approve on Telegram now (@nexus_pfe_bot): the push has Approve / Reject / Explain"
      say "The live view keeps running until the Incident is terminal. No nexusctl call is made." ;;
    yes)
      say "NEXT STEP (changes the cluster): approve $1 as koussay-approver in group nexus-approvers"
      say "  (nexusctl then asks you to type the target, sample-api; K3 admits that one action)"
      say "  command: $(quote "$NEXUSCTL" --context "$CTX" --as koussay-approver --as-group nexus-approvers approve "$1")"
      "$NEXUSCTL" --context "$CTX" --as koussay-approver --as-group nexus-approvers approve "$1" ||
        die "the approval failed (exit $?); nothing further was run" ;;
    *) die "not confirmed; stopped before any approval" ;;
  esac
}

D_ID=S1 D_LEVEL=2 D_AFTER_LEVEL=3
D_TITLE="gray failure at L2 (a human approves the restart)"
D_EXPECT="AwaitingApproval at about +2m10, then after your approval: 2 evictions and Resolved about 2m45 later"
D_INJECT_WHAT="POST /fault/hang on every sample-api pod in nexus-dev (one kubectl exec per pod)"
D_INJECT_ARGS=() D_RESET_ARGS=()
D_HOOK=approve
D_EXPECT_ALERTS=(NexusLatencyAnomaly)
D_EXPECT_WHY="It fires after its for: 1m, about 1m50 after the inject."
demo_run
