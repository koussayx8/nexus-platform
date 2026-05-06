# ADR-004: GitOps Strategy — ArgoCD App-of-Apps with Self-Heal

**Status:** Accepted
**Date:** 2026-05-06
**Author:** Koussay Belhouchet

## Context

NEXUS needs a GitOps delivery model where Git is the single source of truth for cluster
state. ArgoCD is already deployed (7/7 pods healthy). The sample-api is currently deployed
via manual `kubectl apply -k` — this must be replaced with ArgoCD ownership.

Key questions this ADR answers:
1. App-of-apps vs single Application?
2. Sync policy: auto vs manual?
3. Pruning and self-heal behavior?
4. Namespace ownership model?
5. Dev → prod promotion path?

## Decision

### 1. Single Application Per Service (Not App-of-Apps — Yet)

**Decision:** Start with one ArgoCD `Application` per service. Add app-of-apps when
we have ≥3 services.

**Rationale:**
- App-of-apps adds indirection that is premature with one service
- A single Application is easier to debug, demonstrate, and explain in a thesis
- Migration to app-of-apps is trivial: create a parent Application pointing at a directory of Application manifests
- The parent Application will live in `platform/argocd/` when needed

**Trigger for migration:** When the third service is added to `apps/`, create the
app-of-apps pattern. Document this as an appendix to ADR-004.

### 2. Sync Policy: Automated with Self-Heal

```yaml
syncPolicy:
  automated:
    prune: true       # Delete resources removed from Git
    selfHeal: true    # Revert manual cluster changes to match Git
  syncOptions:
    - CreateNamespace=true
    - PrunePropagationPolicy=foreground
    - PruneLast=true
  retry:
    limit: 3
    backoff:
      duration: 5s
      factor: 2
      maxDuration: 1m
```

**Rationale:**
- `selfHeal: true` — proves GitOps discipline: if someone manually scales to 0,
  ArgoCD reverts it. This is a thesis demonstration point.
- `prune: true` — resources deleted from Git are removed from cluster.
  Without pruning, GitOps is incomplete (orphaned resources accumulate).
- `PruneLast: true` — delete orphaned resources only after new resources are healthy.
  Prevents downtime during manifest restructuring.
- `retry` with backoff — handles transient failures (image pull, quota limits).

### 3. Namespace Ownership

- ArgoCD creates the namespace (`CreateNamespace=true`)
- The namespace definition lives in the Kustomize base
- ArgoCD `destination.namespace` matches the Kustomize namespace
- **One namespace per environment:** `nexus-apps` (dev), `nexus-apps-prod` (future)

### 4. Dev → Prod Promotion Path

```
Git commit to main
    ↓
CI pipeline (lint → test → SAST → secrets → build → scan → sign)
    ↓
Image pushed to ghcr.io (tagged with SHA + latest)
    ↓
ArgoCD detects change (3-minute poll or webhook)
    ↓
Dev overlay applied to k3s (auto-sync)
    ↓
[Future] Promote: update prod overlay image tag → PR → merge → ArgoCD syncs to DOKS
```

**Current state:** Only the dev path exists. Prod promotion will use Kustomize
image tag overrides in `overlays/prod/kustomization.yaml`.

**Image tag strategy:**
- Dev: `latest` (auto-updated by CI on every push to main)
- Prod: pinned SHA tag (changed via PR to `overlays/prod/kustomization.yaml`)

### 5. Repository Structure for ArgoCD

```
platform/argocd/
├── applications/
│   └── sample-api.yaml     # ArgoCD Application manifest
└── app-of-apps.yaml         # [Future] Parent Application
```

Application manifests live in `platform/argocd/applications/`, not alongside the
app's Kustomize manifests. This separates "what to deploy" (Kustomize) from
"how ArgoCD manages it" (Application manifest).

## Consequences

- Git becomes the only way to change cluster state (manual kubectl is overridden)
- Self-heal provides a built-in demo: `kubectl scale → ArgoCD reverts` (proves GitOps)
- Pruning prevents resource drift and orphaned objects
- Clear promotion path: dev auto-deploys, prod requires explicit image tag PR
- Single-app-per-service keeps Week 2 simple; app-of-apps is documented for future
- ArgoCD Application manifests are version-controlled (GitOps for GitOps)

## References

- ArgoCD Declarative Setup: https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/
- ArgoCD App of Apps: https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/
- ADR-002 (CI Pipeline Design — image tagging strategy)
- ADR-003 (Autonomy Ladder — self-heal interacts with autonomy levels)
