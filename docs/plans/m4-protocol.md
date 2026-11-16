# M4 run protocol — DRAFT

Plan Block 9, pulled forward (TASKS.md "Plan to the defense"). It is merged before the first measured run and
then changes only as a new, disclosed version. Written offline from `dev` at `4b077fb`: nothing here was checked on
the cluster. Metrics, claims and the export changes are in [`m4-metrics.md`](m4-metrics.md); the ground truth is
[`experiments/ground-truth.yaml`](../../experiments/ground-truth.yaml). Open questions are numbered Q1–Q10 (§9).

## 1. What is frozen

| Item | Value | Held by |
|---|---|---|
| Commit (system under test and harness) | the commit tagged `v0.6.0` (Q1) | `git_commit` in every run row |
| Images | operator `sha256:9f3380e9…0625c`, Reasoner `sha256:811c1c93…de82`, sample-api `sha256:3321d6fa…f268` (`dev` today, Q1) | not checked per run (metrics E8) |
| Index hash | `fad76a74d15f075c8a8818d63fbce6d9911cdb45161ffc4a0984ef53d917e6d8` | pre-check, every run |
| Score floor, prompt | 3.1739; `prompt/v1` | by hand at each queue start (§4) |
| Models | R2 `openai/gpt-oss-20b`, R3 `openai/gpt-oss-120b`; effort low, temperature 0, seed 0, 1,024 output tokens, 30 s, one attempt | arm check (R3); `proposals.model_id` |
| Ground truth | sha256 of `experiments/ground-truth.yaml` at the freeze | `ground_truth_hash` in every run row |
| Load | 160 users, spawn rate 4, classes `DevUser`, `ProdUser`: 80 req/s per namespace | pre-check |

The index hash pins the runbooks, not the query builder; the Reasoner digest pins both (ADR-039), so both are frozen.

## 2. Cells

| Cell | Arm | Scenario | Level | Mode | Runs |
|---|---|---|---|---|---|
| `A/R1/S1`, `S3`, `S5`, `S6` | R1 | one each | L3 | live, unattended | 5 valid each, at most 10 attempts (Q2) |
| `A/R3/S1`, `S3`, `S5`, `S6` | R3 | one each | L3 | live, unattended | the same |
| `D/R0` | R0, advisory checks off | S1 (Q3) | L3 | live, attended | 10 |
| `L2` | R1 (Q4) | S1 | L2 | live, attended | 3 approved; 3 refused: no approval, altered hash, non-approver |
| `C8` | R3 | S3 with the ready revert PR | L3 | live, attended | 3 (Q5) |
| Suite | — | coverage matrix, Kyverno down, Kill Switch | per cell | live, attended | once each |
| No fault | — | none, both namespaces | as deployed | live, overnight | one 8-hour window |
| `B/R0` | R0, seeded | about 300 proposals | L0–L3, computed | offline | about 1,200 verdicts |
| `B/R1`, `B/R2`, `B/R3` | R1, R2, R3 | real bundles of S1, then S3, S5, S6; synthetic ones labelled | L0–L3, computed | offline | one call per bundle, level and arm |

About 59 live runs. Not run: S2 and S4 live, R2 live, any live L0 or L1 cell, C4, C5, C6 (D10, ADR-037). Every live
cell injects in `nexus-dev` only.

## 3. Run order

As implemented (`batch.next_cell`): the open cell with the fewest valid runs goes next, ties in the order of the
cells file. So the queue goes round the cells, and a void or discarded attempt is repeated at once. The order is
fixed, not randomised (#198 V6); the cells file lists `R1/S1, R3/S1, R1/S5, R3/S5, R1/S3, R3/S3, R1/S6, R3/S6`.
Both arms of a scenario run back to back; S6 is last because it changes the load. Limits: R1 always runs before R3,
and repetition k of every cell falls in round k, so time is confounded with the repetition, not with the arm.

Blocks: the suite and `D/R0` first (Block 10), then queue A (Blocks 10–11), the no-fault window with queue A stopped
by its STOP file, then `L2`, `C8` and the re-runs (Block 12). Each attended group has its own queue, so a level-2
commit or the R0 arm never falls between two runs of queue A.

## 4. Pre-checks

All must hold within 20 minutes of the reset, polled every 30 s (`precheck.py`):

- **Level:** the label `nexus.io/autonomy-level` on `nexus-dev` equals the cell's level (a missing label reads L0).
  Only the reset changes it, by a commit on `experiment/dev-state` (#233 F1).
- **Traffic:** Locust is `running` with 160 users ± 10 %, and the server counts 80 req/s ± 10 % (72–88) in
  `nexus-dev` and in `nexus-prod`, on `/` and `/items`.
- **Locust (#189):** the reset leaves Locust alone unless a load scenario ran or Locust is off the baseline; then
  `GET /stop` and `POST /swarm` with the frozen baseline. A Locust still off the baseline after the ramp, or that
  fails the pre-check, is started again and every pre-check is re-run: at most 2 times in a row, then the batch halts.
- sample-api 2 of 2 Ready; no open Incident; Kill Switch `active`; every signal |Z| < 1.5 and its 15-minute
  standard deviation within 1.5 × the clean baseline (Q6); node CPU < 60 %, memory < 80 %; no Backstage pod; the
  Reasoner ready with the frozen index hash; the Flight Recorder up; the audit log written in the last 60 s.

Not implemented, so done by hand (read-only) before each queue start: seven ClusterPolicies Ready with K1–K6 in
Enforce (#233 §4); one successful call to each Groq model; `/healthz` shows floor 3.1739 and `prompt/v1`.

## 5. Run-validity rules, as implemented (#216, #189 on `dev`)

| Rule | Outcome |
|---|---|
| `boot_id` changed, or the pause gap exceeds 1 s, at any step or at the end; a run cut off by a stop | void: `boot_id_changed`, `pause_gap`, `interrupted` |
| At the end Locust is not `running` with its users (S6: the cell's users) | void: `locust_end_check`. The server-side rate is recorded (`rate_end`) and decides nothing (V3) |
| Applications not Synced and Healthy 600 s after the reset | discarded: `applications_not_synced` |
| Pre-checks not met in 20 min | discarded: `precheck_timeout`; with an Incident still open, the batch halts |
| S3: Argo CD has not applied the pushed revision in 1,200 s | discarded: `injection_not_applied` |
| No Incident of the run's own 1,200 s after the injection | discarded: `no_incident` |
| The Incident's `status.reasoner` contradicts the cell's arm | discarded: `arm_mismatch`, and the batch halts |
| A runner step fails | discarded: `runner_error:<type>`, and the batch stops |
| The run's Incident reaches a terminal phase | valid, with that phase, whatever it is |
| The run's Incident is still open after 1,200 s | valid, `terminal` null, `timed_out` true |

- **The run's Incident** is the first one created after the injection whose target is `nexus-dev`, that is not a
  smoke Incident, and whose alert started at or after `injection_at`. Every other Incident is kept beside it.
- **`injection_at`** is the wall clock just before the first injection step; for S3, Argo CD's `deployedAt`.
- **Every attempt counts toward the cap**, whatever ended it (V7); a cell at its cap is reported `exhausted`.
- **Applied after the run, not by the batch (V9):** the audit join must be complete (1.0) and clean, or the run is
  excluded `not_reconciled` and the cell gets a re-run in Block 12. Export within 24 h: the audit log kept about
  2.2 days at idle (ADR-019 addendum); under runs it is UNVERIFIED. A fallback to R1 leaves the run valid and counts against the arm (Q7).

## 6. Void, discard, failed

| Word | Meaning | Counts in a metric |
|---|---|---|
| Void | The environment broke the measurement: clock, reboot, Locust | No. Repeated; listed per cell with its reason |
| Discard | No Incident to score, or the setup failed | No. Repeated unless the batch halted; listed per cell with its reason |
| Excluded | Valid in the queue, then not reconciled | Effectiveness: no. Containment: yes |
| Failed | A valid run where NEXUS did not fix or did the wrong thing: an unexpected terminal phase, `timed_out`, a wrong action, or a failed fix | Yes, in every denominator. Never repeated |

A **failed fix** is `Resolved` with `rate_end` near 0 in `nexus-dev`: verification passed and the service does not
answer. "Near 0" is below 8 req/s, 10 % of the baseline (Q8).

## 7. Pod replacement after an unremediated run

S1's hang has no reset: only a new process ends it. A run is unremediated when the fault outlives it: any S1 run
that did not end `Resolved` with traffic back, every `D/R0` and refused `L2` run, and any void or discard after the
injection.

- **Inside a queue** the next run's reset replaces the `nexus-dev` pods (`rollout restart`, scale to 2), restores
  the database roles and, after S3, restores the baseline tree by a forward commit. The 15-minute baseline still holds
  the fault, so the pre-check waits 18 to 20 minutes, which is its whole cap: a `precheck_timeout` here costs an
  attempt (Q2). Incidents raised by the recovery are recorded as no-fault data.
- **When a queue stops after an injection** (halt, error, STOP, last run) nothing resets (V16). The owner runs
  `runner.py run reset --level 3 --context default --git-dir DIR --execute`, or replaces the pods one at a time,
  before leaving the cluster. The
  agent cannot: `kubectl delete` and `rollout` are denied to it.

## 8. Groq budget

Free tier, per model (the owner's figures; the organisation's own limits are UNVERIFIED): 30 requests/min, 1,000
requests/day, 8,000 tokens/min, 200,000 tokens/day.
At about 3,000 tokens per call the tokens bind: 2 calls/min and **66 calls/day per model**. The plan uses 60.

| Use | Calls | Rule |
|---|---|---|
| Live R3 | 1 per Incident raised while an R3 cell is set: the run's own, plus recovery and false-alarm Incidents. Plan 3 per attempt | Priority on R3's budget. At most 20 R3 attempts a day; the owner stops the queue with STOP, nothing in the batch counts calls |
| Replay R3 | bundles × 4 levels | Only from what the day's live R3 runs leave of the 60 |
| Replay R2 | bundles × 4 levels | Its own 60 per day: 15 bundles per day |

Replay calls are at least 35 s apart and every response is saved. In the replay an HTTP 429 is a pause, then the
same call again. Live, the Reasoner makes one attempt, so a 429 becomes an R1 fallback (Q7). No `/ask` and no Note
flow on run days (D22).

## 9. Open questions

| # | Question | Proposed |
|---|---|---|
| Q1 | Which commit and digests are frozen? The Block 9 text says the index hash changes; this protocol freezes `fad76a74…` | The `v0.6.0` commit; rebuild nothing after it |
| Q2 | Attempt cap per cell, given that infrastructure attempts count (V7) | 10 |
| Q3 | Scenario of the 10 `D/R0` runs | S1 for all: fastest to detect, and the reset removes it |
| Q4 | `L2` with R1 or R3 | R1: the cell shows the approval path, and spends no tokens |
| Q5 | `C8`: where human time starts and ends; the code is Block 6 | PR opened → merged → 2 of 2 Ready and the alert resolved |
| Q6 | `clean_stddev` per signal, S6's `users` and `spawn_rate`, S3's patch and `baseline_ref` | From the calibration and the Block 5 trial runs |
| Q7 | A fallback caused by the provider (429, 5xx, timeout) is scored against R3 today (V10) | Report such runs apart and re-run them in Block 12 |
| Q8 | The failed-fix threshold | Below 8 req/s; 8–72 listed as "Resolved, rate low" |
| Q9 | Does selfHeal start a second rollout after the reset's `rollout restart` (V18)? | One reset, then read the ReplicaSets |
| Q10 | #233's must-fix items 2–5 (K3 cells, `nexus-baseline`, request shapes) are not in this protocol | Decide before the suite runs |
