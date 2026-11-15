# ADR-039: Rule 4 Covers a Full Hang (Option A2); Proposals Keyed per Incident; evidence/v1.1

**Status:** Accepted (owner decision, 2026-11-09: option A2)
**Date:** 2026-11-09
**Spec:** §16 (Rules Engine R1, rule 4; `evidence/v1`), §14 (`proposals` key). An owner-approved deviation
from spec v1.0, recorded for spec v1.1 (G17, re-plan X11). `docs/architecture/final-spec.md` changes at G17,
not with this ADR.

## Context

**S1 in nexus-prod ended Escalated** (E1 attempt 2, 2026-11-07). The Incident `5fa4f476`, detected at
09:52:39Z, has a bundle collected at 09:53:01Z. Its Z-scores: CPU −6.77, request rate −62.12, p95 latency
−0.02. R1 rule 4 needs p95 Z > 3. A hang stops requests completing, so the p95 of the requests that do
complete stays flat. Later in the hang no request completes at all, and the p95 and error-ratio series stop
(from 09:53:14Z in nexus-prod, 08:24:09Z in nexus-dev; capture below). R1 fell to rule 6:
`unknown/escalate`.

**The alert fired on in-flight Z, which evidence/v1 does not carry.** `NexusLatencyAnomaly` is p95 Z > 3
OR in-flight Z > 3 (`platform/observability/alerts/nexus-detection.yaml`).

- **In-flight Z at 09:53:01Z in nexus-prod was 80** (Prometheus, read 2026-11-09).
- The sum, 80, is the 80 ProdUser clients, each holding one request that never returns.
- The baseline mean was 0. The stddev is clamped at a floor of 1 (`clamp_min(..., 1)`), so Z equals the sum.

**Under a hang, in-flight Z is bounded by client concurrency.** With a baseline near 0 and the floor at 1,
fewer than 4 waiting clients cannot reach in-flight Z > 3.

**Proposals were keyed by content hash only.** `proposals.proposal_hash` was the primary key, and the hash is
of the proposal content, which carries no Incident uid. An identical proposal for a second Incident hit
`ON CONFLICT DO NOTHING`. That Incident had no row of its own, and the Proposed step read the first
Incident's row: `get_proposal` looked up the hash alone (`recorder/act.py`, called at `act.py:514`).

The Flight Recorder check of 2026-11-09 (`nexus_reader`, SELECT only) found:
- **6** nexus-prod Incidents reached Proposed without a `proposals` row of their own;
- **0** without an `evidence_bundles` row. The bundle carries `incident.uid` (`evidence.py:139-141`), and no
  redaction pattern matches a UUID (`redact.py:18-66`).

| Detected (2026-11-07) | Incident | Alert | Phase | Proposal | Proposal hash | FR row |
|---|---|---|---|---|---|---|
| 07:19:39Z | ce453920 | Traffic | Escalated | unknown/escalate | `sha256:b0b118824…` | has the row |
| 07:20:09Z | 33bcaaa7 | Cpu | Escalated | unknown/escalate | `sha256:b0b118824…` | **lost** |
| 09:52:39Z | 5fa4f476 | Latency | Escalated | unknown/escalate | `sha256:b0b118824…` | **lost** (S1) |
| 11:36:09Z | 49fe844f | Latency | Escalated | unknown/escalate | `sha256:b0b118824…` | **lost** |
| 10:19:39Z | ec0cf29c | Cpu | Recommended | cpu_saturation/scale_deployment | `sha256:4b5bf9abb…` | has the row |
| 10:20:09Z | fc3f0c3d | Latency | Recommended | cpu_saturation/scale_deployment | `sha256:4b5bf9abb…` | **lost** |
| 11:49:39Z | 4afcad4a | Cpu | Recommended | cpu_saturation/scale_deployment | `sha256:4b5bf9abb…` | **lost** |
| 11:49:39Z | 63984170 | Latency | Recommended | cpu_saturation/scale_deployment | `sha256:4b5bf9abb…` | **lost** |

**Each lost Incident's Gate input came from the earliest Incident with the same hash:** 07:19:39Z (`ce453920`)
for `b0b118824…`, and 10:19:39Z (`ec0cf29c`) for `4b5bf9abb…`. The content was identical, so the Gate verdicts
(all `allowed`) are what each Incident's own row would have given. No run is affected: `run_id` is empty on
every 2026-11-07 Incident.

## Decisions

| # | Decision |
| --- | --- |
| 1 | **Rule 4 (A2):** `(p95 Z > 3) OR (in-flight Z > 3 AND request-rate Z < −3)`, with CPU Z ≤ 1, all replicas Ready and no rollout in progress → `restart_workload`, fault class `gray_failure`. A missing in-flight or request-rate Z never satisfies the new branch (`_below`, like `_above` and `_at_most`). Rules 1–3 still come first. The p95 branch and its proposal are unchanged. |
| 2 | **`runbooks/rb-gray-failure.md` v2** (owner, 2026-11-09). Symptoms describes both forms: requests that complete slowly (p95 up), and a full hang (requests pile up in flight, the completed rate drops, p95 can stay flat). Decision gains the matching sentence. Version 1 → 2. |
| 3 | **evidence/v1.1** adds `metrics.zscores.inflight` (`namespace:nexus_sample_api_inflight:zscore15m`). The collector stamps `evidence/v1.1`, and `evidence_bundles.schema_version` records the bundle's own `schema`. The three validators accept v1.1 and still accept v1, which v1.1 only extends: operator `reasoner_client.check_redacted`, harness `reasoner_client.check_redacted`, Reasoner `service.check_bundle`. The reasoner prompt (`prompt/v1`) is unchanged and identical across models. |
| 4 | **`proposals` keyed by `(incident_uid, proposal_hash)`.** The Writer uses `ON CONFLICT (incident_uid, proposal_hash) DO NOTHING`. The reader `get_proposal(incident_uid, proposal_hash)` returns only this Incident's row, and the Proposed step passes the uid. Fresh installs get the composite key from `05-schema.sql`. An existing volume is migrated by `operator/k8s/flight-recorder-db/migrations/039-proposals-incident-key.sql`, which is separate from the init, idempotent (a no-op on the composite key, safe to re-run) and refuses any other key shape. |
| 5 | **The six lost rows are recorded as lost, with no backfill.** Each Incident's status still holds its proposal (`status.proposal`: hash, faultClass, actionId). This table is the record. |
| 6 | **Rollout order (mandatory).** The owner runs the migration **before** any operator image with the scoped reader and writer is deployed, with no Incident in Diagnosing, then deploys that image right away. See the next section. |

## Rollout order: why

| Database | Operator | What happens |
| --- | --- | --- |
| Old key | **New** image | `ON CONFLICT (incident_uid, proposal_hash)` has no matching unique index, so **every** proposal write fails, not only a repeated one. The Incident ends `Blocked / recorder_unavailable` after the 60 s Diagnosing timeout. This is stricter than the brief's expectation that only a repeated proposal would lack its row and end `Escalated / gate_timeout` after 30 s: the scoped reader alone would do that, but the writer fails first. |
| Migrated | Old image | The old `ON CONFLICT (proposal_hash)` has no matching unique index either, so every proposal write fails: `Blocked / recorder_unavailable`. |
| Migrated | New image | Correct. |

Both mixed states fail closed: there is no row and no mutation. The window between the migration and the
new image must therefore be short, with no measured run in it. Spontaneous no-fault Incidents happen (four
on 2026-11-07), and any in that window end Blocked.

Verify the key after the migration with this query (`nexus_reader`, read-only):

```sql
SELECT pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conrelid = 'proposals'::regclass AND contype = 'p';
-- PRIMARY KEY (incident_uid, proposal_hash)
```

## The limit

**The existing p95 branch already restarts** when a dependency answers slowly or times out inside the app's
own timeouts.
- Synthetic **q27** ("dependency timing out": p95 Z 4.0, CPU Z 0.5) does so, given a Ready workload. In
  `experiments/regression/adr-039/result.md` the row is `gray_failure/restart_workload` under both the base and
  the new rules.
- So does sample-api with a blackholed database. `/items` returns 503 after the 2 s connect timeout
  (`apps/sample-api/main.py:22-24`: slot 0.3 s, connect 2 s, statement 0.5 s), so p95 rises while CPU stays
  flat.

**The new in-flight branch adds the case where requests stop completing at all:** a deadlock (S1), or a
dependency that never answers, in an app without timeouts. Such a dependency looks like a hang, so A2 would
propose a restart. sample-api's requests still complete, so the rate condition keeps its database faults off
the new branch.

**Both branches are bounded and escalate.** `restart_v1` evicts one pod at a time within the PDB
(`maxUnavailable: 1`), and the Verifier escalates when the restart does not bring the signal back to its
baseline.

For the **M4 pre-registration review** (G16): q27 and this paragraph are the reference for "a dependency fault
that rule 4 restarts".

## Evidence: R1 regression

`python -m experiments.harness.r1_regression run` compares the rules module at dev `9b66f0c` (base) with the
new one. The result is committed in `experiments/regression/adr-039/`, and a CI test keeps it current. The
corpus is read-only data:
- every 30 s of the 2026-11-07 clean windows (dev 07:40–08:20Z and 08:47–09:50Z; prod 07:40–09:50Z) and S1
  windows (dev 08:21:39–08:24:16Z; prod 09:51:14–09:56:34Z): five Z-scores, the absolute error ratio, and
  desired/ready/updated plus the rollout state from kube-state-metrics;
- the nexus-dev point 07:50:12Z and the nexus-prod point 09:53:01Z;
- the eight Flight Recorder bundles, with the in-flight Z added from Prometheus at their `collectedAt`;
- the 30 retrieval queries, as is and with ready/updated filled;
- an S4 sweep.

| Result | |
|---|---|
| Rows / changed / changed outside S1 | 571 / 14 / **0** |
| Changes | all S1, `unknown/escalate` → `gray_failure/restart_workload`: the prod bundle, the prod 09:53:01Z point, 8 of 11 prod and 4 of 6 dev S1 points |
| Base reproduces the eight recorded FR proposals | yes (0 mismatches) |
| 2026-11-07T07:50:12Z nexus-dev burst (in-flight Z 4.87, rate Z −0.07) | `unknown/escalate`, unchanged |
| 11:36:09Z no-fault Incident `49fe844f` (CPU Z 1.22, p95 Z 4.53, in-flight Z 1.40, rate Z 0.01) | `unknown/escalate`, unchanged |
| S4 sweep (rate Z 4.2, p95 Z 0.3, err 0, CPU Z 0.5/1.0/1.2 × in-flight Z 0–10) | `traffic_spike/no_action`, unchanged (15/15) |
| The four cpu_saturation no-fault Incidents | `cpu_saturation/scale_deployment`, unchanged (rule 3 runs first) |

**Retrieval** (`make eval-retrieval`, 30 queries, after the runbook edit):

| Metric | Before | After |
|---|---|---|
| Recall@1 | 0.75 | 0.7917 (q06 now ranks rb-cpu-saturation first) |
| Recall@3 | 0.9167 | 0.9167 |
| MRR | 0.8374 | 0.8583 |
| Proposed floor | 2.9584 | 3.0675 |
| Recall@1 at the floor | 0.5 | 0.4583 |
| False retrieval at the floor | 0.1667 | 0.0 |

The floor is unfrozen until G16. The Reasoner ConfigMap follows it.

## Consequences

- **The corpus hash changes** (`27f60160…` → `b9190372…`), so M4's `precheck.reasoner.expected_index_hash` (G16)
  takes the new value.
- **No reasoner image rebuild in M2.** No Reasoner is deployed, and M2 runs R1. The current image's validator
  accepts `evidence/v1` only, so it would answer an evidence/v1.1 bundle with 422 `invalid_bundle`, and the
  operator would fall back to R1 (`fallbackUsed: true`). **The image must be rebuilt before any R2/R3 run.**
- **The BM25 query:** a Z-score above 3 adds its name as a query token. `inflight` matches no runbook term, so it
  adds nothing to any score.
- **Spec v1.1 (G17):** record decisions 1, 3 and 4 against §16 and §14 (re-plan X11).

## Addendum (2026-11-11): what `restart_v1` verifies

"The limit" above says the Verifier escalates when the restart does not bring "the signal" back to its baseline. That
overstates it. `restart_v1` reads every replica Ready, no new container restart for 60 s, and **p95 latency** within the
raw pre-fault baseline (`operator/nexus_operator/verifier.py`). It reads neither the in-flight count nor the request
rate, which are the two signals the new branch acts on.

| Case | What the Verifier sees | Verdict |
|---|---|---|
| **Full hang, restart fixes it** (S1; the M2 exit, `docs/plans/m2-exit-record.md`) | No request completes during the hang, so the p95 series goes missing; a missing value never verifies. After the evictions the new pods serve, p95 returns within baseline, and 60 s later it verifies | Correct: `Resolved` 13:53:14Z on 2026-11-11 |
| **Full hang, restart does not fix it** | p95 stays missing | Correct: times out, `Escalated / verification_failed` |
| **Partial hang, restart does not fix it** (some requests still complete) | The p95 of the requests that complete can sit within baseline while requests still pile up in flight | **Can verify early:** `Resolved` with the fault still present (authority-boundary review, #197 finding A2) |

The third case is not one of S1–S6, where every hang is total. It stays open: the fix is to make `restart_v1` also
require in-flight requests and the request rate back near baseline when the proposal came from the in-flight branch.
That change belongs to G16's review of verification criteria, and needs its own ADR because the catalogue's criteria are
frozen (spec §9).

## Addendum (2026-11-15): direction-aware metric words in the BM25 query

A retrieval-protocol change, made before the Block 9 freeze (TASKS.md D23). R1 is unaffected: the Rules Engine
does not use retrieval (`operator/nexus_operator` does not import `bm25`).

**The finding (reviewer, Block 3).** "The BM25 query" above said `inflight` adds nothing to any score. It also
missed a worse effect: the query ignored the sign of a Z-score. On index `fad76a74…`, the real S1 bundle (E1
attempt 3, nexus-prod, 2026-11-11: CPU Z −6.83, rate Z −57, in-flight Z 80) gave
`[nexuslatencyanomaly, completed, cpu, inflight, requestrate]`. It ranked `rb-cpu-saturation#1` first at 4.49,
above the 3.1739 floor, so the Reasoner would have shown R3 the CPU-saturation runbook for a hang. The CPU *drop*
added `cpu`, and the camelCase names (`requestrate`, `inflight`, `latencyp95`, `errorratio`) matched no runbook word.

**The decision.** `bm25.build_query` maps an anomalous metric (|Z| > 3) to words from the runbooks' own text, by
direction (`bm25.METRIC_TERMS`). A metric not in the table adds nothing.

| Metric | High (Z > 3) | Low (Z < −3) | Why |
|---|---|---|---|
| `cpu` | `cpu` | nothing | Only saturation raises CPU; a drop points at no runbook |
| `inflight` | `flight` | nothing | rb-gray-failure: "the in-flight request count" |
| `requestRate` | `rise` | `completed`, `drops` | rb-traffic-spike: "a rise in requests"; rb-gray-failure: "the rate of completed requests drops" |
| `latencyP95` | nothing | nothing | Shared by rb-gray-failure and rb-cpu-saturation; CPU decides. "latency p95" moved q01–q03 to rb-cpu-saturation |
| `errorRatio` | nothing | nothing | "error" pulled the no-runbook queries q25–q27 toward rb-cert-expiry and rb-traffic-spike |

A rate rise does not add "request rate": rb-cpu-saturation contains those words too, and the real traffic-step
bundle would have passed the floor for it (4.91). No runbook text changed.

**Results** (`make eval-retrieval`, the same 30 queries):

| Metric | Before | After |
|---|---|---|
| Corpus hash | `fad76a74…e6d8` | `fad76a74…e6d8` (the runbooks did not change) |
| Recall@1 / Recall@3 / MRR | 0.7917 / 0.9167 / 0.8583 | 0.7917 / 0.9167 / 0.8583 |
| Proposed floor | 3.1739 | 3.1739 |
| Recall@1 at the floor | 0.4583 | 0.5417 |
| False retrieval at the floor | 0.0 | 0.0 |

Acceptance (`experiments/harness/tests/fixtures/retrieval-acceptance.json`, `RetrievalAcceptanceTest`): each case
retrieves its runbook first and no other runbook above the floor.

| Case | Top-1 | Other runbooks above the floor |
|---|---|---|
| Real S1, E1 attempt 3 (2026-11-11) | rb-gray-failure 6.28 | none (rb-cpu-saturation 2.50) |
| Real S1, ADR-039 corpus (2026-11-07) | rb-gray-failure 4.22 | none |
| Real traffic step (2026-11-07) | rb-traffic-spike 4.09 | none |
| Synthetic CPU saturation (CPU Z +5) | rb-cpu-saturation 4.95 | none |
| Synthetic S4 (q10) | rb-traffic-spike 3.49 | none |

With the old builder, two of the three acceptance tests fail; the third holds the committed eval to the
record above (negative control).

**Consequences.**
- The corpus hash pins the runbooks, not the query builder. Two Reasoner images with the same `corpusHash` can
  retrieve differently. The image digest pins both, so the pre-registration (G16) records the Reasoner digest
  as well as the index hash.
- `bm25.py` is a `reasoner.yml` build path. The change rides promotion 9, so the image is rebuilt once.
- Spec v1.1 (G17): §15's query construction records the direction-aware metric words.

## Addendum (2026-11-15): `restart_v2` replaces `restart_v1` (#197 A2)

**Owner decision, 2026-11-15:** A2 is fixed, not kept as a limitation. This replaces the last paragraph of the
2026-11-11 addendum, which left the fix to G16 and its own ADR. `docs/architecture/final-spec.md` changes at G17
(re-plan X12).

**What a wrong restart ended as.** The same inputs on dev `c007c78` and on this change (the act-path rig, two
replicas, L3; a "wrong restart" is one that does not remove the fault):

| Scenario | After the restart | `restart_v1` | `restart_v2` |
|---|---|---|---|
| S1, partial hang not cleared | p95 flat, 80 in flight, rate 17 of 80 req/s | **`Resolved / remediation_verified`** | `Escalated / verification_failed` |
| S1, full hang not cleared | no p95 (nothing completes) | `Escalated / verification_failed` | the same |
| S3, bad rollout | never restarted: the Gate's preconditions refuse it | `Blocked / precondition_failed`, 0 evictions | the same |
| S5, dependency refuses the application | p95 flat, error ratio 20 % | **`Resolved / remediation_verified`** | `Escalated / verification_failed` |
| S6, traffic step that goes on | p95 flat, rate 120 of 80 req/s | **`Resolved / remediation_verified`** | `Escalated / verification_failed` |

R1 never proposes a restart on S5 or S6 (rules 6 and 5). There the restart is a Reasoner's proposal (R0, R2, R3)
or the static baseline's.

**The criterion.** `restart_v2` is met when, for 60 s, every replica is Ready, no container restarts, and every
required signal is within its raw pre-fault baseline.

| | |
|---|---|
| Required signals | p95 always. In-flight, request rate and error ratio when their Z-score in the evidence bundle was anomalous (Z > 3; \|Z\| > 3 for the request rate) or missing |
| Within baseline | `value <= mean + 3 × max(stddev, ε)`, with ε the signal's floor in the Z-score rules (ADR-024): p95 0.01 s, in-flight 1, error ratio 0.01, request rate 1/s. The request rate is two-sided: a drop is requests that do not complete, a rise is a spike that goes on |
| Unknown | Not met (ADR-024): a missing value, a missing baseline, or no record of the anomalous signals (then all four are required) |
| Where it is recorded | `status.evidence.baseline` holds the mean and stddev of the four signals' `baseline_avg15m` and `baseline_stddev15m` rules, read at Diagnosing. `status.evidence.anomalous` lists the signals. The verification row's `observations.signals` holds each value and verdict |

**No evidence-schema change.** Both fields are Incident status, which the CRD leaves open
(`x-kubernetes-preserve-unknown-fields`). The bundle stays `evidence/v1.1`: the validators, the Reasoner image and
the corpus hash are untouched. The four baseline rules already exist (`nexus-detection.yaml`).

**The Verifying timeout of a restart goes from 180 s to 300 s** (spec §7, §9). The request rate is a 2 min rate
over the new pods' series. After a restart that clears a hang it needs about 120 s to return to 80 ± 3 req/s, is
read up to 45 s late (15 s scrape, 30 s rule interval), and then has to hold for 60 s: about 225 s. With 180 s
the test with the real S1 values ends `Escalated`. **The climb is the rule's arithmetic, not a measurement:** no
recorded run holds that series through a recovery. The F3 values that are real: the bundle's Z-scores (in-flight
80.0, request rate −60.3), the p95 baseline (0.0257 s ± 0.0024), and p95 good 55 s after Verifying began. The
first live S1 at L3 settles it; `observations.signals` and `status.verification.goodSince` show the wait.

**Consequences and limits.**
- **S1 resolves later.** `restart_v1` was met 115 s after Verifying began (F3). `restart_v2` should take about 3
  to 4 minutes, so M2's 2 min 34 s from detection to resolution will not repeat. A failed restart escalates after
  300 s, not 180 s.
- **A fault that ends on its own during verification still credits the restart** (a traffic step that stops within
  the window). No signal-based criterion can tell the two apart.
- **One in-flight burst resets the 60 s hold.** A single scrape read 5 in flight in a clean window (nexus-dev,
  2026-11-07T07:50:12Z; the bound is 3). The 300 s leave room for one reset.
- **An Incident proposed by an older operator has no record**, so all four signals are required against baselines
  it does not have: it escalates. Deploy with no Incident between Proposed and Verifying.
- **`scale_v1` has the same hole and is not changed here.** It reads Ready replicas, CPU per pod and p95. In the
  rig a wrong scale 2 → 3 ends `Resolved / remediation_verified` on the S1 partial hang, the S5 and the S6 inputs
  above.
- **Spec v1.1 (G17):** §7 and §9 (`restart_v2`, 300 s), §20 (S1's criterion).
