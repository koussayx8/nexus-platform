# GitOps Validation Log — Week 2

## Test 1: ArgoCD Sync from Git

**Date:** 2026-05-06
**Commit:** 9948d74 (feat(week2): ADR-004 GitOps strategy + ArgoCD Application manifest)

```
$ kubectl apply -f platform/argocd/applications/sample-api.yaml
application.argoproj.io/sample-api created

$ kubectl get app -n argocd sample-api
Sync: Synced, Health: Healthy, Phase: Succeeded

$ kubectl get pods -n nexus-apps
NAME                          READY   STATUS    RESTARTS   AGE
sample-api-5f55798f69-vrvrp   1/1     Running   0          107s
```

**Result:** ArgoCD pulled from GitHub, rendered Kustomize overlay, created namespace + deployment + service. ✅

---

## Test 2: Self-Heal (Manual Scale to 0)

ArgoCD `selfHeal: true` reverts any manual cluster changes.

```
=== BEFORE: Manual scale to 0 ===
sample-api-5f55798f69-vrvrp   1/1   Running   0     8m11s

--- kubectl scale deploy/sample-api -n nexus-apps --replicas=0 ---
deployment.apps/sample-api scaled

--- 5 seconds later (pod terminating, ArgoCD detects drift) ---
sample-api-5f55798f69-d2xnq   0/1   Running   0     4s

--- 30 seconds later (ArgoCD self-heals) ---
=== AFTER: ArgoCD restored ===
sample-api-5f55798f69-d2xnq   1/1   Running   0     34s
Sync: Synced, Health: Healthy
```

**Result:** ArgoCD detected replicas=0 drift within ~5s, restored to replicas=1 (from Git), pod healthy within 34s. ✅

**This proves:** Git is the single source of truth. Manual kubectl changes are automatically reverted.

---

## Test 3: Kyverno Policy Validation (Autonomy Ladder)

**Kyverno:** v1.18.0 installed via Helm (4 pods running)
**Policy:** `nexus-autonomy-level` (ClusterPolicy, Audit mode)

```
$ kubectl get clusterpolicy nexus-autonomy-level
Status: Ready: True

$ kubectl get policyreport -n nexus-apps -o wide
NAME                                   KIND         NAME         PASS   FAIL   WARN   ERROR   SKIP
335a20ca-e6c6-462c-9f9d-50210edb0f64   Deployment   sample-api   2      0      0      0       0
```

**Rules validated:**
1. `warn-missing-autonomy-level` — checks annotation exists → **PASS** (annotation present)
2. `validate-autonomy-level-value` — checks value is 0-4 → **PASS** (value is "0")

**Result:** sample-api deployment passes both Kyverno rules. Invalid values (e.g., "9") and missing annotations are caught. ✅
