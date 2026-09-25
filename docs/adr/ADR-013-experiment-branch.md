# ADR-013: experiment/dev-state Is Never Force-Pushed

## Status: Accepted

## Context
`sample-api-dev` tracks `experiment/dev-state` (spec §3). The Experiment Runner commits fault
injections (S3) and autonomy-level changes to it, then resets it between runs (§13). §13 describes
the reset as returning to a baseline tag; §19 keeps the branch "deliberately unprotected". A reset
by force-push would erase the injected commits that the run artefacts refer to, and "never
force-push" would be only a promise.

## Decision
- **Creation:** `experiment/dev-state` is created from `main` in M0-3. Before the M0-5 rebuild,
  `main` is merged into it. The baseline tag on `main` is set only after `verify-state.sh` passes
  (M0-5); the branch is not created from a tag.
- **Reset:** the runner restores the baseline tree with a forward commit,
  `chore(exp): reset to baseline`, skipped when there is no diff. History only grows.
- **Enforcement:** a repository ruleset on `refs/heads/experiment/dev-state` blocks **force-push
  (non-fast-forward) and deletion only**, with **no bypass actors**. Direct pushes stay allowed, so
  the runner commits without pull requests.
- **Levels:** `nexus-dev` is `"0"` on this branch, raised to `"3"` by commit in M2.

## Rationale
- Every injection and every reset stays in history, so run artefacts can cite commits that still exist.
- With no bypass actors the rule binds the owner too: the guarantee is enforced, not promised.
- The branch stays open to direct pushes, which is the runner's working surface under §19.

## Tradeoff
This departs from §13 (create from a tag, reset to the tag) and from §19 (no protection at all).
Both deltas go into spec v1.1. History grows by one reset commit per run that changed the tree.
