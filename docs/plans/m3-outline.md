> **M3 plan outline, rev 1 (task 0, 2026-11-02). One revision round at most; frozen at approval. Current state lives
> in `TASKS.md`.**

# M3 outline: L2, the Trusted Enforcement Plane in Enforce, network policies, by 2026-12-06

## Goal

Make approval and confinement properties of the Trusted Enforcement Plane (spec §10, §11): K1–K6 in Enforce,
the L2 flow with `nexusctl`, `nexus-prod` grants under Kyverno, NetworkPolicies N1–N6, `verifyImages` in Audit,
CODEOWNERS. On WSL2, by **2026-12-06**.

## Scope

| # | Item | Source | Task |
|---|---|---|---|
| 1 | Kyverno K1–K6, Enforce, `failurePolicy: Fail`, matching only `Deployment/scale`, `Pod/eviction`, `Incident`; also `nexus-baseline` | §11 | T3 |
| 2 | `kyverno test` per policy, both directions (§11 "Policy tests"), run in CI on every change to `platform/policies/` | §11, §19 | T3 |
| 3 | K5 approver stamping (`approvedBy`, `approvedAt` from request identity and clock), TTL ≤ 15 min, and the proposal-hash binding (re-plan X7: which policy checks the hash is settled in T3) | §11, I16 | T3 |
| 4 | `verifyImages` for the ADR-017 signing identity, Audit mode | §19, Later | T3 |
| 5 | NetworkPolicies per §18: default deny, then allows; node IP templated at bootstrap, never hardcoded; `nexus-load` closes the unauthenticated Locust API | §18, Later | T3 |
| 6 | CODEOWNERS on `platform/policies/`, `platform/rbac/`, the Action Catalogue | §19 | T3 |
| 7 | `nexusctl` core: `list`, `explain`, `approve`, `reject` (§16A); sanitising renderer; exit codes | §16A | T3 |
| 8 | Approval schema (`spec.approval`) and `approvalTTL` (ADR-025 key) | §8, ADR-025 | T3 |
| 9 | Operator L2 states (`AwaitingApproval`, approval re-validation at stage 8, `Expired`, `Rejected`) | §7, §9 | T3 follow-up, after T2 merges (G8) |
| 10 | `nexus-prod` Scale/Eviction grants, with K1–K6 in Enforce | M2 plan, §11 | T3 follow-up (G8) |
| 11 | The slimmer base and its Trivy comparison; multi-arch images and signing | Later, ADR-030 | T5 |
| 12 | `verify-state.sh`: K1–K6 in Enforce, N1–N6 | §25 | G7 |

## Sequence

| Window | Step |
|---|---|
| 11-02 → 11-20 | T3 and T5 PRs, offline, in parallel with M2 |
| after T2 merges | T3 follow-up: operator L2 states; `nexus-prod` grants |
| 11-21 → 12-06 | T3, its follow-up and T5 merged; live: Kyverno policies Enforce, N1–N6, the §16A coverage cells; the exit; the tag (separate approval) |

## Automated safety tests (re-plan O7)

| Invariant | Test | Task |
|---|---|---|
| K1–K6 (I5–I8) | `kyverno test`, both directions, every row of §11's "Policy tests" table | T3 |
| K5 binding (I16) | Approval by the operator or a non-approver denied; identity and time stamped by the mutate rule; expiry > 15 min denied; an altered `proposalHash`, `actionId` or replicas denied; replay of an old approval denied | T3 |
| C2 write-once | A second approval rejected (CRD CEL; existing CRD tests extended if needed) | T3 |
| I6 Kill Switch | K4: `halted` and absent both deny, compensation included | T3 |
| I1/I2 | NetworkPolicy manifests: Reasoner egress excludes pod CIDR, service CIDR and node IP; FR DB ingress only from the operator and Grafana. Reasoner manifest (G9): no token, no RoleBinding | T3, G9 |
| `nexusctl` | Sanitisation against S6 strings and escape sequences; a changed hash aborts with exit 4; every exit code (§16A) | T3 |
| RBAC (I4) | Rendered RBAC grants Scale/Eviction in `nexus-dev` and `nexus-prod` only | G8 |

## Live checks (all live check pending; owner-run or owner-approved, on WSL2)

- The Kyverno version questions of §27: `Deployment/scale` and `Pod/eviction` matching, Incident lookups,
  ConfigMap context, time functions, failed-lookup behaviour.
- The three §16A cells: a non-Approver's approval denied by K5; a second approval rejected by C2; an expiry beyond
  15 minutes rejected by K5.
- N1–N6 (§18). N1–N4 and N6 need a pod in `nexus-reasoner`; until G9 deploys the Reasoner, a stand-in pod with the
  Reasoner's labels and ServiceAccount settings is used, and the tests are re-run once the Reasoner exists.
- `verifyImages` Audit reports for sample-api and the operator.

## Exit criterion (proposed)

On WSL2:
1. An L2 incident (re-plan Q6) approved with `nexusctl approve` is admitted and ends `Resolved`; the same request
   with no approval, an expired approval or an altered hash is denied by Kyverno.
2. K1–K6 report Enforce; the §16A cells pass. The fail-closed test runs with the containment suite in M4 (G11).
3. The operator holds Scale/Eviction in `nexus-dev` and `nexus-prod` only (`auth can-i --list`).
4. N1–N6 give the §18 results.

## Decided inside the task PRs

- Re-plan X7: where `proposalHash` is checked (K3, K5 or both).
- Re-plan Q6: the L2 namespace for the exit.
- The node-IP templating mechanism for the NetworkPolicies (bootstrap step or ConfigMap-driven).

## Risks

- WSL2 NetworkPolicy enforcement by the k3s controller is "verified, not assumed" (§18); a failure is a decision
  gate, not an adaptation.
- K1–K6 in Enforce with `failurePolicy: Fail` can block the operator during M3's live work; the Kill Switch and
  Kyverno availability are checked before each live step.
- T3's L2 follow-up waits on T2; a late T2 merge compresses M3.
