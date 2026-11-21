# ADR-048: Spec v1.1

## Status: Proposed, 2026-11-20 (owner's overnight list, item 2; G17). Accepted when #203 merges. Before the `v0.6.0` freeze.

## Context
`docs/architecture/final-spec.md` section 0: the architecture "changes only by ADR plus a version bump of this
document". `TASKS.md` says the same for G17 ("Spec v1.1 (ADR plus version bump)") and puts spec v1.1 in Block 9:
"spec v1.1 (#203, amended for D3, D8, D9 and the L1 scenario item in Later)".

Since version 1.0 (2026-09-24), the decisions have piled up in places the spec does not read:
- the re-plan's contradiction list, X1–X12 on `dev` and X13–X14 on #237 (`docs/plans/pfe-replan.md`);
- the `[M2-4 → G17]` and `[ADR-039 → G17]` items in `TASKS.md`, and the owner's decisions D1–D24 of 2026-11-12;
- ADR-035 to ADR-045, several of which say "amends spec §… until spec v1.1".

#203's draft (2026-11-10) recorded X1–X11 only. It was 223 commits behind `dev`; `origin/dev` is merged into it
(no rebase), and this ADR's change completes it.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | **The spec becomes version 1.1.** The header names the version and this ADR; section 0's "Changes in version 1.1" table lists every change, with its section and its source (an ADR, a `TASKS.md` decision, or a merged pull request). | The rule of section 0. One place a reader can check what changed since 24 September. |
| 2 | **Version 1.1 decides nothing new.** It records what an ADR, the owner or a merged pull request already decided. In three places it records what the code does, because the owner asked for it: at L1 a proposed `escalate` ends `Escalated` (`act.py`; TASKS.md "the spec's scenario table disagrees with the operator at L1"); an L0 Incident makes no Reasoner call, no mutation and no Flight Recorder row (`reconcile.py`, `act.py`), so section 14 says the Flight Recorder holds L1 and above; and `nexusctl`'s exit codes are the ones `exitcodes.py` and `commands/` return. | A spec that disagrees with the code is not frozen; it is wrong. |
| 3 | **Owner decisions override the draft's rows.** D3 removes the Haiku arm the draft added (X1). D10 cuts the effort sweep (X2) and the A1 demo host (X4, X5), and the demo runs on the WSL2 laptop with a backup video. The rows stay in the table, marked superseded. | The draft predates the owner's decisions of 2026-11-12. |
| 4 | **Pending ADRs are named, not anticipated.** ADR-040 (the bot) and ADR-041 (C8) are draft pull requests (#237). Version 1.1 states the decisions they implement (D8, D9, X13, X14) and names #237. The detail (the bot's namespace, its impersonation grants, `spec.source.note`, C8's commit rule) enters sections 3, 11, 13 and 18 when those ADRs merge, by a version 1.2 or an amendment of this ADR. | Writing a draft's detail as frozen text would freeze what the owner has not accepted. |
| 5 | **D10, D11, D15, D16, D25 and D26 are decided** (owner, 2026-11-12; D25 and D26 on 2026-11-21) and folded in: 5 valid runs per cell; the live matrix is S1, S3, S5 and S6 at L3 with R1 and R3; S2 and S4 not measured live; R2 replayed only; C5, the detector comparison (G14), the effort sweep and the A1 host cut; C4, the static baseline, cut (D25: no `nexus-baseline`, G10 cut); S4 and S6 at the calibrated load, Locust total 170 users, `nexus-dev` 90 req/s against its baseline 80 (D26); C8's fork and machine account; the replay's real bundles. ADR-046 (#282) and ADR-047 (#283) are folded in where the spec describes the runbooks, the runner and the protocol. The pre-registration file is `experiments/ground-truth.json`, the JSON the Evaluation Harness reads. | They are decisions already taken; leaving them out would leave the spec wrong. |
| 6 | **What is not changed here** is listed in section 0, table D: the attempt cap and the pause-gap threshold (the pre-registration's, #234, G16). | Each needs the owner's decision or the pre-registration. |

## What version 1.1 records (summary; section 0 has every row)
- **Detection and verification:** the 3-minute baseline lag and the in-flight signal (X8); `restart_v2` with a
  300 s Verifying timeout (X12).
- **Evidence:** `evidence/v1.1`, then `evidence/v1.2` (pod spec, rollout diff, other firing alerts); logs from the
  last 15 minutes (#270).
- **Retrieval:** the Decision expansion (ADR-043), direction-aware metric words, the runbook polish (D23).
- **Reasoner:** per-configuration timeouts at most 300 s, the operator's + 5 s and Diagnosing's + 30 s (330 s), one
  retry for a transient failure; named NIM configurations with one backup per arm, chosen by the bake-off;
  `prompt/v3` (16,000-token budget, 40,000-token guard, the reference wording); the reasoning trace stored as
  untrusted text; the escalation's `suggestedChange` and `runbookGap`, never applied.
- **Operator and Gate:** the L2 reading of stage 7 and the compensation limit (ADR-038); `status.gate.detail` (#269).
- **Humans:** `nexusctl`'s impersonation options and exit codes; the approval bot (pending ADR-040); C8 in scope
  (pending ADR-041).
- **Evaluation:** the L1 terminal phases of S3 and S5; no Haiku arm, no effort sweep; 5 valid runs per cell on S1,
  S3, S5 and S6, live at L3 with R1 and R3, R2 replayed only (D10, D11); C4 cut (D25); C5 and the detector
  comparison cut; S4 and S6 at the calibrated load, Locust total 170 users (D26); the replay on real bundles (D16); the runner's rules (ADR-047); the bake-off as the model selection method, with the
  round-3 truth corrections.
- **The Flight Recorder:** L1 and above; an L0 Incident writes no row.

## Consequences
- `CLAUDE.md` now says "version 1.1, frozen" and points here; it changes in this same pull request.
- The next spec change is version 1.2, by its own ADR.
- Runs are compared only within one prompt version and one evidence version (ADR-042, ADR-044, ADR-045); the
  spec now says which versions are frozen: `prompt/v3` and `evidence/v1.2`.
