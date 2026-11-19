# ADR-041: C8 — The Ready Revert PR, Opened from a Machine-Account Fork

**Status:** Proposed (accepted when its pull request merges). Owner answers of 2026-11-17 are recorded below;
the pull request stays draft until Block 6.
**Date:** 2026-11-16
**Spec:** §13 (GitOps boundary, spec:1005), §19 (rulesets), §20 (configuration C8, spec:1788), AD-16. C8 moves
from SHOULD to in scope, recorded for spec v1.1 (#203). `docs/architecture/final-spec.md` does not change with
this ADR.
**Decisions it implements:** `TASKS.md` D8 (C8 in), D15 (fork, machine account, the token test).
**Built in:** Block 6.

Labels: **VERIFIED** = read in a file on `origin/dev` at `4b077fb` in this session, or a `gh` read, cited.
**UNVERIFIED** = not exercised; the settling command is named. No cluster command was run for this ADR.

## Context

For S3, a bad deployment, the only correct fix is a Git revert: ArgoCD selfHeal undoes a live rollback within
about 30 seconds, so `rollout undo` is not a NEXUS action (spec:681, spec:989). Today NEXUS ends such an
Incident `Escalated` with `recommendation.kind: git_revert` and stops. D8 adds the ready pull request.

The designs are in `~/nexus-reports/fable-review-plan-2026-11-12.md` Part 3 (3.3) and in section 5 of
`docs/reviews/2026-11-15-m3-boundary.md` (PR #233, not yet on `dev`). D15 chose the fork over a ruleset change.

Facts this ADR builds on:

| Fact | Status |
|---|---|
| `nexus-dev` is synced from `experiment/dev-state`, path `overlays/dev`; `nexus-prod` from `main`, path `overlays/prod` | VERIFIED: `platform/argocd/applications/sample-api-dev.yaml:17-18`, `sample-api-prod.yaml:17-18` |
| `koussayx8/nexus-platform` is public and is not itself a fork | VERIFIED: `gh repo view --json visibility,isFork`, 2026-11-16 |
| The rules reasoner R1 names **no commit**: its recommendation is `{kind: git_revert, detail: <reason text>}` | VERIFIED: `operator/nexus_operator/rules.py:64-65`, `:92-99` |
| The proposal schema requires only `recommendation {kind, detail}`; `detail` is free text | VERIFIED: `operator/nexus_operator/proposal.py:82-93` |
| The evidence bundle carries the Argo CD synced revision as `lastCommit.sha` | VERIFIED: `operator/nexus_operator/evidence.py:236-237` |
| The operator's NetworkPolicy has no public egress; its comment says the spec's public-443 rule "is for the SHOULD fork-PR feature and is omitted" | VERIFIED: `platform/network/system.yaml:36-38` |
| Every workflow triggers on `pull_request`; none on `pull_request_target` or `workflow_run` | VERIFIED in the review (section 5); `grep` on `.github/workflows/` here shows `pull_request` only |

## Owner answers, 2026-11-17 (#237)

| Point | Answer |
|---|---|
| A1: the newest commit touching the overlay, when the Reasoner names none | Accepted. The pull request body must say the commit was chosen by this rule, not by the Reasoner |
| A2: the commit must touch **only** the overlay path | Accepted |
| Fallback (outcome C): push to the fork and post the compare link | Accepted |
| The token is in the bot, not the operator | **This ADR overrides spec:1005.** Added to the spec v1.1 list as X13 (`docs/plans/pfe-replan.md`) |
| 2026-11-19 (#255): check 4b; "recent"; token test step 5; merge recording | 4b added (workload files only, never the Namespace). Recent = the last 50 commits **and** 24 hours. Step 5 makes no merge attempt. Merge recognition stays a notification (decision 10, limit 6): no Incident or Flight Recorder write. The S3 fault is committed as `experiments/faults/s3-bad-rollout.patch`, and the runner stages only its files (#256; settles U5 once merged) |

## Decisions

| # | Decision |
|---|---|
| 1 | **The bot pod opens the pull request, not the operator.** The bot (ADR-040) already watches Incidents and has outbound access. The operator keeps no Git credential and no public egress, so "NEXUS never writes desired state" holds for the component that holds cluster rights. |
| 2 | **From a fork owned by a GitHub machine account.** The account owns one repository, a fork of `koussayx8/nexus-platform`. It is **not** a collaborator on the upstream repository and is in no team. The bot pushes a branch to the fork and opens a pull request against the upstream branch. |
| 3 | **The token has no write access to `koussayx8/nexus-platform`.** First choice: a fine-grained token, repository access "only the fork", Contents read/write, Pull requests read/write, Metadata read. If that token cannot open the upstream pull request: a classic token with `public_repo` only. **No ruleset change** (D15). |
| 4 | **The 10-minute token test runs first**, before any Block 6 code (below). Its result picks the token type, or the fallback. |
| 5 | **Branch map, static, in the bot's ConfigMap in Git:** `nexus-dev` → `experiment/dev-state`, overlay `overlays/dev`; `nexus-prod` → `main`, overlay `overlays/prod`. Any other namespace: no pull request. |
| 6 | **The commit to revert must be on the tracked branch and touch only the target's overlay** (A2). A SHA named by the Reasoner is untrusted and is accepted only if it passes these checks (below). When the Reasoner names none, the bot takes the newest commit on the tracked branch that touches the overlay (A1), and the pull request body says so. If no check-passing SHA exists, no pull request is opened and the chat says why. |
| 7 | **The diff is a mechanical revert computed by the bot** (`git revert`). No Reasoner output is ever in the diff, the commit message, the branch name or the title. |
| 8 | **The pull request is ready, not draft**, titled and described from a fixed template filled with validated fields (below). |
| 9 | **NEXUS never merges.** The token cannot merge (no upstream write). The bot calls no merge endpoint, enables no auto-merge, and sets `maintainer_can_modify: false`. The owner merges under CLAUDE.md rule 11. |
| 10 | **The human merge is recognised by polling the pull request.** The Incident stays `Escalated`, which is terminal. Recovery is ordinary: ArgoCD syncs, the rollout completes, the alert resolves. |
| 11 | **One pull request per Incident, and at most one open revert pull request per tracked branch.** Working branch `nexus/revert-<incident-name>`; the bot pushes only branches under `nexus/`, only to the fork. |

## The 10-minute token test

Run by the owner (it needs the machine account and its tokens), with the agent preparing the commands. Nothing
is merged; the test pull request is closed at the end.

| Step | Action | Expected |
|---|---|---|
| 1 | Create the machine account; fork `koussayx8/nexus-platform` with all branches; disable Actions on the fork | — |
| 2 | Create the fine-grained token (decision 3). Store it in `~/.nexus/`, never in Git or in a command line | — |
| 3 | With that token: push a branch `nexus/token-test` to the **fork**, holding one empty commit on top of `experiment/dev-state` | Accepted |
| 4 | With that token: `POST /repos/koussayx8/nexus-platform/pulls` with `head: <machine>:nexus/token-test`, `base: experiment/dev-state` | **This is the question.** Expected to be refused: a fine-grained token reaches only the repositories it lists, and the upstream is not one of them (UNVERIFIED — recalled from GitHub's documentation, not tested) |
| 5 | Negative checks with the same token, **no merge attempt** (owner, 2026-11-19): `GET /repos/koussayx8/nexus-platform` → `permissions.push`; `git push` of a throwaway ref (`nexus/token-test-throwaway`) to the **upstream** | `permissions.push` is `false` and the push gets `403`. If either does not hold, stop: decision 3 is broken. Steps 3 and 4 are the positive checks (fork push, upstream pull request) |
| 6 | If step 4 was refused: create the classic token with `public_repo` only and repeat steps 3 to 5 | 3 and 4 accepted, 5 refused |
| 7 | Close the test pull request; the owner deletes the test branch in the fork | — |

| Outcome | Then |
|---|---|
| A — the fine-grained token opens the pull request | Use it. Smallest reach: one repository |
| B — only the classic token does | Use it, and state its reach in the thesis: write to the machine account's own public repositories, and pull requests, issues and comments on any public repository. Still no write upstream |
| C — neither does | **Fallback (accepted by the owner):** the bot pushes the branch to the fork and posts the compare link (`…/compare/<base>...<machine>:nexus/revert-<incident>?expand=1`) in the chat; the owner opens the pull request with one tap. C8's metric then starts at the link, not at the pull request |

Record the outcome, with the HTTP status of steps 4 and 5, in this ADR before Block 6 continues.

## Commit validation

Input: an Incident in phase `Escalated` whose proposal is `escalate` with `recommendation.kind: git_revert`,
and whose `spec.target.namespace` is in the branch map. Checks, in order; the first failure ends the attempt
with one chat line naming the check.

| # | Check | Why |
|---|---|---|
| 1 | The candidate is a full 40-hex SHA | No refs, no ranges, no option-like strings reach `git` |
| 2 | The bot fetches the tracked branch from the **upstream** (anonymous, read-only, depth 50), the candidate is an ancestor of its tip, **and its committer date is within 24 hours** (owner, 2026-11-19) | "On the tracked branch" and "recent": not a fork commit, not another branch, not older than 50 commits or 24 hours |
| 3 | The candidate has exactly one parent | Reverting a merge needs a chosen parent; refuse instead |
| 4 | The candidate changes at least one file, and **every** file it changes is under the target's overlay path (A2) | A commit that also touches `platform/` or `.github/` would have those changes reverted too |
| 4b | **Every** file it changes is a sample-api workload file of the overlay (`overlays/dev/kustomization.yaml`, `overlays/dev/sample-api*.yaml`; the set the S3 runner may commit), and none is `overlays/dev/namespace.yaml` (owner, 2026-11-19) | The Namespace holds `nexus.io/autonomy-level`, and the runner commits levels to it (`experiments/harness/runner.py:58-60`): without 4b, rule A1 or a named SHA could open a pull request that changes `nexus-dev`'s level |
| 5 | `git revert --no-edit <sha>` applies without conflict, and the resulting diff is not empty |
| 4c | If the revert changes `<overlay>/kustomization.yaml`: parsed as YAML at the branch tip and after the revert, **every key except `patches`** is identical; an unparsable or missing file is refused (owner, 2026-11-19) | 4b checks file names only, and that file's `resources` lists `namespace.yaml`: a revert could drop or restore the Namespace, or change `namespace`, `labels` or `images`, through an allowed file. Consequence: a commit that changes only the image digest is not revertible by C8 (escalation without a pull request) | A commit already reverted, or changed since, is not reverted blindly |
| 6 | No open pull request from `<machine>:nexus/revert-*` targets that branch, and the working branch does not exist in the fork | Decision 11 |

**Where the candidate comes from.** The frozen spec says the escalation names "the commit that introduced the
regression" (spec:1005, spec:1801). Today nothing structured does: R1's `detail` is the reason text, and the
schema has no commit field. So:

- If `recommendation.detail` contains exactly one 40-hex token, that is the candidate (untrusted; checks 1–6).
- Otherwise (rule A1) the candidate is the newest commit on the tracked branch that touches the overlay path.
  It then goes through the same checks 1–6; if it fails one, no older commit is tried. The pull request body
  says the commit was chosen by rule A1, not by the Reasoner.

**Two rules added by this ADR, accepted by the owner on 2026-11-17:**

| # | Rule | Reason |
|---|---|---|
| A1 | The newest overlay commit, when the Reasoner names none. The body names the rule | Without it, every R1 run of S3 opens no pull request, and C8 could only be shown with R3 |
| A2 | Check 4 is strict: **every** file the candidate changes is under the overlay path | See check 4. The runner's S3 commits touch the overlay only (UNVERIFIED, U5 — read the S3 injector in Block 6) |

Git is run with hooks disabled, no submodules, in an empty scratch directory. The token is passed by an
askpass helper or an HTTP header, never in a URL, in `argv` or in a log line. The revert commit's message is
fixed: `revert(<overlay>): revert <sha>` and the line `This reverts commit <sha>.` — the original subject,
which is repository content, is not copied into it.

## Pull request title and body

Title: `revert(<overlay>): revert <sha, 12 hex> for <incident name>`

```
NEXUS opened this pull request. NEXUS did not merge it and cannot.

Incident:        <name>            (namespace nexus-system)
Target:          <namespace>/<deployment>
Detected at:     <spec.detectedAt>
Fault class:     <enum>            confidence <number>
Reasoner:        <arm> / <model>
Evidence bundle: <sha256:…>
Proposal:        <sha256:…>

Reverts:         <full SHA>
Subject:         `<original subject, sanitised, at most 72 characters>`
Chosen by:       <one of the two lines below, verbatim>
                 the Reasoner named this commit; NEXUS checked it
                 rule A1 (ADR-041): the newest commit touching <overlay>. The Reasoner did NOT name it
Tracked branch:  <branch>          (Argo CD application <name>)

The diff is a mechanical `git revert` of that one commit. Review the diff, not this text.
```

Every field is an enum, a number, a hash, a name already validated by the CRD, or a timestamp — except the
subject, which is sanitised (`nexusctl`'s `sanitize`), stripped of `@` and `#`, and put in a code span. **The
Reasoner's free text (hypothesis, `detail`) is not in the body.** So the body cannot carry a mention, a link,
or a line shaped like an approval; and rule 11 ignores approvals in pull-request text in any case.

No label. The plan asked for a `nexus-revert` label, but an account with no triage right on the upstream
cannot set one. The export finds these pull requests by author (the machine account) and head prefix
`nexus/revert-`.

## Merge recognition and the C8 metric

| Aspect | Rule |
|---|---|
| Poll | `GET /repos/koussayx8/nexus-platform/pulls/<N>` every 30 seconds |
| Merged | `merged == true`: the bot posts "merged by `<merged_by.login>`, merge commit `<merge_commit_sha>`" and stops polling |
| Closed without merge | The bot posts "closed, not merged" and stops polling |
| Timeout | 24 hours, then one line and stop. The pull request stays open |
| After a bot restart | The open pull requests are found again by author and head prefix; the bot keeps no state file |
| The Incident | Stays `Escalated`. No new phase, no status write: the bot has no write on `incidents/status`, and the phase is terminal in the spec |
| The alert | Resolves by itself after the sync; the bot's ordinary `Resolved` push does not apply (the Incident is terminal), so the Alertmanager "resolved" message (ADR-040 decision 12) is what the chat shows |

C8's metric is the human time to resolve S3 with and without a ready pull request (spec:1788). Its three
timestamps come from outside NEXUS: the pull request's `created_at` and `merged_at`, and the alert's resolve
time. Between merge and recovery lies ArgoCD's pickup of the new commit, 108 to 382 seconds per ADR-020
(UNVERIFIED this session) unless the application is refreshed. A pull request into `main` also waits for the
required check; report those minutes with the human time, not as NEXUS latency.

## Threat model

| Threat | What the attacker gets | What still holds | Residual |
|---|---|---|---|
| The GitHub token is stolen | Push to the fork; open pull requests upstream (outcome B: also issues and comments on public repositories) | No upstream write, no merge. Every change to desired state still needs the owner's merge | Spam and misleading pull requests. **Accepted limit 1** |
| A bad Reasoner names a commit | A candidate SHA | Checks 1–6: only a real, recent, single-parent commit on the tracked branch that touches the overlay can be reverted | It can ask for the revert of a **good** recent overlay commit. A human reviews and merges. **Accepted limit 2** |
| Reasoner text in the pull request | Nothing: none is copied | Template fields only | None found |
| A crafted commit subject on the tracked branch | Sanitised text in a code span | Whoever can commit to the tracked branch already controls desired state | None added |
| A compromised bot pod | The token, so everything in row 1; and it can push any content to the fork and open a pull request that is **not** a revert | The owner's review of the diff; CI on `pull_request` gives fork runs no secrets | The body's claim "mechanical revert" is only as good as the bot. The reviewer checks the diff. **Accepted limit 3** |
| Fork pull request runs CI | Arbitrary code from the fork in a workflow run | `pull_request` runs from forks get a read-only token and no secrets. Keep "require approval for fork pull request workflows" on, and never add `pull_request_target` | CI minutes |
| The pull request is merged by mistake | A cluster change through ArgoCD | That merge is the control: rule 11, a full SHA, `--match-head-commit` | By design |
| A compromised operator | Can end an Incident `Escalated / git_revert` at will, so it can cause pull requests to be opened | Decision 11 caps it at one open pull request per branch; checks 1–6 limit what each can revert | A channel outside the four-action envelope, ending at a human. Stated as such |

## Stated limits

1. **The token is a credential to the outside world held in the cluster.** It cannot change desired state.
   With the classic token (outcome B) its reach is wider than this repository.
2. **NEXUS can propose reverting the wrong commit.** The checks guarantee the commit is real, recent and
   touches the right overlay; they do not guarantee it is the cause.
3. **The pull request is a second output channel besides the four catalogue actions.** It ends at a human
   merge. The thesis claim is about cluster mutations and is unchanged; the channel is named in the trust model.
4. **`main` is mapped but unused.** At level 1, S3 ends `Recommended`, not `Escalated` (spec:1801), and
   `nexus-prod` is at level 1. The `main` mapping is the first thing to cut (`TASKS.md` Block 6).
5. **The bot pod now holds this token and the approver impersonation right together** (ADR-040 limit 6). Two
   Deployments with two ServiceAccounts would separate them; that split is not planned before the defense.
6. **Merge recognition is a chat message, not a record in the Flight Recorder.** The export reads the times
   from GitHub.

## Contradictions with the spec and the sources

Reported, not adapted silently. The rows that change the spec are in the spec v1.1 list as X13
(`docs/plans/pfe-replan.md`). Rows are numbered P1–P6 here, to keep them apart from that list's X numbers.

| # | Source | Says | This ADR | Why |
|---|---|---|---|---|
| P1 | Spec:1005 | The fork token "is mounted into the operator as a file" | Mounted in the bot pod only. **Overrides the spec** (owner, 2026-11-17); spec v1.1 list X13 | Review section 5: in the operator it gives a compromised operator a direct outside channel. The operator's NetworkPolicy already has no public egress (`system.yaml:36-38`) |
| P2 | Spec:1005, spec:1801 | The escalation names the commit that introduced the regression | Nothing structured names it today (`rules.py:64-65`); addition A1 covers the gap | A structured `recommendation.commit` field would be a schema and prompt change before the freeze; not proposed here |
| P3 | Spec §20 | C8 is SHOULD | In scope (D8) | Owner decision |
| P4 | Plan 3.3 | Label `nexus-revert` | No label; found by author and head prefix | The machine account cannot label upstream |
| P5 | Plan 3.3 | Preferred fix: a "require a pull request" ruleset with a bypass | No ruleset change; fork | D15 |
| P6 | Review section 5 | Title and body "carry Reasoner text: treat as untrusted" | They carry none | Simpler than sanitising it |

## Not verified

| # | Item | Settled by |
|---|---|---|
| U1 | Which token type can open the upstream pull request | The token test, steps 4 and 6 |
| U2 | The upstream setting "require approval for fork pull request workflows" and its level | Owner: repository Settings → Actions → General; or `gh api repos/koussayx8/nexus-platform/actions/permissions/fork-pr-contributor-approval` |
| U3 | Whether the ruleset on `experiment/dev-state` blocks a plain push (plan 3.3). No longer decisive: the token has no upstream write | `gh api repos/koussayx8/nexus-platform/rulesets` |
| U4 | The exact paths of the fault class, confidence, arm and model in `status` (`act.py:395` shows `bundleHash`; `act.py:558-564` the recommendation) | Read `act.py` and one `Escalated` Incident from a Block 5 trial run |
| U5 | The S3 injector's commits touch only `overlays/dev` (rule A2) | Read `experiments/` S3 injection code in Block 6 |
| U6 | ArgoCD pickup time after a merge into `experiment/dev-state` | Measured in the Block 6 end-to-end run |
| U7 | The required check on `main` runs on a fork pull request without secrets and can pass | The first pull request against `main`, or cut the mapping |

## Build checklist — Block 6, 10 hours (+2 moved from Block 7)

Block 6 comes before the bot core, so it creates the bot package and its pod **without** the impersonation
ClusterRole and without Telegram: it watches Incidents, opens the pull request and writes to its log. Block 7
adds the rest (ADR-040). The package and image step (2 hours) is ADR-040's step 1, done here.

| Step | Files | Tests (offline unless marked) | Hours |
|---|---|---|---|
| 0. Token test | This ADR: the outcome and the HTTP statuses | The test itself (owner) | 0.2 |
| 1. Package, image, pod | `bot/` skeleton, `bot/Dockerfile` (with `git`), `.github/workflows/bot.yml`, `bot/k8s/` (namespace, ServiceAccount, Deployment, ConfigMap with the branch map), `platform/rbac/nexus-bot.yaml` (the read Role on Incidents only), `platform/network/bot.yaml`, the Argo CD Application, the AppProject destination | `kustomize build`; CI green | 2 (from Block 7) |
| 2. Trigger | `bot/nexus_bot/watch.py`: `Escalated` + `escalate` + `git_revert` + namespace in the map | Other phases, kinds and namespaces trigger nothing; one attempt per Incident | 1 |
| 3. Commit validation | `bot/nexus_bot/revert.py` | A fixture repository built in the test: SHA not 40 hex; SHA on another branch; older than depth; merge commit; outside the overlay; overlay plus another path (A2); no SHA named → the newest overlay commit (A1); already reverted; conflict. Each → no push, the named check | 2 |
| 4. Revert and push | `bot/nexus_bot/revert.py`, `bot/nexus_bot/gitcmd.py` (hooks off, askpass) | Fixture "fork" remote: only `nexus/revert-<incident>` is pushed; the diff equals the inverse of the candidate's; the token is in no `argv`, URL or log line | 2 |
| 5. Pull request | `bot/nexus_bot/github.py`: create, find by author and head prefix, get | Fake API: body equals the template for a fixed Incident; the "Chosen by" line is the A1 line when the Reasoner named no SHA and the Reasoner line otherwise; no Reasoner free text; `maintainer_can_modify` false; `draft` false; no merge or auto-merge call exists in the module (a test greps for it); second Incident on the same branch → no second pull request | 2 |
| 6. Merge recognition | `bot/nexus_bot/prwatch.py` | Fake API: merged, closed, timeout, restart recovery | 1 |
| 7. Secret script | `scripts/nexus-bot-secrets.sh` (GitHub token; the Telegram token is added in Block 7) | Stub `kubectl`; the value never in output | 0.5 |
| 8. Export | `scripts/export-results.py`: C8 times from the pull request | Fixture JSON | 0.5 |

**Live gate (owner approval needed):** S3 on `nexus-dev` ends `Escalated`; a pull request appears against
`experiment/dev-state` from the fork; its diff is the inverse of the injected commit; the owner merges it under
rule 11; ArgoCD syncs; the alert resolves; the log (the chat after Block 7) shows "merged by". One refusal: an
Incident whose named SHA is not on the branch opens no pull request.

**Owner-only:** the machine account, its fork and tokens; the fork-workflow approval setting (U2); running the
Secret script; merging the revert pull request; deleting test branches in the fork.

**Cut first:** the `main` mapping; then the export step (times can be read by hand for three C8 runs).

## Consequences

- S3 ends with a reviewable pull request one tap away from the fix, and NEXUS still cannot change desired
  state: the fix is a human merge of a diff that NEXUS proposed.
- The operator's trust envelope is unchanged: no token, no public egress.
- One more outside credential lives in the cluster, in the bot pod, with no write to this repository.
- The spec needs addenda in v1.1: §13 (the bot proposes through Git, a human merges; the token is not in the
  operator), §19 (no ruleset change; the fork-workflow approval setting), §20 (C8 in scope), §24 (the threat
  rows above).
