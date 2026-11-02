> **M2 plan outline, rev 1 (task 0, 2026-11-02). One revision round at most; frozen at approval. Replaces the phases
> and host of [`m2-plan.md`](m2-plan.md) rev 3, whose scope trace stands. Current state lives in `TASKS.md`.**

# M2 outline: the operator acts, on WSL2, live by 2026-11-20

## Goal

The README's M2 row: the mutation path through the Scale and Eviction subresources, Safety Gate checks, and the
autonomy level raised on `nexus-dev`. On WSL2 (re-plan O2), live by **2026-11-20**.

## Scope

From the M2 plan rev 3 scope trace (sources there); the task column is new.

| # | Scope line | Task |
|---|---|---|
| 1 | §11 operator rows: cluster reads, ArgoCD Applications; `deployments/scale` and `pods/eviction` in `nexus-dev` only (staged; `nexus-prod` in M3) | T2 |
| 2 | Action Catalogue `operator/catalogue.yaml`: the four frozen entries (§9) | T2 |
| 3 | Evidence Collector, Evidence Redactor, Rules Engine R1; output validated as `proposal/v1`; redacted bundles in `evidence_bundles` | T2 (R1); collector and redactor: G1, T2's second PR (Q1: the redactor is an operator stage) |
| 4 | Action Executor: intent row and idempotency key before one Scale or Eviction request; resume checks real state | T2 on T1's Writer |
| 5 | Safety Gate stages 1–7, advisory, fixed order, first failing stage recorded; Kill Switch read at stage 5 (advisory until K4) | T2 |
| 6 | Circuit Breaker (2 per target per 15 min, 6 per hour) and cool-down (5 min), counted from the FR since `breakerEpoch` | T2 |
| 7 | Verifier (`restart_v1`, `scale_v1`) against the raw pre-fault baseline; Escalator | T2; Compensator: G2, T2's second PR (Q2) |
| 8 | Levels: L0 `Recorded`, L1 `Recommended`, L2 `Escalated`, L3 acts; `nexus-dev` → `"3"` at the exit | T2; the level commit at the exit |
| 9 | `flight-recorder-db` (StatefulSet, PV, `nexus-system`, `nexus` Application, AppProject widened in the same PR), the §14 schema, the Secrets script, the Writer, the write-ahead rule | T1 |
| 10 | `Audit-Id` stored per request (NF-05); the client wrapper | T1; the live dry-run is a live step |
| 11 | ADR-025 keys `advisoryChecks`, `breakerEpoch`, `reasonerEndpoint` (unset) | T2 (G3) |
| 12 | Operator metrics and ServiceMonitor (U3) | G3, T2's second PR |
| 13 | CI boundary check for `ignoreDifferences` | G4, T6 |
| 14 | Minimal `audit_id` join script | G5, T6 |
| 15 | Grafana FR datasource and dashboard JSON (moved here from M4) | T1 |
| 16 | `verify-state.sh` M2 checks: FR DB Ready, staged RBAC by `auth can-i --list`, live rollout strategy, levels | G7, local |

Cut from M2: step 0, the x86 replication, the new-host bootstrap fixes, the A1 calibration (re-plan O3, O4).
Moved to T5: arm64 and images.

## Sequence

| Window | Step | Who |
|---|---|---|
| 11-02 → 11-08 | T1 and T2 PRs, offline. T1 publishes the Writer interface first; T2 codes against it | agent sessions |
| by ~11-12 | T1 merged. Live: the owner runs the Secrets script; FR DB `Synced`/`Healthy`; one approved `Audit-Id` dry-run | owner (live check pending) |
| by ~11-16 | T2 merged; the operator image built and re-pinned (ADR-017); deployed with `nexus-dev` still at L0; dev RBAC widened | owner (live check pending) |
| 11-17 → 11-20 | `nexus-dev` → `"3"` by commit on `experiment/dev-state` (CLAUDE.md rule 11); the exit demo; `CURRENT_STATE.md`; the tag (separate approval) | owner |

## Automated safety tests (re-plan O7)

| Invariant | Test | Task |
|---|---|---|
| I4 RBAC | Rendered RBAC grants Scale/Eviction in `nexus-dev` only; envtest `SubjectAccessReview` denies them in `nexus-prod` | T2 |
| I14 write-ahead | FR DB unreachable ⇒ `Blocked` / `recorder_unavailable`, no request sent; intent row committed before the request | T1, T2 |
| I10 `Audit-Id` | The wrapper returns the response's `Audit-Id`; the Writer stores it with the row; a missing header is recorded, not invented | T1 |
| I6 Kill Switch | `halted` or absent ConfigMap ⇒ stage 5 blocks every action, compensation included | T2 |
| I11, I15 verification | Failed verification never reaches `Resolved`; verification uses the catalogue criteria and the raw pre-fault baseline; a forged `expectedEffect` is ignored | T2 |
| Breaker | Third action on one target within 15 min blocked; cool-down 5 min; counts from the FR since `breakerEpoch` | T2 |

Normal unit tests elsewhere; the existing envtest harness is reused (no new harness).

## Live checks (all live check pending; owner-run or owner-approved, on WSL2)

- Every Application `Synced`/`Healthy` and stable ≥ 60 s; `verify-state.sh` exits 0 with the M2 checks (G7).
- `kubectl auth can-i --list --as=system:serviceaccount:nexus-system:nexus-operator` in `nexus-dev` and
  `nexus-prod`.
- One `dryRun=All` request returns an `Audit-Id`.
- The exit criterion below.

## Exit criterion (from the M2 plan, host changed to WSL2)

On WSL2, under baseline load B = 80:
1. S2 (or S1) on `nexus-dev` at L3 ends `Resolved` / `remediation_verified` through the catalogue action.
2. The intent row precedes the request, and its `audit_id` matches an API Audit Log event (G5).
3. The same fault class in `nexus-prod` ends `Recommended`.
4. `auth can-i --list` shows no operator `deployments/scale` or `pods/eviction` in `nexus-prod`, and the audit
   extract shows no operator mutation there.
5. The incident is visible on the FR dashboard (T1). Proposed.

## Decided inside the task PRs

- U3, operator metric names (G3); U4, durable absorb (T2).
- The reason code for L2 `Escalated` in M2 (T2).

## Risks

- Until M3, `nexus-dev`'s floor, bounds and Kill Switch are advisory operator checks; RBAC (staged) and the PDB are
  the authoritative layers.
- Until M3's NetworkPolicies, any pod can reach `flight-recorder-db` on 5432.
- WSL2's checkpoint B failed twice (ADR-026, ADR-028); "quiet when healthy" is a stated limitation (O5).
- T2 depends on T1's interface; a late interface change ripples into T2.
- The operator image keeps the `USER` env workaround until T5's UID 10001 passwd entry merges; T5 inverts its test.
