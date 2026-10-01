# ADR-016: The observability Application: Values from Git, No Loki, Persistent Prometheus

## Status: Accepted

## Context
Before M0 (snapshot `20260925T064759Z`), the `observability` Application rendered
kube-prometheus-stack 86.2.2 from **inline** values. A second, drifting copy lived in
`platform/observability/k8s/base/` for manual `helm upgrade` runs. Both held a literal Grafana
admin password (ADR-010) and a Loki datasource. Prometheus used an `emptyDir` with 7-day retention.
NEXUS's own ServiceMonitor, alert and dashboard were deployed by no Application. Three were broken:
- the ServiceMonitor selected `nexus-apps`;
- the alert lacked the `release` label the rule selector needs, and matched `5..` although the
  instrumentator emits `5xx`;
- the dashboard was in HTTP-API format, which Grafana file provisioning rejects ("title cannot be empty").

## Decision
- **Name kept: `observability`** (spec §3 calls it `monitoring`; the rename is not worth the churn).
  Its directory stays `platform/observability/`. Spec v1.1 records the name.
- **Multi-source, no inline values:**
  1. chart `kube-prometheus-stack` **86.2.2**, values from `platform/observability/kube-prometheus-stack-values.yaml`;
  2. this repository as `$values`;
  3. `platform/observability/config`: the ServiceMonitor, alert rules and dashboard.

  The required check fails on inline values (ADR-012).
- **Grafana:** the admin comes from `monitoring/grafana-admin` (`admin-user`, `admin-password`),
  created by `bootstrap.sh` from `~/.nexus/keys.env`; no password is in Git. The datasources are the
  chart's: **Prometheus is the only `isDefault`**, and Alertmanager. **No Loki** (§23).
- **Prometheus:** `retention: 15d`, `retentionSize: 9GB`, on a 10Gi `local-path` PVC
  (volumeClaimTemplate). History now survives WSL restarts, which the M1 Locust baseline and the
  Z-score windows need.
- **NEXUS config fixes:**
  - the ServiceMonitor selects `nexus-dev` and `nexus-prod`;
  - the alert carries `release: observability`, matches grouped `5xx`, excludes `/health`,
    `/ready` and `/metrics`, and computes the ratio per namespace;
  - the dashboard is a top-level model titled "NEXUS sample-api — RED", with per-namespace
    timeseries panels.
- **No `ignoreDifferences`.** The old entries named admission Secrets that the chart never renders.
  The webhooks' `caBundle` is patched by the cert-gen hook Job; the chart renders no `caBundle`, and
  with `ServerSideApply` ArgoCD never owns that field, so selfHeal does not revert it. The pre-M0
  Application, with the same setup, is Synced/Healthy.
- The superseded workload autonomy annotations are removed (ADR-018).

## Evidence (2026-09-25)
- Metric names and labels were read from `/metrics` of the sample-api source at `311ad81` (the
  pinned image), run locally: `http_requests_total{handler,method,status="2xx|4xx|5xx"}` and the
  histogram `http_request_duration_seconds{handler,method,le}`.
- All four PromQL expressions (three panels, one alert) parse on the live Prometheus through
  read-only API queries (`status=success`; 0 series, because the old image exposes no metrics).
- Render: Grafana's `GF_SECURITY_ADMIN_USER` and `GF_SECURITY_ADMIN_PASSWORD` come from Secret
  `grafana-admin`; no Grafana admin Secret is rendered; the datasource ConfigMap has Prometheus
  (`isDefault: true`) and Alertmanager; 0 mentions of Loki.

## Tradeoff
`bootstrap.sh` must create `grafana-admin` before the Application syncs, or Grafana does not start.
M0-5 orders it that way.

## Addendum (2026-10-01, M1b-3): Grafana limits sized to an open dashboard

**Basis (M1-5 run 1, 2026-09-27, `TASKS.md` M1-5).** With one dashboard open through a
port-forward, the `grafana` container was throttled in 99 % of CFS periods at its 200m limit. Its
working set grew from 270 to 483 MiB of 512 MiB. The readiness probe failed 123 times: the chart
sets no `timeoutSeconds`, so Kubernetes' 1 s applies. Liveness killed it once, and `observability`
flapped between Healthy and Progressing.

**Decision.** In `kube-prometheus-stack-values.yaml` under `grafana`:

| Value | Before | After | Why |
|---|---|---|---|
| CPU limit | 200m | **1000m** | the throttling ceiling. A burst is bounded per container, not reserved. |
| CPU request | 100m | **200m** | covers the open-dashboard steady state that hit 200m |
| Memory limit | 512Mi | **1Gi** | 483 MiB peak; 1Gi keeps the 80 % acceptance bound at about 819 MiB |
| Memory request | 256Mi | 256Mi | unchanged |
| `readinessProbe.timeoutSeconds` | unset (1 s) | **5** | the 123 failures were 1 s timeouts under load |
| Liveness | chart default | unchanged | timeout 30 s, failureThreshold 10, initialDelay 60 s (U4, read from the 86.2.2 render) |

**Render (chart 86.2.2, `helm template` before and after):** only `Deployment/observability-grafana`
changes, in exactly these fields. Prometheus, the operator and Alertmanager render identically, so
none of them restarts. Grafana runs 1 replica with RollingUpdate and no PVC (dashboards come from the
sidecar), so a new pod starts before the old one stops.

**Acceptance (live, on the observability gate):** one dashboard open for 15 min. Pass:
- 0 liveness kills and 0 readiness failures;
- CFS throttled periods ≤ 5 % of periods over the window;
- working set ≤ 80 % of the limit;
- `observability` Healthy throughout.

Then a `verify-state.sh` run with no dashboard open. Only then is the "no dashboards during verify
runs" gate rule retired.

**Left as is.** The dashboard and datasource sidecars render with no resources. That is outside this
change and recorded in `TASKS.md` Later.
