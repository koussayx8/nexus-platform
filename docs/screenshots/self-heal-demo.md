# Self-Heal Demonstration — ArgoCD Restores After Manual Scale-to-0

```
=== SELF-HEAL DEMONSTRATION ===
Timestamp: 2026-05-06T21:46:58Z

--- STEP 1: Current state ---
$ kubectl get deploy sample-api -n nexus-apps
Replicas: 1, Ready: 1

--- STEP 2: Manually scaling to 0 (simulating outage) ---
$ kubectl scale deploy/sample-api -n nexus-apps --replicas=0
deployment.apps/sample-api scaled
Scale command sent at: 2026-05-06T21:46:59Z

--- STEP 3: 5 seconds later (pods terminating) ---
$ kubectl get deploy sample-api -n nexus-apps
Replicas: 1, Ready: <none>      ← ArgoCD already reverting spec

--- STEP 4: 40 seconds later (ArgoCD self-heal complete) ---
Check at: 2026-05-06T21:47:39Z
$ kubectl get deploy sample-api -n nexus-apps
Replicas: 1, Ready: 1

$ kubectl get pods -n nexus-apps
NAME                          READY   STATUS    AGE
sample-api-5f55798f69-lbgms   1/1     Running   40s

$ kubectl get app -n argocd sample-api
Sync: Synced, Health: Healthy

--- RESULT ---
ArgoCD detected drift within ~5 seconds.
Pod fully restored and healthy within 40 seconds.
Manual kubectl changes are automatically reverted.
Git is the single source of truth. ✅
```

**Captured:** 2026-05-06T21:47Z
