# NEXUS Kyverno policies (spec §11)

| ID | File | Matches | Effect | Tests |
| --- | --- | --- | --- | --- |
| K1 | `k1-autonomy-floor.yaml` | `Deployment/scale`, `Pod/eviction` from the operator or baseline | Deny unless namespace label `nexus.io/autonomy-level` is 2 or 3 | `tests/k1` |
| K2 | `k2-scale-bounds.yaml` | `Deployment/scale` | Deny outside [1, 5] or a change over 2 | `tests/k2` |
| K3 | `k3-l2-approval.yaml` | scale and eviction in level-2 namespaces | Allow only with an approved, unexpired Incident that pins target, action, replicas and proposal hash | `tests/k3` |
| K4 | `k4-killswitch.yaml` | scale and eviction | Deny unless `nexus-killswitch` has `state: active` | `tests/k4` |
| K5 | `k5-incident-approval.yaml` | Incident create and update | Approvers only; stamps `approvedBy`/`approvedAt`; expiry required and at most 15 min; approval binds to `status.proposal` | `tests/k5` |
| K6 | `k6-incident-source.yaml` | Incident create | `detector` only from the operator, `human` only from approvers; stamps `source.requester` | `tests/k6` |
| — | `verify-images.yaml` | Pods using `ghcr.io/koussayx8/nexus-platform/*` | Cosign keyless check, **Audit only** | none offline (needs registry and Rekor) |
| — | `approvers-rbac.yaml` | — | Kyverno read on Incidents; group `nexus-approvers` Role (§11) | `scripts/tests/policies-offline.py` |

Enforce, `failurePolicy: Fail`, admission only. `nexus-baseline` is treated like `nexus-operator`.

## Run the tests

```bash
kyverno test platform/policies/tests          # Kyverno CLI 1.18.0, as pinned in .github/workflows/policies.yml
python3 scripts/tests/policies-offline.py     # schema check and test-coverage check, no Kyverno needed
```

One directory per identity, because a `kyverno test` has one `userinfo`. Context lookups
(namespace label, Incident list, Kill Switch ConfigMap) are mocked through `values.yaml`; the
filters over the mocked Incident list are real. Edit a policy and its cases together.

## Not covered offline (live check pending)

- Webhook wiring, subresource matching (`scale`, `eviction`) and the real `apiCall` lookups.
- A missing `nexus-killswitch` ConfigMap: tested as state `missing`; the API 404 path is live only.
- K5 `approvedBy`/`approvedAt` values: the test checks that the rule applies, not the timestamp.
- K5 and K6 together with CRD rules C1–C4 (envtest or the section 20 live matrix).
- Eviction matching is by Deployment-name prefix (`<name>-`), as §11 says; a Deployment named
  `sample` would also cover pods of `sample-api`. Both namespaces hold one Deployment today.

## Staging: merged is not deployed

Nothing the cluster syncs references these policies, `platform/network` or
`platform/m3-live`. CI builds and validates them and runs `kyverno test` and the offline checks.
The M3-live PR switches them on with one line in `platform/kustomization.yaml`:

```yaml
resources:
  - m3-live
```

Run the live checks above first (K6 gates the Alert Poller's Incident create). Until the
operator's L2 states exist, an L2 namespace stays `Escalated` and K3 never sees a request.
