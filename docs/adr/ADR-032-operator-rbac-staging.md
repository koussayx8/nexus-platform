# ADR-032: Operator RBAC Staging — Dev-Only Scale and Eviction in M2, Prod in M3

**Status:** Proposed (task 2, PR to `dev`)
**Date:** 2026-11-02
**Spec:** §11 (RBAC, frozen), §12, §9; ADR-025 (the first staging of §11)

## Context

§11 gives the `nexus-operator` ServiceAccount `deployments/scale` (get, patch, update) and
`pods/eviction` (create) in `nexus-dev` and `nexus-prod`. ADR-025 staged §11 for M1b: no mutating
right at all, because no stage after `Detected` existed. M2 adds the Action Executor. Kyverno
K1-K6 are M3 work, so in M2 the only authoritative layers on a mutation are RBAC and the
PodDisruptionBudget.

## Decision

| # | Decision |
| --- | --- |
| 1 | **M2 grants the two mutating rights in `nexus-dev` only**, as a namespaced Role and RoleBinding in `platform/rbac/nexus-operator-dev.yaml`: `apps/deployments/scale` get, patch, update; `pods/eviction` create. With them, the Deployment and Pod reads the Gate, Executor and Verifier use (get, list, watch), in `nexus-dev` only. |
| 2 | **`nexus-prod` gets no binding in M2.** A mutation there is refused by RBAC before any policy is asked. `nexus-prod` is also L1, so no mutation is planned there. |
| 3 | **`nexus-prod` joins in M3, in the same pull request that puts Kyverno K1-K6 in Enforce**, never before. Granting the right in prod while the autonomy floor (K1), the bounds (K2) and the kill switch (K4) are not enforced would leave RBAC and the PDB as the only authority there. |
| 4 | **§11's end state is unchanged.** The rows, the denied-by-omission list (no delete, no patch on Incident spec or metadata, no writes to namespaces, ConfigMaps, Secrets, RBAC or policies, no `impersonate`, `bind` or `escalate`) and the group rows stay as frozen. This ADR changes the order in which they are granted, as ADR-025 did. |
| 5 | **The cluster-wide reads of §11** (`pods/log`, `events`, `services`, `endpoints`, `replicasets`, `endpointslices`, ArgoCD Applications) stay ungranted until the Evidence Collector exists. The act path does not need them. |
| 6 | **A unit test guards the boundary** (`operator/tests/unit/test_rbac_manifest.py`): the mutating rules are exactly Scale and Eviction, bound only in `nexus-dev`; nothing reaches `nexus-prod` or `nexus-data`; no delete, wildcard or escalation verb; no write to Secrets, ConfigMaps, namespaces or RBAC. It reads the manifests in Git and fails if any of them widens. |

## Consequences

- In M2 a mutation is authorised by RBAC and the PodDisruptionBudget, and refused by them in
  `nexus-prod`. The operator's own Safety Gate is advisory. The compromised-operator result
  (phase D) needs Kyverno and is M3's.
- The live half of the boundary test (a request the API server refuses, and its `Audit-Id`) is
  **live check pending**: no cluster was available when this was written.
- The M3 pull request must widen `test_rbac_manifest.py` in the same change: `nexus-prod` joins
  the allowed set there and nowhere else.
