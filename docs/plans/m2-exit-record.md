# M2 exit record (WSL2, 2026-11-11)

The record of the live runs that close M2 ([`m2-outline.md`](m2-outline.md), exit criterion; [`m2-live-plan.md`](m2-live-plan.md)
steps E1 and F1–F3). Every fact below comes from the Incident objects, the Flight Recorder (read as `nexus_reader`), the
API Audit Log or Prometheus, read at the time; the evidence directories are on the host, outside the repository.

**Host:** WSL2, single-node k3s v1.34.6. Load: B = 80 req/s per namespace (Locust, 160 users).
**Operator:** `nexus-operator@sha256:b7edff2a9f0b2997e3372a723daa3807ee3134c80807478dfed6f9d7b048ca87` (ADR-039), on
`main` `030d9fe` (promotion 4b). Flight Recorder `proposals` keyed by `(incident_uid, proposal_hash)` (migration 039).
**Levels:** `nexus-dev` 0 for E1, 3 for F3 (`main` `f949526`, `experiment/dev-state` `80d0181`); `nexus-prod` 1 throughout.
**Reasoner:** none (`reasonerEndpoint` empty): every proposal is R1's.

## E1 attempt 2 (2026-11-07): the finding that led to ADR-039

| Namespace | Incident | Detected | Ended | Proposal |
|---|---|---|---|---|
| `nexus-dev` (L0) | `inc-669532275a7b73d2-1794039789` | 08:23:09Z | `Recorded / level_observe` | none (L0 records only) |
| `nexus-prod` (L1) | `inc-a676334b7210ccba-1794045159` (`5fa4f476`) | 09:52:39Z | `Escalated / proposed_escalation` | `unknown / escalate` |

The prod bundle (09:53:01Z) had CPU Z −6.77, request-rate Z −62.12 and p95 Z −0.02: a full hang stops requests from
completing, so the p95 of those that complete stays flat, and R1 rule 4 (p95 Z > 3) did not match. The alert had fired on
in-flight Z (80), which `evidence/v1` did not carry. Six prod Incidents also lost their `proposals` row to the
content-hash key. Both are settled by ADR-039 (owner, 2026-11-09, option A2).

Evidence: `~/nexus-evidence/adr-039-fr-20261107/`, `~/nexus-evidence/adr-039-prom-20261107/`; ADR-039 "Context".

## E1 attempt 3 (2026-11-11, nexus-prod only): the in-flight branch

S1 injected on both `nexus-prod` pods at 08:30:17Z (one `kubectl exec` per pod, the runner's planned calls).

| Incident | Detected | Detected → Diagnosing → Proposed → terminal | Ended | Proposal |
|---|---|---|---|---|
| `inc-a676334b7210ccba-1794385899` | 08:31:39Z | 08:31:48 → 08:31:58 → 08:32:03 → 08:32:08Z | `Recommended / level_recommend` | `gray_failure / restart_workload` |

Bundle `evidence/v1.1`: CPU Z −6.83, p95 Z 0.02, request-rate Z −57.0, **in-flight Z 80.0**: rule 4's in-flight branch.
Gate stages 1–3 pass (stages 4–7 do not run at L1). 0 `actions` rows; 0 operator scale or eviction requests in the
audit log. Pods replaced by the owner at about 09:29Z.

Evidence: `~/nexus-evidence/e1-attempt3-2026-11-11/` (Incident object, Flight Recorder rows, the recovery Incidents).

## F3 (2026-11-11): the exit run

Pre-checks at 13:48:56Z: no Nexus alert; every Z-score in both namespaces below 1 except `nexus-prod` CPU Z −0.83;
2/2 Ready in both; Kill Switch `active`; `recorder_up` 1; PDB `maxUnavailable: 1` with 1 disruption allowed; no open
Incident. A 10-minute watch after `nexus-dev` went to L3 (13:30–13:40Z) saw no alert, Incident or operator request.

### S1 on `nexus-dev` at L3 (injected 13:49:16Z)

| Incident `inc-669532275a7b73d2-1794405039` (`NexusLatencyAnomaly`, detected 13:50:39Z) | Time (Z) |
|---|---|
| Detected | 13:50:43 |
| Diagnosing | 13:50:49 |
| Proposed: `gray_failure / restart_workload` (R1, in-flight branch; bundle in-flight Z 80.0, request-rate Z −60.3) | 13:50:54 |
| Executing: gate stages 1–7 pass; stage 7 dry-run eviction admitted (13:50:59.201) | 13:50:59 |
| Eviction 1, `sample-api-8577d7dd4d-dch5r`: intent row 13:51:04.670, request 13:51:04.673, HTTP 201, Audit-Id `2873484a-dda7-4afb-923d-f4bc2c025381` | 13:51:04 |
| Replacement `5v9zg` Ready | 13:51:11 |
| Eviction 2, `sample-api-8577d7dd4d-szmjw`: intent row 13:51:14.693, request 13:51:14.696, HTTP 201, Audit-Id `2e7fa771-7cab-468f-b0f4-1cbcf9d6999b` | 13:51:14 |
| Replacement `twftl` Ready | 13:51:20 |
| Verifying (`restart_v1`; good since 13:52:14, 0 restarts) | 13:51:19 |
| **Resolved / `remediation_verified`** | 13:53:14 |

Detection (`detectedAt` 13:50:39.8Z) to resolution: 2 min 34 s. Injection to resolution: 3 min 58 s.

`scripts/audit-join.py` over 13:49–13:54Z: 2 operator mutation events, 2 `actions` rows, **2/2 matched**, completeness
1.0, 1 dry-run left out of the join, `ok`.

### S1 on `nexus-prod` at L1 (injected 13:57:22Z)

| Incident `inc-a676334b7210ccba-1794405519` (`NexusLatencyAnomaly`, detected 13:58:39Z) | Time (Z) |
|---|---|
| Detected | 13:58:43 |
| Diagnosing | 13:58:49 |
| Proposed: `gray_failure / restart_workload` (bundle in-flight Z 80.0, request-rate Z −52.3); gate stages 1–3 pass | 13:58:54 |
| **Recommended / `level_recommend`** | 13:58:59 |

0 `actions` rows; 0 operator scale or eviction requests in the audit log after 13:57Z. Pods replaced by the owner at
14:10:27Z and 14:10:59Z.

### The five exit criteria

| # | Criterion | Result |
|---|---|---|
| 1 | S1 on `nexus-dev` at L3 ends `Resolved / remediation_verified` through the catalogue action | **Met:** `restart_workload`, two evictions one at a time within the PDB, `restart_v1` met |
| 2 | The intent row precedes the request, and its `audit_id` matches an API Audit Log event (G5) | **Met:** intent 3 ms before each request; audit-join 2/2, completeness 1.0 |
| 3 | The same fault class in `nexus-prod` ends `Recommended` | **Met:** `Recommended / level_recommend`, `gray_failure / restart_workload` |
| 4 | `auth can-i --list` shows no operator `deployments/scale` or `pods/eviction` in `nexus-prod`, and the audit extract shows no operator mutation there | **Met:** get, list and watch only; 0 operator mutations in `nexus-prod` |
| 5 | The Incident is visible on the Flight Recorder dashboard | **Met:** dashboard "NEXUS Flight Recorder" (`/d/nexus-flight-recorder`): both Incidents in "Incidents by terminal phase and reason", the two evictions in "Actions" |

`verify-state.sh` after F3 (15:12–15:13Z): 16 passed, 0 failed.

Evidence: `~/nexus-evidence/f3-2026-11-11/` (`SHA256SUMS`): two audit-log copies, `actions-dev.jsonl`,
`actions-prod.jsonl` (empty), `audit-join-dev.txt`, `audit_links-dev.jsonl`, `incidents.json`, `fr-rows-dev.txt`,
`fr-rows-prod.json`, `can-i-nexus-prod.txt`, `dashboard.png`, `CURRENT_STATE-after-f3.md`.

## Deviations

| # | Deviation | Effect |
|---|---|---|
| 1 | `nexus-prod` was injected 4 minutes after `nexus-dev` resolved, without a gate run on `nexus-prod` first. The F3 pre-check at 13:48:56Z covered both namespaces; a fresh one at 13:57:10Z showed no alert and every prod Z-score below 1 except CPU Z −1.12 | None seen: the prod Incident is the injected fault's. The plan's run order is corrected below |
| 2 | Prod pods were replaced by hand at L1 (owner), because `kubectl delete` and `kubectl rollout` are on the project's deny list | None: L1 never acts; replacement is the experimenter's job |
| 3 | Recovery Incidents from baseline carry-over. After each prod pod replacement the 15-minute baseline still held the hang (rate near 0), so normal load read as a spike. 2026-11-11 09:31:09Z `NexusTrafficAnomaly` and 09:32:09Z `NexusCpuAnomaly` (after E1 attempt 3), and 14:15:39Z both again (after F3): all `nexus-prod`, `Escalated / proposed_escalation`, `unknown / escalate`. Also 09:34:09Z `NexusCpuAnomaly` in `nexus-dev` (then L0), `Recorded` | No action (L1, L0). Recorded as no-fault Incidents; M4's pre-checks wait them out |
| 4 | E1 attempt 3 ran in `nexus-prod` only; `nexus-dev`'s L0 path was already shown by attempt 2 | None |
| 5 | The WSL2 VM stopped or paused whenever no session held it (2026-11-09 19:23 → 11-10 00:08Z, 11-10 04:56 → 05:04Z, a ~4 h 45 min pause, 11-11 10:34 → 12:59Z) | No run was in flight; each restart was waited out before the next step |
