#!/usr/bin/env bash
# s3.sh — S3 bad rollout at L3: a bad commit on experiment/dev-state; NEXUS must escalate, not act.
# Every cluster or Git change asks for a typed "yes". See docs/demo/README.md.
# shellcheck disable=SC2034  # the D_* variables are read by demo_run in lib.sh
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_tools

# After the inject, ask Argo CD to refresh sample-api-dev so the bad revision lands sooner.
post_inject() {
  run_step "ask Argo CD to refresh its Applications (a hard refresh annotation, no sync)" \
    "$@" run refresh --context "$CTX" --execute
}

D_ID=S3 D_LEVEL=3
D_TITLE="bad rollout (a bad commit in Git)"
D_EXPECT="Escalated / proposed_escalation with git_revert naming the commit, no mutation; the alert needs about 4-9 min"
D_INJECT_WHAT="commit experiments/faults/s3-bad-rollout.patch (readinessProbe /readyz) and push it forward to experiment/dev-state"
D_INJECT_ARGS=(--git-dir "$EXP_DIR" --s3-patch experiments/faults/s3-bad-rollout.patch)
D_RESET_ARGS=()
D_EXPECT_ALERTS=(NexusRolloutStuck NexusCrashLooping)
D_EXPECT_WHY="This takes minutes: Argo CD polls Git every 3 min (the refresh may shorten it) and deploys the bad revision, then NexusRolloutStuck needs its for: 3m; about 9-10 min from the inject in the trials."
D_POST_INJECT=post_inject
demo_run
