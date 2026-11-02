# ADR-031: M3 — Kyverno policies, NetworkPolicies, CODEOWNERS and nexusctl

## Status: Proposed (task 3 of the 2026-11-02 re-plan; accepted when its pull request merges)

Number taken from the next free slot after ADR-030; renumber if another task's ADR lands first.

## Context
Spec §11, §16A, §18 and §19 freeze what M3 builds: six Kyverno policies (K1–K6), network
isolation (N1–N6), CODEOWNERS on the Trusted Enforcement Plane, and the `nexusctl` approval
interface. There is no cluster in the authoring session, so nothing here ran against an API server.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | The policies are `kyverno.io/v1` **ClusterPolicy**, Enforce, `failurePolicy: Fail`, `background: false`. | It is the API the pinned CLI schema (datree catalog, used by `repo-checks`) validates, and Kyverno 1.18 still serves it. Moving to `ValidatingPolicy`/CEL is a later, separate change. |
| 2 | K1–K4 match the operator **and** `nexus-baseline` by username in `preconditions`. | §11: both responders face identical enforcement. |
| 3 | K3 loads the Incident list once (`apiCall`) and filters it in JMESPath; K3 also requires `approval.proposalHash == status.proposal.hash`. | The filter is then real code under test with a mocked list. The hash match is the "hash binding" at execution time; §11's K3 text lists target, action, replicas and expiry. |
| 4 | K5 is six small rules (stamp, approvers-only, expiry required, expiry ≤ 15 min, hash binding, action/replicas binding) rather than one. | One failing reason per message, and `kyverno test` can name the rule. The stamp applies only to the **first** write of `spec.approval`; a later update must carry the stored value unchanged (C2). |
| 5 | K5 binds `approval.proposalHash` (both decisions), `actionId` and, for `scale_deployment`, `replicas` to the Incident's own `status.proposal`. | §16A "what you see is what you approve": an approver cannot pin something other than what the operator proposed. The check reads `status` from the update request's full object. |
| 6 | An approval without `expiresAt` is rejected by K5. | Otherwise omitting the field would remove the TTL. |
| 7 | `nexusctl` sets `expiresAt = now + ttl − 15 s`. | K5 compares with the API server's clock; exactly `now + 15m` can land past the bound by clock skew. |
| 8 | verifyImages: `failurePolicy: Ignore`, `failureAction: Audit`, keyless, subject `…/.github/workflows/(ci\|operator).yml@refs/heads/main`, issuer GitHub Actions, Rekor. | §19 "SHOULD, Audit first"; the identity is the one recorded in ADR-017. Enforce is a later commit. |
| 9 | The Reasoner's public-443 egress excludes the private, link-local, CGNAT and loopback ranges instead of naming the node IP. | WSL2 changes the node IP on restart (TASKS.md Later); the ranges contain the pod CIDR, the service CIDR and any WSL2 or LAN node address, and nothing is hardcoded. The Local Model rule (host IP, TCP 11434) needs the node IP and stays a bootstrap-time template (not built here). |
| 10 | Approver RBAC and Kyverno's read access to Incidents live in `platform/policies/approvers-rbac.yaml`. | Keeps this change off `platform/rbac/`, which the operator task edits; both paths are under CODEOWNERS. |
| 11 | `kyverno test` has one `userinfo` per test file, so each identity gets its own directory. Context lookups are mocked in `values.yaml`. | The CLI's design. |
| 12 | `nexusctl` ships the four MUST commands (`list`, `explain`, `approve`, `reject`) plus `nexus-client`. `explain` reads the Incident CR only; its Flight Recorder and Audit Log sections are named unavailable and `--evidence`/`--raw` exit 9. | The Flight Recorder schema belongs to another task. The SHOULD commands are M6 (§16A "Delivery"). |
| 13 | The `platform` AppProject gains `kyverno.io/ClusterPolicy` and `networking.k8s.io/NetworkPolicy`. | Standing rule: the AppProject widens in the pull request that needs the kinds. |

## CODEOWNERS and branch protection
`.github/CODEOWNERS` lists `platform/policies/`, `platform/rbac/` and the Action Catalogue module
paths. Whether review by a code owner is *required* is a branch-protection setting, unchanged by
this ADR (self-merge after green checks is allowed, §19).

## Not verified (live check pending)
- Every policy against a real API server: webhook wiring, subresource matching for `scale` and
  `eviction`, `apiCall` lookups, and that a rule error fails closed (a missing Kill Switch ConfigMap).
- `kyverno test` itself was **not run in the authoring session** (no Kyverno CLI there); the first
  run is the `policies` workflow in CI. The test files follow the CLI 1.18 formats from memory of
  its documentation and may need adjusting on first run.
- K5/K6 together with C1–C4, and the three live cells of §16A "Delivery and tests".
- Whether k3s' network-policy controller enforces under WSL2: `scripts/tests/network-n1-n6.sh`.
- `verifyImages` verification of a real signature.
- N6 (no ServiceAccount token in the Reasoner) is a property of the Reasoner's pod spec (M4).

## Consequences
- Merging to `main` delivers the policies in Enforce. K6 gates the Alert Poller's `create`; a wrong
  K6 would stop detection, so the gate pull request needs a live check of one detector Incident first.
- `nexus-prod` stays at L1; K1 denies any mutation there until the label changes in Git.
