# ADR-038: Operator L2 states — AwaitingApproval, approval validation, approvalTTL

## Status: Proposed (G8, the T3 follow-up; accepted when its pull request merges, after the M2 exit)

## Context
Spec §7 and §9 stage 8 define the L2 path: `Proposed → AwaitingApproval → Executing | Rejected |
Expired | Blocked`. ADR-033 (decisions 3, 5, 15) put the authoritative approval checks in K3 and K5.
This change adds the operator's advisory side. Nothing here ran against a cluster.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | At L2 the Safety Gate runs stages 1–6 before `AwaitingApproval` and holds **stage 7 (the dry-run) until a valid approval exists**. After approval, stages 1–7 are re-run, then `Executing`. | K3 refuses a scale or eviction without a pinning approval, and a server-side dry-run passes through admission, so a dry-run before approval is denied by design. This is a **reading of §9**, which orders stage 7 before 8 without saying so for L2. Record it in the Spec v1.1 note. |
| 2 | The operator compares `spec.approval.proposalHash` with `status.proposal.hash` (and `actionId` and `parameters` with the stored proposal). A mismatch ends `Blocked / approval_rejected`, unsent. K3 re-checks the same hash at action time. | The operator check fails early with a reason code. It is advisory; K5 (write time) and K3 (action time) stay authoritative (ADR-033 decision 15). |
| 3 | `approvalTTL` is accepted from **1 s to 15 m** (`s` or `m` suffix; default 15m). A longer value is a `ConfigError`. | §7 fixes 15 min as the ceiling and K5 `expiry-max-15m` refuses a longer `expiresAt`. A larger operator value would only never be used. |
| 4 | An Incident with no decision after `approvalTTL` in `AwaitingApproval` ends `Expired / approval_expired`. An approval whose `expiresAt` passed also ends `Expired`. A rejection ends `Rejected / approval_rejected`. | §7 phase table. |
| 5 | The operator also refuses an approval not stamped (`approvedBy`, `approvedAt`) or whose `expiresAt` exceeds `approvedAt + approvalTTL` (+5 s skew between K5's stamp and its ceiling check). | Defence in depth if K5 were bypassed; the stamp is K5's. |
| 6 | The operator's `nexus-prod` Scale and Eviction grants are in `platform/m3-live/nexus-operator-prod.yaml`, not in `platform/rbac`. | Merged is not deployed (ADR-033 decision 14). K1 still denies in `nexus-prod` at level 1. |

## Consequences
- At L2 an Incident now waits instead of ending `Escalated / autonomy_denied`. `nexus-dev` is not L2 before the M3 exit demo.
- Live checks pending: K3 admitting the post-approval dry-run, a `SubjectAccessReview` in `nexus-prod`, the L2 run of the M3 exit.
