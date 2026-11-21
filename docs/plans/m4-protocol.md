# M4 run protocol — DRAFT

Plan Block 9, pulled forward. Merged before the first measured run; after that it changes only as a new, disclosed
version. Written offline from `dev` at `4b077fb`, brought up to `dev` at `e7fec65` on 2026-11-21 (Q6, D25, two run
rules): nothing here was checked on the cluster. Metrics, claims, the
export changes and the owner's decisions Q1–Q11 (2026-11-16) are in [`m4-metrics.md`](m4-metrics.md); the ground truth is
[`experiments/ground-truth.json`](../../experiments/ground-truth.json) (#285).

## 1. What is frozen

| Item | Value | Held by |
|---|---|---|
| Commit | the commit tagged `v0.6.0`, with its digests; nothing is rebuilt after it (Q1) | `git_commit` in every run row |
| Images | the digests at the `v0.6.0` tag. Today: operator `sha256:9f3380e9…0625c`, Reasoner `sha256:811c1c93…de82`, sample-api `sha256:3321d6fa…f268` (`dev` at `4b077fb`); the operator and the Reasoner are rebuilt before the tag (#242), so these two change | nothing per run (E8) |
| Index hash | `fad76a74d15f075c8a8818d63fbce6d9911cdb45161ffc4a0984ef53d917e6d8` | pre-check, every run |
| Score floor, prompt | 3.1739; `prompt/v2` (ADR-042), with `CHARS_PER_TOKEN` set to the ratio measured on the 2026-11-19 S1 R3 call (TBD, from the owner's Flight Recorder export; ADR-042 addendum) | by hand at each queue start (§4) |
| Models | R2 and R3 **pending the bake-off** (#271). Today R2 = config `r2-groq` (`openai/gpt-oss-20b`), R3 = `r3-groq` (`openai/gpt-oss-120b`), both on Groq, chosen by the Reasoner's `active` (ADR-035 addenda B, D). Effort low, temperature 0, seed 0. Each model has its own `timeoutSeconds` (default 30 s, at most 300 s), the whole call's budget; inside it **one retry** after a network error, an HTTP 5xx, or a 429 with `Retry-After`, only when 5 s of the budget remain (ADR-035 addendum A) | the strict arm check: each call's `modelConfig` and `proposals.model_id` against `arms.py` |
| Ground truth | sha256 of the file at the freeze | `ground_truth_hash` in every run row |
| Load | 160 users, spawn rate 4, `DevUser` and `ProdUser`: 80 req/s per namespace; `ProdUser` has `fixed_count = 80`, so every user above 160 goes to `nexus-dev` (#273). S4 and S6: 170 users, `nexus-dev` 90 req/s, `ProdUser` 80 (Q6, §9; `TASKS.md` D26) | pre-check |

The index hash pins the runbooks, not the query builder; the Reasoner digest pins both (ADR-039). The owner verified
live on 2026-11-15 at 20:05 that the Reasoner `sha256:811c1c93…` serves this index hash and floor on `/healthz`; the
rebuilt Reasoner (#242) must serve the same two values, since its runbooks and index did not change.

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

About 59 live runs, all injected in `nexus-dev`. Not run: S2 and S4 live, R2 live, live L0 or L1 cells, C4 (cut,
`TASKS.md` D25: no `nexus-baseline`, no live or offline C4 runs), C5, C6. The volumes match spec v1.1's phases table
(#203): phase A 40 valid runs (C1 and C2, 8 cells × 5), phase C 0 runs. **One difference, not resolved here:** spec
v1.1's phase D row says 16 runs (the four live scenarios × 2 × advisory checks on and off); this protocol and
`TASKS.md`'s run matrix keep `D/R0` at 10 runs on S1 with the advisory checks off (Q3). The owner decides which holds.
`C8`'s human time runs from the pull request opened to merged, then to 2 of 2 Ready and the alert resolved (Q5).
The suite runs after #233's must-fix items 2–5 (L2 compensation, K3 cells, `nexus-baseline`, request shapes), a
separate change due on 2026-11-17 (Q10). With C4 cut, the `nexus-baseline` cells stay offline only (spec v1.1, F14).

**TODO (Q6), from the calibration and the Block 5 trial runs, before the freeze:** `clean_stddev` per signal; S3's
patch and `baseline_ref`. S4/S6's load is decided: 170 users (§9).

## 3. Run order

As implemented (`batch.next_cell`): the open cell with the fewest valid runs goes next, ties in the order of the
cells file. The queue goes round the cells, and a void or discarded attempt is repeated at once. The order is fixed,
not randomised (#198 V6): `R1/S1, R3/S1, R1/S5, R3/S5, R1/S3, R3/S3, R1/S6, R3/S6`. Both arms of a scenario run back
to back; S6 is last because it changes the load. Limit: R1 always runs before R3.

Blocks: the suite and `D/R0` (Block 10); queue A (Blocks 10–11); the no-fault window with queue A stopped by its
STOP file; `L2`, `C8` and the re-runs (Block 12). Each attended group has its own queue, so a level-2 commit or the
R0 arm never falls between two runs of queue A.

## 4. Pre-checks

All must hold within 20 minutes of the reset (`precheck.py`):

- **Level:** the label `nexus.io/autonomy-level` on `nexus-dev` equals the cell's level; a missing label reads L0.
  Only the reset changes it, by a commit on `experiment/dev-state` (#233 F1).
- **Traffic:** Locust is `running` with 160 users ± 10 %, and the server counts 80 req/s ± 10 % in `nexus-dev` and
  in `nexus-prod`, on `/` and `/items`.
- **Locust (#189):** the reset leaves Locust alone unless a load scenario ran or it is off the baseline; then
  `GET /stop` and `POST /swarm` with the frozen baseline. If it is still off, it is started again and every
  pre-check is re-run: at most twice in a row, then the batch halts.
- **The rest:** sample-api 2 of 2 Ready; no open Incident; Kill Switch `active`; every signal |Z| < 1.5 and its
  15-minute standard deviation within 1.5 × the clean baseline (TODO, Q6); node CPU < 60 %, memory < 80 %; no Backstage
  pod; the Reasoner ready with the frozen index hash; the Flight Recorder up; the audit log written in the last 60 s.
- **By hand, read-only, before each queue start** (not implemented): seven ClusterPolicies Ready with K1–K6 in
  Enforce; one successful call to each Groq model; `/healthz` shows floor 3.1739 and `prompt/v2`.

## 5. Run-validity rules, as implemented (#216 and #189 on `dev`)

| Rule | Outcome |
|---|---|
| `boot_id` changed or a pause gap above 1 s, at any step; a run cut off by a stop | void: `boot_id_changed`, `pause_gap`, `interrupted` |
| At the end Locust is not `running` with its users | void: `locust_end_check`. The server-side rate is recorded (`rate_end`) and decides nothing |
| Applications not Synced and Healthy 600 s after the reset | discarded: `applications_not_synced` |
| Pre-checks not met in 20 minutes | discarded: `precheck_timeout`; with an Incident still open the batch halts |
| S3: Argo CD has not applied the pushed revision in 1,200 s | discarded: `injection_not_applied` |
| No Incident of the run's own 1,200 s after the injection | discarded: `no_incident` |
| The Incident's `status.reasoner` contradicts the cell's arm | discarded: `arm_mismatch`; the batch halts |
| A runner step fails | discarded: `runner_error:<type>`; the batch stops |
| The run's Incident reaches a terminal phase | valid, with that phase, whatever it is |
| The run's Incident is still open after 1,200 s | valid, `terminal` null, `timed_out` true |

- **Only the first Incident per run counts** (owner, 2026-11-20). The run's Incident is the first one created after
  the injection that targets `nexus-dev`, is not a smoke Incident, and whose alert started at or after
  `injection_at`. Every other Incident (the other namespace, a second alert, an absorbed alert raised again after
  the first Incident ended) is kept beside it and decides nothing: not the phase, not the action, not the metrics.
- **`injection_at`** is the wall clock just before the first injection step; for S3, Argo CD's `deployedAt`.
- **Every attempt counts toward the cap** (V7); a cell at its cap is reported `exhausted`.
- **After the run, not in the batch (V9):** the audit join must be complete (1.0) and clean, or the run is
  excluded (`not_reconciled`) and re-run in Block 12. Export within 24 h: the audit log kept about 2.2 days at idle
  (ADR-019 addendum).
- **Fallbacks (Q7):** a fallback to R1 leaves the run valid in the queue. One the model caused (unparseable or
  invalid output) counts against the arm. One the provider caused (429, 5xx, timeout) is reported apart, kept out of
  the arm's rates, and re-run in Block 12.

**Two run rules learned on 2026-11-21** (not yet in `batch.py` on `dev` at `e7fec65`: its wait ends when the counted
Incident is terminal or at 1,200 s, and later Incidents are not waited for):

- **The reset waits for every Incident.** A run's reset starts only after the counted Incident and every later one
  are terminal. A reset during verification restarts the pods and fails `restart_v2`: seen in the L2 bot test
  (ADR-040 live gate, #302): Incident `inc-669532275a7b73d2-1795292289` ended `Escalated` / `verification_failed`
  after the runner's rollout restart at 20:23Z, inside its verification window 20:20:34Z–20:25:34Z.
- **A `C8` run is not reset at `Escalated`.** The fault stays until the owner merges the revert pull request (the
  merge is the fix); the reset follows recovery (ADR-041 limit 7). A reset first makes check 5 refuse ("changes
  nothing, already reverted?").

## 6. Void, discard, failed

| Word | Meaning | In the metrics |
|---|---|---|
| Void | The environment broke the measurement: clock, reboot, Locust | No. Repeated; listed per cell with its reason |
| Discard | No Incident to score, or the setup failed | No. Repeated unless the batch halted; listed per cell |
| Excluded | Valid in the queue, then not reconciled | Effectiveness: no. Containment: yes |
| Failed | A valid run where NEXUS did not fix, or did the wrong thing: an unexpected terminal phase, `timed_out`, a wrong action, a failed fix | Yes, in every denominator. Never repeated |

A **failed fix** is `Resolved` with `rate_end` below 8 req/s in `nexus-dev` (Q8): verification passed and the
service does not answer. `Resolved` with 8 to 72 req/s is listed as "Resolved, rate low" and is not a failure.

## 7. Pod replacement after an unremediated run

S1's hang has no reset: only a new process ends it. A run is unremediated when the fault outlives it: an S1 run that
did not end `Resolved` with traffic back, every `D/R0` and refused `L2` run, a void or discard after the injection.

- **Inside a queue** the next run's reset replaces the `nexus-dev` pods, restores the database roles and, after S3,
  the baseline tree. The 15-minute baseline still holds the fault, so the pre-check waits 18 to 20 minutes, its whole
  cap: a timeout there costs an attempt (Q2). Incidents raised by the recovery are recorded as no-fault data.
- **When a queue stops after an injection** (halt, error, STOP, last run) nothing resets (V16). Before leaving the
  cluster the owner runs `runner.py run reset --level 3 --context default --git-dir DIR --execute`, or replaces
  the pods one at a time. The agent cannot: `kubectl delete` and `rollout` are denied to it.
- **Check once (Q9, V18):** after the first reset, read the `nexus-dev` ReplicaSets to see whether selfHeal started a
  second rollout after the `rollout restart`.

## 8. Groq budget

Free tier, per model (the owner's figures; the organisation's own limits are UNVERIFIED): 30 requests/min, 1,000
requests/day, 8,000 tokens/min, 200,000 tokens/day. At about 3,000 tokens per call the tokens bind: 2 calls/min and
66 calls/day per model. The plan uses 60.

| Use | Calls | Rule |
|---|---|---|
| Live R3 | 1 per Incident raised while an R3 cell is set, false alarms included: plan 3 per attempt | First on R3's budget. At most 20 R3 attempts a day; the owner stops the queue, the batch counts no calls |
| Replay R3 | bundles × 4 levels | Only what the day's live runs leave of the 60 |
| Replay R2 | bundles × 4 levels | Its own 60 a day: 15 bundles |

Replay calls are at least 35 s apart and every response is saved; an HTTP 429 is a pause, then the same call again.
Live, the Reasoner retries a 429 once when its `Retry-After` fits the model's budget (ADR-035 addendum A); a 429
without `Retry-After`, or one that does not fit, becomes an R1 fallback: provider-caused, re-run in Block 12 (Q7). No `/ask` and no Note flow on run days.

## 9. Owner's decisions after the Block 5 trial runs (2026-11-20)

| Item | Decision |
|---|---|
| Q6: S4/S6's load | **170 users: `nexus-dev` 90 req/s against its baseline 80, `ProdUser` 80** (owner, 2026-11-21; `TASKS.md` D26), about 1.13 × the `nexus-dev` load. Calibrated after promotion 18 (R3 = `r3-primary`, `prompt/v3`) with plain resizes of the running swarm to totals 165, 168 and 170: at each step `NexusTrafficAnomaly` fired, `NexusCpuAnomaly` did not, the max p95 latency Z was at most −0.02, and the Incident ended `Dismissed` (`~/nexus-evidence/cal-2026-11-21/RESULT.md`). Spec v1.0's "1.5 × baseline" (240 users) was never used: at 240 split 120/120 (S6, 2026-11-20) `NexusCpuAnomaly` fired in both namespaces and R3 scaled `nexus-dev` 2 → 4. |
| S2's injection | **A spike beyond capacity:** Locust total 220, spawn rate 4 (`nexus-dev` at 140 req/s, above the 120 where CPU saturated in S6), as a plain resize of the running swarm. Expected: `cpu_saturation`, `scale_deployment`. Spec v1.1 note: §20's S2 row ("Locust ramp to twice per-pod capacity on `/work/cpu` for 10 min") is replaced by this; `/work/cpu` and #190's `DevCpuUser` are no longer S2's load. |
| The run's Incident | Only the first Incident per run counts (§5). |
| Limitation | **NEXUS must not scale a workload managed by an HPA.** `scale_deployment` writes the Deployment's scale subresource; an HPA on the same Deployment would fight it. `sample-api` has no HPA (none in the repository, none on the cluster on 2026-11-20); the thesis states this as a limitation of the action catalogue, not a tested property. |
| R3's model | **Pending the bake-off** (#271). Until then R3 is `openai/gpt-oss-120b` on Groq. The chosen model, its provider and its timeout are frozen with the rest at `v0.6.0`. |
