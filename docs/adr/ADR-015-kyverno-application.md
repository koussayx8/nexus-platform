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

## Addendum (2026-09-26): perpetual `OutOfSync` from `ServerSideApply=true`, fixed with `ServerSideDiff=true`

The real rebuild's first attempt surfaced this: `kyverno` (and, as a knock-on, `root`, which tracks
the `kyverno` Application object) stayed `OutOfSync`/`Healthy` indefinitely. `operationState` showed
every sync attempt actually **succeeding** (`phase: Succeeded`) — the sync itself was never the
problem; a diff reappeared immediately after each one.

Root cause, confirmed by a full field-by-field diff of live vs. rendered manifest, not assumed:
`ServerSideApply=true` (above) auto-enables ArgoCD's "Structured-Merge Diff" comparison strategy
(confirmed against [ArgoCD's Diff Strategies doc](https://argo-cd.readthedocs.io/en/stable/user-guide/diff-strategies/),
which documents exactly this: that strategy is "automatically applied when enabling Server-Side
Apply sync option," has "some challenges... for CRDs that define default values," and is being
"discontinued in favour of Server-Side Diff"). Eleven of the chart's `policies.kyverno.io` CRDs
(the CEL-based policy types) come from a different subchart (`kyverno-api`) than the rest
(`crds`), and render `metadata.labels`/`metadata.annotations` as literal `{}` instead of the real
`app.kubernetes.io/*` labels the `crds` subchart's CRDs get. `--show-managed-fields=true` (kubectl
hides this by default — the first check without it wrongly suggested nothing had applied these
objects at all) confirms `argocd-controller` *does* Server-Side-Apply both the broken and the
healthy CRDs identically; the difference is that the API server drops a genuinely-empty map from
the stored object regardless of who applied it, while a populated map survives — so only the
`kyverno-api` subchart's CRDs end up with a permanent desired-(`{}`)-vs-live-(absent) gap.
(`spec.conversion: {strategy: None}` is *also* apiserver-defaulted on every CRD checked, both
broken and healthy alike — confirmed identical on both, so it is not the differentiator.)

**Fix:** `argocd.argoproj.io/compare-options: ServerSideDiff=true` on the `kyverno` Application
(an annotation, not a sync option — compatible with `ServerSideApply=true`, which controls how
objects are *applied*, not how diffs are *compared*). Server-Side Diff runs an actual dry-run
Server-Side Apply against the real API server to compute the comparison, so the predicted state it
diffs against already reflects the same empty-map normalization a real apply produces — resolving
the false permanent diff. Chosen over `ignoreDifferences` (which would also have worked, scoped to
`jsonPointers: [/metadata/labels, /metadata/annotations]` on the 11 CRDs by name) because it is the
mechanism ArgoCD's own docs name as the direct replacement for the strategy causing the problem,
"Stable (Since v3.1.0)" — well within our pinned v3.3.8 — rather than a workaround for one
subchart's specific rendering gap. Applied at the Application level, not system-wide via
`argocd-cm`, consistent with the standing rule that shared ArgoCD configuration stays as tight as
possible.

The `kyverno-migrate-resources` Job that also showed a non-`Synced` status is unrelated: it is a
Helm `post-upgrade` hook (`helm.sh/hook: post-upgrade`, confirmed via its own annotations), already
completed successfully, and Helm/ArgoCD hooks are not continuously diffed against Git the way
regular tracked resources are — its blank status reflects that, not a problem. `verify-state.sh`'s
pod-readiness check already skips `Succeeded` pods for exactly this kind of one-shot resource.
