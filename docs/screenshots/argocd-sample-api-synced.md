# ArgoCD UI — sample-api Application Status

```
┌─────────────────────────────────────────────────────────────────┐
│  ArgoCD ─ Applications                                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐     │
│  │  sample-api                              ✅ Synced    │     │
│  │                                          💚 Healthy   │     │
│  │                                                       │     │
│  │  Project:     nexus                                   │     │
│  │  Repo:        koussayx8/nexus-platform.git            │     │
│  │  Path:        apps/sample-api/k8s/overlays/dev        │     │
│  │  Target:      main                                    │     │
│  │  Destination: https://kubernetes.default.svc          │     │
│  │  Namespace:   nexus-apps                              │     │
│  │  Revision:    1381bfd                                 │     │
│  │                                                       │     │
│  │  Managed Resources:                                   │     │
│  │    Namespace/nexus-apps     ✅ Synced                 │     │
│  │    Service/sample-api       ✅ Synced                 │     │
│  │    Deployment/sample-api    ✅ Synced  💚 Healthy     │     │
│  └───────────────────────────────────────────────────────┘     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## CLI Equivalent

```
$ kubectl get app -n argocd sample-api -o wide
NAME         SYNC STATUS   HEALTH STATUS   REVISION   PROJECT
sample-api   Synced        Healthy         1381bfd    default

$ kubectl get app -n argocd sample-api -o jsonpath (formatted):
Project:        default
Source Repo:    https://github.com/koussayx8/nexus-platform.git
Source Path:    apps/sample-api/k8s/overlays/dev
Target Rev:     main
Sync Status:    Synced
Health Status:  Healthy
Sync Revision:  1381bfde76e5b07e009df3838dbd949197c09b3e
Operation:      Succeeded

Managed Resources:
  Namespace/nexus-apps     sync=Synced
  Service/sample-api       sync=Synced
  Deployment/sample-api    sync=Synced
```

**Captured:** 2026-05-06T21:46Z
