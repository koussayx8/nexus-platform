# NEXUS — Current State

Generated 2026-09-26T14:10:54Z by `scripts/verify-state.sh`. Never hand-edited (spec §25).

### M1 — Applications Synced and Healthy — PASS
```
root: sync=Synced health=Healthy
platform: sync=Synced health=Healthy
kyverno: sync=Synced health=Healthy
observability: sync=Synced health=Healthy
sample-api-dev: sync=Synced health=Healthy
sample-api-prod: sync=Synced health=Healthy
```
### M2 — Exactly one default Grafana datasource — PASS
```
isDefault:true count across labelled ConfigMaps = 1
```
### M3 — No Loki, Crossplane, or sample-db — PASS
### M4 — sample-api digest and /metrics — PASS
```
nexus-dev: Git-pinned digest from origin/experiment/dev-state:overlays/dev/kustomization.yaml = sha256:45e7a88c950a40fd6ce37a6544d95bc435c76a6aa5af2eefcc9463363150d57c
nexus-dev: ready pods running the pinned digest = 2
nexus-dev: /metrics HTTP 200
nexus-prod: Git-pinned digest from origin/main:overlays/prod/kustomization.yaml = sha256:45e7a88c950a40fd6ce37a6544d95bc435c76a6aa5af2eefcc9463363150d57c
nexus-prod: ready pods running the pinned digest = 2
nexus-prod: /metrics HTTP 200
```
### M5 — Namespace autonomy levels (ADR-018) — PASS
```
nexus-prod: label=1 want=1
nexus-data: label=0 want=0
nexus-dev: label=0 want=0
nexus-system: label=<unset> want=<unset>
nexus-reasoner: label=<unset> want=<unset>
nexus-load: label=<unset> want=<unset>
```
### M6 — Pod readiness (Succeeded pods skipped) — PASS
```
Succeeded pods skipped: 1
every non-Succeeded pod: all containers ready
```
### M7 — Audit log probe (§14) — PASS
```
probe event 'verify-state-probe-1790431866-3239' found in /var/log/nexus-audit/audit.log
apiserver_audit_event_total: before=6123 after=6126
```
### M8 — Kill Switch active — PASS
```
nexus-killswitch state=active
```

## Summary

8 passed, 0 failed.
