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
