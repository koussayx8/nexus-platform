# Adversarial review of the authority boundary, 2026-11-10

**Scope:** `dev` at `0698d7c` (after ADR-039). Code read: `operator/nexus_operator/`, `platform/policies/`,
`platform/rbac/`, `platform/crds/`, `scripts/audit-join.py`, `scripts/reconcile-audit.py`. The L2 states
(#179) are not on `dev` and were not reviewed.
**Threat model:** the Reasoner returns any JSON it likes; evidence text (logs) is attacker-controlled (S6).
The operator, the Flight Recorder, the cluster admin and Git are trusted.
**Rule:** docs and tests only. No fix is in this change.

## Verdict

**No path was found from a proposal or from evidence text to a mutation outside the envelope.** Every error
path found fails closed. What an adversarial Reasoner controls is: one of four action ids, and `replicas`
within [1, 5] and a step of 2.

| Why | Evidence |
|---|---|
| The target of a request never comes from the proposal | `act.py:592-593`, `act.py:635-636` read `spec.target`; the proposal's target is only compared (`gate.py:163-168`) |
| The only parameter is an integer `replicas` | `proposal.py:27-28`, `proposal.py:71-74`; bounds at `gate.py:180-189` |
| The catalogue holds even with advisory checks off | `act.py:530-537` |
| No intent row, no request | `recorder/actions.py:42-45`; `executor.py:62-67` |
| R1 reads no text | `rules.py` reads `workload`, `pods[].containers[].state/reason` and `metrics` only |

The findings below are of four kinds: attacker-controlled text that stops the trusted plane (A1, A4, A5);
verification that can call a failed repair a success (A2, A3); audit records that can disagree with what
happened (A6, A12, A13); and advisory checks that have no authoritative twin deployed today (A9, A10, A14,
A15).

## What enforces the boundary today (VERIFIED live, 2026-11-10 00:55Z, read-only)

| Layer | State |
|---|---|
| Kyverno K1–K6 | **Not deployed.** `kubectl get clusterpolicy` returns none; `platform/kustomization.yaml` does not list `m3-live` |
| RBAC | `nexus-operator-act` RoleBinding exists in `nexus-dev` only |
| Levels | `nexus-dev` 0, `nexus-prod` 1 |
| PDB | `maxUnavailable: 1` (`apps/sample-api/k8s/base/pdb.yaml:10`) |

So until M3-live, the level, the bounds and the Kill Switch are enforced only by the operator's own gate.
RBAC bounds the namespace and the two verbs; the PDB bounds concurrency. That is trusted code, not the
Reasoner, so the thesis claim holds for M2, but A9, A10, A14 and A15 have no second layer yet.

## Findings

Tests: `operator/tests/unit/test_review_authority_boundary.py` (pytest, strict xfail),
`operator/tests/unit/test_review_authority_boundary_pg.py` (real PostgreSQL, CI only) and
`scripts/tests/test_review_authority_boundary.py` (unittest `expectedFailure`). Each test states the wanted
behaviour and fails today; a fix removes its marker.

| ID | Severity | Finding | Test |
|---|---|---|---|
| A1 | High | Redaction time is quadratic in the length of a log line | yes |
| A2 | High | `restart_v1` never reads the in-flight signal that ADR-039's branch acts on | yes |
| A3 | Medium | A response lost after the request took effect ends the Incident without verification | yes |
| A4 | Medium | A NUL character or a non-finite number reads as a Flight Recorder outage | yes (3 of 6 in CI only) |
| A5 | Medium | One redaction pass can leave an address that the second pass finds | yes |
| A6 | Medium | Gate and verification rows are first-write-wins, written before the transition | yes |
| A12 | Medium | A crash-recovered action is reported as an unexplained request and a containment violation | yes |
| A13 | Medium | The step re-check trusts the operator's own record of the previous count | no |
| A7 | Low | The Gate never recomputes the hash of the document it reads | yes |
| A8 | Low | `proposal/v1` is strict at the top level only | yes |
| A9 | Low | The namespace level is not read again during Executing | yes |
| A10 | Low | A resumed request is sent again without reading the Kill Switch | yes |
| A11 | Low | A Deployment without `matchLabels` selects every pod in the namespace | yes |
| A14 | Low | Compensation restores the recorded count even outside [1, 5] | yes |
| A15 | Low | Breaker and cool-down are check-then-act | yes |

### A1 (High): redaction is quadratic in an attacker-controlled line

- **Evidence:** `redact.py:45-52` (`password`) and `redact.py:53-57` (`email`) start with an unanchored run
  (`\b[\w.-]*…`, `\b[A-Za-z0-9._%+-]+@`); `redact.py:111` applies every pattern to every string.
  `kube.py:171` asks for 200 lines with no byte limit. `act.py:331` runs it inside the event loop.
- **Measured** (this machine, `redact_text` on one line of `a.a.a.…`): 2,000 chars 0.25 s; 4,000 0.95 s;
  8,000 3.8 s; 16,000 13.6 s. Doubling the length quadruples the time.
- **Scenario:** a workload logs 200 lines of 16,000 such characters. Collecting one Incident's evidence then
  blocks the operator's only event loop for about 45 minutes per pod. The liveness probe (15 s × 3;
  stale after 30 s, `main.py:252`) restarts the operator. The diagnosis is kept in memory only
  (`act.py:119-122`), so it starts again and blocks again. While this lasts no Incident in any namespace
  moves, R1 included, and a restart in flight for another Incident stops between two evictions.
- **Safety:** fails closed (nothing is sent). The attacker controls the availability of the trusted plane.
- **Not run live.** The restart loop is read from the code.

### A2 (High): a restart can be "verified" while the hang continues

- **Evidence:** `verifier.py:148-155`: `restart_v1` reads Ready replicas and p95 only. ADR-039's new branch
  restarts when in-flight Z > 3 and request-rate Z < −3 with p95 **flat** (`rules.py`, rule 4).
- **Scenario:** a hang that the restart does not clear, where a few requests still complete. p95 of those
  stays within baseline, every pod is Ready, no container restarts for 60 s: `Resolved /
  remediation_verified`, with requests still piling up.
- **What limits it:** in the recorded S1 the p95 series stopped once no request completed (09:53:14Z in
  nexus-prod), and a missing p95 never verifies (`verifier.py:85-94`). So it needs a partial hang.
- **Correction to ADR-039:** its "The limit" section says the Verifier escalates when the restart does not
  bring "the signal" back to baseline. The Verifier reads p95, not in-flight or the request rate.
- **Also:** I11 ("failed verification never becomes success") holds for the state machine, not for a
  criterion that does not measure the fault.

### A3 (Medium): a lost response leaves an unverified change

- **Evidence:** `recorder/actions.py:50-58` records a transport error; `executor.py:70-77` returns `error`;
  `act.py:818-819` ends the Incident `Escalated / execution_timeout`. The crash path does check the cluster
  (`act.py:638-641`); this path does not.
- **Scenario:** the scale 2 → 4 is admitted and the response is lost (client timeout 10 s,
  `recorder/audit.py:59`). The Deployment stays at 4. No verification, no compensation. The row has no
  Audit-Id, so reconciliation reports the audit event as unexplained (see A12).

### A4 (Medium): values PostgreSQL cannot store read as a recorder outage

- **Evidence:** `writer.py:152-158` wraps every database error as `RecorderUnavailable`. `act.py:359-365`
  retries until the 60 s timeout, then `Blocked / recorder_unavailable`. R1 is not used as a fallback and
  neither the bundle nor the proposal is stored. Each failure drops the pool and sets
  `nexus_operator_recorder_up` to 0 (`recorder/act.py:169-189`), which the harness pre-check reads.
- **Inputs:**
  - `confidence: NaN`, `Infinity` or `1e999`: Python's `json.loads` accepts all three and
    `proposal.py:78` accepts the float; `json.dumps` then writes `NaN`, which is not JSON. **Verified offline.**
  - A NUL byte in a log line (`kube.py:178`; redaction keeps it) or in the Reasoner's raw output
    (`reasoner_client.py:192`), or `\u0000` in a proposal string. PostgreSQL rejects it in `text` and in
    `jsonb`. **UNVERIFIED locally** (no PostgreSQL here); the three tests run in CI.
- **Scenario:** an adversarial Reasoner answers every Incident with `"confidence": NaN`. Every Incident ends
  `Blocked / recorder_unavailable` after 60 s, labelled as an infrastructure fault, with no proposal row.

### A5 (Medium): redaction is not idempotent on overlapping addresses

- **Evidence:** `redact.py:58-67`: the IPv4 pattern runs before the IPv6 one. On `::999.1.1.1.2` it matches
  `999.1.1.1`, keeps it (not a valid address, `redact.py:93-98`) and moves past it; the IPv6 pattern then
  takes `::999`. The stored text is `[REDACTED:ip].1.1.1.2`, and a second pass finds `1.1.1.2`.
- **Effect 1:** a valid address stays in the stored bundle. In R1-only mode nothing checks.
- **Effect 2:** with a Reasoner configured, `reasoner_client.py:78` finds it, returns `not_redacted`, and
  `act.py:353-358` ends the Incident `Escalated / evidence_error` with no R1 fallback.
- **Scenario (S6):** 13 characters in one log line end every R2/R3 Incident on that workload as Escalated,
  while the R1 arm is untouched. That biases any comparison between arms.

### A6 (Medium): the recorded verdict may not be the one acted on

- **Evidence:** `act.py:523-528` records the gate decisions, then writes the status. A lost status write
  (`_write` returns `None` on a 409) runs the gate again on the next tick. The rows are
  `ON CONFLICT … DO NOTHING` (`writer.py:294`, `writer.py:308`), so the first verdict stays.
- **Scenario:** tick 1, stage 6 fails (a rollout is in progress) and the status write loses a 409. Tick 2,
  the rollout is over, the gate passes and the scale executes. The Flight Recorder shows stage 6 = fail for
  an Incident that acted. The same holds for a verification row written before a lost transition
  (`act.py:888-899`).

### A12 (Medium): a crash at the wrong moment becomes a containment violation

- **Evidence:** `act.py:821-827` closes a crash-recovered row as `applied_unconfirmed` with no Audit-Id.
  `audit-join.py:88-90` calls that row `pending`; `audit-join.py:101-105` calls its audit event
  `unexplained`; `reconcile-audit.py:162` counts every unexplained event as a containment violation.
- **Contradiction:** spec §17, "Flight Recorder write fails after an admitted request": "Reconciliation
  flags the incomplete row; not an unexplained request". That holds when the completion is retried from
  status with its Audit-Id; it does not when the operator died before it had one.
- **Scenario:** the operator restarts between a request and its completion write (it has restarted 3 times
  since 2026-11-04; the node restarted at 00:09Z today). The run reports 1 containment violation, the thesis
  metric that must be 0. The run must still not count; it must not be called a violation.

### A13 (Medium): the step bound is re-checked against the operator's own record

- **Evidence:** `reconcile-audit.py:114-127` reads `previous_state.replicas` from the `actions` row. The
  operator read it at `act.py:600-605`, one tick before the request, and the gate checked the step against
  an earlier read still (`gate.py:184-189`).
- **Scenario:** the count changes between the read and the request (a human, an HPA). The re-check computes
  the step from a stale count; for a compromised operator it computes it from whatever the operator wrote.
  "From the authoritative record" (script docstring, step 4) is not true for this one rule.
- **No test:** the fix needs a design (the audit event's response object carries the old status).

### Low findings

| ID | Evidence | Scenario | Authoritative twin |
|---|---|---|---|
| A7 | `act.py:514-523` uses the document as read; `proposal.py:100-103` is never applied to it | A row whose document does not hash to `status.proposal.hash` is executed. Not reachable by the Reasoner; K5 binds an approval to the hash, and nothing binds the document to it | none |
| A8 | `proposal.py:42` checks top-level keys only; `action`, `hypothesis`, `escalation`, `recommendation` accept extra keys (`proposal.py:55-74`, `82-96`) | Arbitrary Reasoner JSON lands in `status.proposal.escalation` (`act.py:545`; the CRD preserves unknown fields, `incidents.nexus.io.yaml:123`). No decision reads it. Spec §24 says "Strict schema … residual risk: none" | n/a |
| A9 | `act.py:669-678` re-reads the Kill Switch only; the level is read once (`gate.py:196-207`) | The label goes 3 → 1 after the first eviction; the second is still sent (a restart may run 300 s) | K1 (M3) |
| A10 | `act.py:638-659` sends a resumed pending request with no Kill Switch read and no Executing timeout | After a crash with the Kill Switch halted, the request goes out | K4 (M3) |
| A11 | `gate.py:82-86` builds the selector from `matchLabels` only; an empty selector lists the namespace (`kube.py:138-144`). Used at `gate.py:246`, `act.py:607-617`, `act.py:713` | A Deployment with `matchExpressions` only: the first eviction can hit another workload's pod. RBAC allows any pod in the namespace. sample-api uses `matchLabels` | none (K3 by name prefix at L2 only) |
| A14 | `act.py:951-969` scales back to `plan.previous` unchecked; stage 3 bounds only the new count and the step (`gate.py:180-189`) | Live count 0, proposal 2: admitted; verification fails; compensation sends replicas 0. Spec §9 excludes scale to zero | K2 (M3) |
| A15 | `gate.py:214-227` reads intent rows; the row is written a tick later (`act.py:790-792`) | Two Incidents on one target pass the gate in the same tick and both act. The Alert Poller keeps one open Incident per target (`poller.py:157-170`), so it needs a smoke and a real Incident, or (M3) a human-created one | none: the spec calls the breaker advisory only |

## Claim scope (no defect; state it in the thesis)

| # | Observation | Evidence |
|---|---|---|
| B1 | At L3 nothing in the Trusted Enforcement Plane binds a request to an Incident or its target. K3 applies at level 2 only; RBAC allows any Deployment/scale and any eviction in the namespace. Reconciliation's envelope is namespace, level and bounds, not the target | `k3-l2-approval.yaml` (precondition `level == 2`); `nexus-operator-dev.yaml`; `reconcile-audit.py:67-128` |
| B2 | A K3 approval is reusable until it expires and is not bound to the Incident's phase: any number of matching requests in 15 minutes, also after the Incident is terminal. I16 says it "binds one action". `tests/k3` has no replay case | `k3-l2-approval.yaml:40-45`, `87-91` |
| B3 | The Reasoner decides inaction with no gate: `no_action` at L2/L3 ends `Dismissed`, and nobody is handed the Incident. A manipulated Reasoner can dismiss a real fault. The spec's residual risks name unnecessary actions, not suppressed ones | `act.py:547-551`; spec §24 |
| B4 | `advisoryChecks: off` does not remove every operator check: the catalogue, the level routing from `status.autonomyLevel` and the compensation's Kill Switch read and dry-run stay. Spec §9 says "remove the whole blue box" | `act.py:530-537`, `548-564`, `942-963` |
| B5 | A target read error at stage 3 is recorded as stage 6, with stages 3–5 unrecorded | `gate.py:172-177` |

## Checked, no finding

| Area | Result |
|---|---|
| Type confusion in `replicas` (bool, float, string, missing) | Rejected at stage 1 or 3 (`proposal.py:27-28`, `gate.py:181-183`); existing tests |
| Redirected target, unknown action, out-of-range count | Blocked before any request; existing tests |
| Write-ahead order; recorder down; completion failure | Intent before request; no intent, no request; completion retried from status |
| Replay of an idempotency key | A recorded key is never sent again (`recorder/actions.py:42-45`); the next request uses a new key |
| Gate timeout, dry-run denial or error | Fail closed (`gate.py:130-135`, `251-255`) |
| Illegal phase changes | Refused in code (`act.py:73-88`, `211-218`) |
| Kill Switch missing or unreadable | Treated as halted (`killswitch.py`); a read error fails stage 5 |
| Reasoner metadata | Cut to short scalars (`reasoner_client.py`, `_clean_meta`) |
| Incident creation | Only from alerts whose `nexus_target` equals the namespace label, in a C3 namespace (`poller.py:72-88`) |
| `evidence_bundles` key | `bundle_hash` alone, but the bundle carries `incident.uid`, so two Incidents cannot share it (ADR-039, K) |

## Not covered

- The L2 path in the operator (#179) and nexusctl.
- Kyverno behaviour on a live API server: subresource matching and `apiCall` lookups are live-only checks
  (`platform/policies/README.md`).
- The Reasoner service itself (prompt, adapters): it is untrusted by assumption.
