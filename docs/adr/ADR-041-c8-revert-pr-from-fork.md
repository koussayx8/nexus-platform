# ADR-041: C8 — The Ready Revert PR, Opened from a Machine-Account Fork (OUTLINE)

**Status:** Outline — draft follows in the next commit
**Date:** 2026-11-16
**Spec:** §13, §19, §20 (C8), AD-16; `TASKS.md` D8, D15

## Outline

1. **Context** — D8, D15; review plan Part 3 (3.3); M3 boundary review section 5; spec:1005
   (token "mounted into the operator"); where a `git_revert` names a commit today.
2. **Decisions** (table)
   - Who opens it: the bot pod, from a fork owned by a GitHub machine account.
   - Token scopes; no write access to `koussayx8/nexus-platform`; no ruleset change.
   - The 10-minute token test, run first.
   - Branch map: `nexus-dev` → `experiment/dev-state`, `nexus-prod` → `main`.
   - The commit to revert: on the tracked branch, touching the target's overlay.
   - The revert is mechanical; no Reasoner text in the diff.
   - The PR body.
   - NEXUS never merges.
   - How the human merge is recognised.
3. **The 10-minute token test** — steps and the three outcomes.
4. **Commit validation** — the checks, in order.
5. **PR title and body** — the template.
6. **Merge recognition and the C8 metric.**
7. **Threat model** (table) and **stated limits**.
8. **Contradictions with the spec and the sources** — for spec v1.1.
9. **Not verified** — each with the command that settles it.
10. **Build checklist, Block 6** — files, tests, hours.
11. **Consequences.**
