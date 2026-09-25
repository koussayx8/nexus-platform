# ADR-015: Kyverno as an Application, Reports Disabled, Superseded Policies Removed

## Status: Accepted

## Context
Before M0, Kyverno v1.18.0 ran as a Helm release (chart `kyverno-3.8.0`) that was outside Git, so
the rebuild could not reproduce it. Its reports controller was in CrashLoopBackOff with 62 restarts
from cache-sync and lease timeouts (snapshot `20260925T064759Z`, `03e`/`03f`). Two ClusterPolicies,
`nexus-autonomy-level` (Audit) and `mutate-autonomy-level-default`, implemented a workload-annotation
autonomy design that spec §12 supersedes (ADR-018).

## Decision
- **Application `kyverno`**, multi-source:
  - chart `kyverno` **3.8.0** (Kyverno **v1.18.0**, the running version) from `https://kyverno.github.io/kyverno/`;
  - values from `platform/kyverno/values.yaml` on `main`;
  - namespace `kyverno` with `CreateNamespace=true`;
  - `ServerSideApply=true`, because Kyverno's CRDs exceed the client-side apply annotation limit.
- **Reports disabled.** `reportsController.enabled: false`, together with the admission, aggregate,
  policy and ValidatingAdmissionPolicy reports and the background scan, which only produce reports.
  Nothing consumes policy reports: the API Audit Log and the Flight Recorder are the evidence
  records (§14). Rendered: the admission, background and cleanup controllers at 1 replica each; no
  reports-controller workload.
- **Policies.** Both ClusterPolicies are removed from Git. K1–K6 arrive in M3, in Enforce with
  `failurePolicy: Fail` (§11, §25). Until then, the rebuilt cluster has Kyverno and no NEXUS policy.
- The AppProject admits the Kyverno chart repository. The render check caught its absence.

## Rationale
- A pinned chart and values in Git make the trusted enforcement base reproducible.
- Disabling a component that nobody consumes removes the one crashing pod. That serves the M0
  verify-state check "no failing pods" better than tuning it.

## Tradeoff
There are no policy reports for debugging. Admission denials remain visible in API responses and in
the API Audit Log (RequestResponse for the operator, §14), which is where NEXUS attributes blocks.
