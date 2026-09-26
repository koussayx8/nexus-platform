# ADR-014: Converge in Git, Then Rebuild

## Status: Accepted

## Context
The pre-M0 cluster diverges from the frozen architecture in several ways:
- Loki and Crossplane are installed;
- Kyverno and Crossplane exist only as Helm releases, outside Git;
- `sample-db` is a busybox sleep loop;
- there is no root Application.

It holds no persistent data (no PV, no PVC; snapshot `20260925T064759Z`, `10b`), and M0-5 rebuilds
it anyway. Removing things by hand would mean editing finalizers, running `helm uninstall` and
cascading Application deletions: none of it through Git, and all of it lost at the rebuild.

## Decision
- **M0-4 changes Git only.** Git describes the complete target state, validated offline by the
  required check: `kustomize build`, `helm template` with the value files from Git, an AppProject
  admission check and `kubeconform -strict` (ADR-012). Nothing is applied to the live cluster; it
  stays untouched as the "before" reference.
- **Branch flow.** Feature branches merge into `dev` by PR, and `dev` merges into `main` by PR only
  at a gate. M0-4 changes paths the live cluster tracks on `main` (`apps/sample-api/k8s`,
  `platform/argocd`), so it accumulates on `dev` until the rebuild.
- **M0-5 order:**
  1. final capture of the old cluster;
  2. the owner uninstalls k3s;
  3. the `dev` → `main` PR is merged;
  4. `main` is merged into `experiment/dev-state`;
  5. `bootstrap.sh`.

  M0 is done when `verify-state.sh` exits 0.
- **Root Application** `platform/argocd/root.yaml` owns `platform/argocd/applications/`. It sits
  outside that directory, so it never manages itself. It is applied only by `bootstrap.sh`, after
  the AppProject, and never to the pre-M0 cluster, where its first sync would prune and cascade.
- **Target Application set:** `platform`, `kyverno`, `observability`, `sample-api-dev` (tracks
  `experiment/dev-state`) and `sample-api-prod`. There is no `loki` and no
  `crossplane-infrastructure`. `dependency-db` arrives in M1 with `/items`; `nexus` arrives in M1
  with the operator.

## Settled facts (read-only, 2026-09-25)

| Question | Answer | Consequence for `bootstrap.sh` |
| --- | --- | --- |
| Is the ghcr package public? | Yes: `gh api /users/koussayx8/packages/container/nexus-platform%2Fsample-api` gives `visibility=public`, and an anonymous manifest GET of the pinned digest returns 200 | No image pull secret |
| Is the repository public? | Yes (`visibility=public`) | No ArgoCD repository credentials |
| Does any Ingress use Traefik? | No: no Ingress, Traefik IngressRoute or Gateway API route exists; the Traefik LoadBalancer routes nothing | k3s is installed with `--disable traefik --disable servicelb` |

## Rationale
- Every change reaches the rebuilt cluster through the same path the thesis claims: pull request →
  green checks → ArgoCD.
- A rebuild from Git proves the state is reproducible, which a hand-cleaned cluster cannot.

## Tradeoff
Until M0-5 the live cluster keeps running the old state: Loki, Crossplane, the exposed Grafana
password and the busybox `sample-db`. This is accepted: nothing in it is persistent or reachable
off this machine (ADR-010).

## Addendum (2026-09-26): `root` stuck permanently `OutOfSync` — a second `ServerSideDiff=true`

The final from-empty rebuild attempt surfaced this after the `kyverno` fix (ADR-015 addendum)
converged: `root` itself stayed `OutOfSync`/`Healthy` indefinitely. Polled every 10-13s for 5.5
minutes: zero transitions — permanent, not intermittent.

### The mechanism — confirmed from ArgoCD v3.3.8's actual source, not just docs

`root`'s only non-`Synced` tracked resource is the `Application/kyverno` object itself. A full
`.spec` diff of desired (`origin/main:platform/argocd/applications/kyverno.yaml`) vs. live: zero
difference. The entire diff is `.metadata.finalizers`: live has three
(`resources-finalizer.argocd.argoproj.io`, `pre-delete-finalizer.argocd.argoproj.io`,
`pre-delete-finalizer.argocd.argoproj.io/cleanup`); desired declares only the first. Only `kyverno`
among the five child Applications has the two extra ones.

**What adds `pre-delete-finalizer.argocd.argoproj.io` — confirmed from source
(`controller/appcontroller.go`, tag `v3.3.8`):**
```go
// Update finalizers BEFORE persisting status to avoid race condition where app shows "Synced"
// but doesn't have finalizers yet, which would allow deletion without running pre-delete hooks
if (compareResult.hasPreDeleteHooks != app.HasPreDeleteFinalizer() ||
    compareResult.hasPreDeleteHooks != app.HasPreDeleteFinalizer("cleanup")) &&
    app.GetDeletionTimestamp() == nil {
    if compareResult.hasPreDeleteHooks {
        app.SetPreDeleteFinalizer()
        app.SetPreDeleteFinalizer("cleanup")
    } else { ... }
    ctrl.updateFinalizers(app)
}
```
**Under what conditions:** every single reconciliation of an Application, unconditionally,
whenever `compareResult.hasPreDeleteHooks` is true for its *desired* (rendered) manifest — this is
a deliberate safety feature (the comment above says why), not a bug, and it runs regardless of
which diff strategy that Application itself uses. `kyverno`'s chart renders two
`helm.sh/hook: pre-delete` Jobs (`kyverno-rm-webhooks`, `kyverno-scale-to-zero`,
`kyverno/templates/hooks/pre-delete-*.yaml`) — ArgoCD's Helm-hook mapping treats these the same as
its own `PreDelete` hook annotation ([Sync Phases and Waves doc](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/),
`PreDelete Hooks` section) — so `kyverno` gets the finalizer on every reconciliation; the other
four Applications render no such hooks, so they never do.

**Consequence, also documented on that same page:** deleting the `kyverno` Application now runs
these two hooks first — confirmed in source too, `finalizeApplicationDeletion()`: if
`app.HasPreDeleteFinalizer()`, it calls `executePreDeleteHooks()` and, while not done, returns
without removing the finalizer, blocking deletion. A hanging or failing hook (a stuck Job) would
block `kyverno`'s deletion — and by extension anything waiting on it — until the hook succeeds or
is removed by hand. Not a change in scope, but a real operational fact worth knowing before anyone
ever runs `kubectl delete application kyverno` or an equivalent prune.

**Why `root` was `Synced` earlier (M0-5 rebuild sequence step 6b) with the same config — UNKNOWN,
precisely.** The finalizer-setting logic above is keyed to *`kyverno`'s own* reconciliation, not
to when `root` last compared the `kyverno` Application object — so it does not depend on step 6b's
timing in any way the source rules out. Two explanations are equally consistent with the evidence
and neither can be distinguished from what was captured at the time: either the finalizer simply
hadn't been set yet at that specific point in `kyverno`'s own reconciliation history (fewer cycles
had run since its `ServerSideDiff` fix converged), or `root` was already silently on the same
permanent trajectory and the point-in-time check in step 6b (no multi-minute stability poll was run
then, unlike this time) simply caught it in a moment before the drift. Recorded as unresolved
rather than guessed.

### Fix

`argocd.argoproj.io/compare-options: ServerSideDiff=true` on `root` (`platform/argocd/root.yaml`)
— the same mechanism just used for `kyverno`, and validated the same way before proposing it: a
server-side dry-run apply with `--field-manager=argocd-controller` (root's own applier identity)
against the desired `kyverno` Application manifest predicts
`finalizers: [resources-finalizer..., pre-delete-finalizer..., pre-delete-finalizer.../cleanup]` —
identical to the real live object, confirming Server-Side Apply preserves fields owned by other
managers rather than stripping them, so Server-Side Diff's dry-run-based comparison will see no
difference here either. `ignoreDifferences` on `/metadata/finalizers` for `kind: Application` would
also have worked; not chosen, for the same reason as `kyverno`'s fix — this addresses the actual
mechanism, not one Application's specific symptom.
