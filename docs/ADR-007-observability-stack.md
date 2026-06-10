# ADR-007: Observability Stack — Prometheus + Grafana + Loki on k3s

**Status:** Accepted
**Date:** 2026-06-10
**Author:** Koussay Belhouchet

## Context

NEXUS has established GitOps (ArgoCD), policy enforcement (Kyverno), and infrastructure
provisioning (Crossplane) foundations. The platform currently operates without centralized
observability — metrics and logs are either absent or inspected manually via `kubectl logs`.
Week 5 must deliver a complete observability layer that:

1. Collects cluster and application metrics with Prometheus-compatible scraping
2. Aggregates container logs from the k3s containerd runtime
3. Provides visual dashboards for the RED method (Rate, Errors, Duration)
4. Integrates with our existing GitOps pipeline (ArgoCD-managed)
5. Respects k3s resource constraints (single-node, 8GB RAM limit)
6. Enforces platform standards (SHA-pinned images, autonomy-level annotations)
7. Supports the Autonomy Ladder with metrics-driven anomaly detection

Key questions this ADR answers:
1. Why kube-prometheus-stack over vanilla Prometheus or VictoriaMetrics?
2. How do we handle k3s-specific monitoring gaps (etcd, scheduler, controller-manager)?
3. What is our log aggregation strategy (Loki vs EFK vs fluent-bit only)?
4. How does the stack integrate with ArgoCD GitOps?
5. What is our namespace and resource constraint strategy?
6. How do we handle image SHA-pinning for Helm charts?
7. What is our dashboard and alerting approach?
8. How do autonomy-level annotations interact with observability?

## Decision

### 1. kube-prometheus-stack as the Metrics Layer

**Decision:** Deploy the Prometheus Community `kube-prometheus-stack` Helm chart for
metrics collection, storage, and alerting. This bundles Prometheus, Grafana,
Alertmanager, and node-exporter in a single maintained chart.

**Rationale:**
- Single chart provides Prometheus + Grafana + Alertmanager + node-exporter + kube-state-metrics
- CRD-based configuration (ServiceMonitor, PrometheusRule) fits our GitOps-native approach
- Community-maintained with regular security updates and k3s compatibility patches
- Grafana includes pre-built Kubernetes dashboards (cluster, node, pod, workload)
- Native Helm values allow resource tuning for single-node k3s constraints

**k3s-specific adaptations:**
k3s embeds control plane components (etcd, kube-scheduler, kube-controller-manager,
kube-proxy, kube-apiserver) into a single binary. These components do not expose
standard Kubernetes Service endpoints, so the default ServiceMonitors fail to scrape.

**Decision:** Disable the following ServiceMonitors in `values.yaml`:
- `kubeEtcd.enabled: false`
- `kubeScheduler.enabled: false`
- `kubeControllerManager.enabled: false`
- `kubeProxy.enabled: false`
- `kubeApiServer.enabled: false`

**Rationale:**
- k3s does not expose these components as separate Services with scrape endpoints
- Enabling them produces false-positive "TargetDown" alerts and noisy error logs
- Node-level metrics (node-exporter) and workload metrics (kube-state-metrics) remain fully functional
- Control plane health is inferred indirectly via node readiness and API server responsiveness

```yaml
# values.yaml — k3s-specific disables
kubeEtcd:
  enabled: false
kubeScheduler:
  enabled: false
kubeControllerManager:
  enabled: false
kubeProxy:
  enabled: false
kubeApiServer:
  enabled: false
```

### 2. Loki Stack for Log Aggregation

**Decision:** Deploy Grafana Loki (simple scalable mode) with Promtail as the log
aggregation layer. Loki runs in the same namespace as Prometheus/Grafana for unified
access control.

**Rationale:**
- Loki is label-indexed (like Prometheus) — same query mental model for metrics and logs
- Promtail is lightweight and Kubernetes-native (DaemonSet on every node)
- No full-text indexing means lower resource usage than Elasticsearch
- Native Grafana integration — logs and metrics in the same UI
- Helm chart (`grafana/loki-stack`) bundles Loki + Promtail + optional Grafana

**Promtail containerd configuration:**
k3s uses containerd with the CRI logging format. Pod logs are written to
`/var/log/pods/<namespace>_<pod-name>_<pod-uid>/<container-name>/`. Promtail must
mount `/var/log/pods` as a hostPath volume and parse the CRI timestamp prefix.

```yaml
# Promtail values snippet
config:
  snippets:
    scrapeConfigs: |
      - job_name: kubernetes-pods
        kubernetes_sd_configs:
          - role: pod
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
            action: keep
            regex: true
        pipeline_stages:
          - cri: {}
```

### 3. ArgoCD Integration: Helm Application with ignoreDifferences

**Decision:** Manage the observability stack as an ArgoCD `Application` using the
Helm source type. Configure `ignoreDifferences` for Prometheus-operator-generated
secrets and webhook certificates.

**Rationale:**
- The Prometheus operator generates TLS secrets and webhook configurations dynamically
- Without `ignoreDifferences`, ArgoCD shows constant "Out of Sync" for healthy resources
- Helm source type allows values file override via `valuesFiles` in the Application spec
- Follows the same pattern as Crossplane (ADR-006) and sample-api (ADR-004)

```yaml
# platform/argocd/applications/observability.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: observability
  namespace: argocd
  labels:
    app.kubernetes.io/part-of: nexus-platform
    nexus.io/managed-by: argocd
spec:
  project: nexus
  source:
    repoURL: https://prometheus-community.github.io/helm-charts
    chart: kube-prometheus-stack
    targetRevision: 69.8.0
    helm:
      valuesFiles:
        - apps/observability/values/prometheus-values.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: monitoring
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
  ignoreDifferences:
    - group: ""
      kind: Secret
      name: prometheus-operator-admission
      jsonPointers:
        - /data
```

### 4. Namespace Strategy: `monitoring` with PodSecurity Restricted

**Decision:** Use a dedicated `monitoring` namespace for all observability components
(Prometheus, Grafana, Alertmanager, Loki, Promtail). Apply the same PodSecurity
restricted profile used in `crossplane-system`.

**Rationale:**
- Separates observability concerns from application workloads (`nexus-apps`) and
  platform infrastructure (`crossplane-system`)
- Network policies can restrict cross-namespace access (Grafana → Prometheus only)
- PodSecurity restricted profile aligns with our security posture (ADR-003, Kyverno)
- ArgoCD AppProject `nexus` is expanded to include `monitoring` in destinations

```yaml
# Namespace manifest (committed to Git)
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
    nexus.io/autonomy-level: "0"
```

### 5. Image SHA-Pinning Strategy

**Decision:** Pin the kube-prometheus-stack chart version to an exact semver
(`69.8.0`) and rely on the chart's internal image pinning. For Loki, pin the
`grafana/loki-stack` chart version and override individual image repositories with
SHA digests where the chart exposes them.

**Rationale:**
- Helm charts pin their default images to specific digests in `values.yaml` — using an
  exact chart version is equivalent to SHA-pinning the bundled images
- Where custom images are needed (e.g., custom Grafana plugins), use the
  `repository@sha256:<DIGEST>` syntax with `image.ignoreTag: true` (pattern from ADR-006)
- This satisfies Hard Rule #2 (no `:latest` tags) while using the standard Helm installation path
- Chart version bumps are explicit Git changes — fully auditable

```yaml
# values.yaml — explicit image pinning where exposed
grafana:
  image:
    repository: grafana/grafana@sha256:<DIGEST>
    tag: ""  # ignored when digest is present
prometheus:
  prometheusSpec:
    image:
      repository: quay.io/prometheus/prometheus@sha256:<DIGEST>
      tag: ""
```

### 6. Resource Constraints for Single-Node k3s

**Decision:** Cap total observability stack memory at ~2GB and CPU at ~1.5 cores.
Use Prometheus retention of 15 days / 10GB disk. Disable redundant exporters.

**Rationale:**
- k3s node has 8GB RAM total; Crossplane already consumes ~1.07GB (ADR-006)
- sample-api, Backstage, and Kyverno consume additional memory
- Prometheus with 15-day retention is sufficient for thesis demonstration and chaos experiments
- Loki with 7-day retention and no boltdb-shipper (single-node) keeps disk usage bounded

| Component | Memory Limit | CPU Limit | Notes |
|-----------|-------------|-----------|-------|
| Prometheus | 1.2GB | 800m | 15d retention, 10GB PVC |
| Grafana | 256MB | 200m | No alerting UI (rules only) |
| Alertmanager | 128MB | 100m | Single replica (dev) |
| Loki | 512MB | 300m | No replication, filesystem storage |
| Promtail | 128MB | 100m | DaemonSet per node |
| **Total** | **~2.2GB** | **~1.5 cores** | Includes headroom |

```yaml
# values.yaml — resource limits
prometheus:
  prometheusSpec:
    resources:
      limits:
        memory: "1200Mi"
        cpu: "800m"
    retention: "15d"
    retentionSize: "10GB"
grafana:
  resources:
    limits:
      memory: "256Mi"
      cpu: "200m"
```

### 7. Dashboard and Alerting Strategy

**Decision:** Use the RED method (Rate, Errors, Duration) for application dashboards.
Deploy dashboards via Grafana sidecar with ConfigMap auto-import. Define alerts as
Kubernetes `PrometheusRule` CRDs, not Grafana UI alerts.

**Rationale:**
- RED metrics are language-agnostic and work for any HTTP/gRPC service
- sample-api already exposes `/metrics` via `prometheus_fastapi_instrumentator` —
  RED metrics are available without code changes
- Grafana sidecar watches ConfigMaps with `grafana_dashboard: "1"` label and auto-imports
  them — dashboards are GitOps-managed YAML, not manual UI clicks
- `PrometheusRule` CRDs are Kubernetes resources — they live in Git, are validated by
  Kyverno, and are applied by ArgoCD
- Grafana UI alerts are not GitOps-native and are lost on pod restart

**RED dashboard panels:**
- **Rate:** `sum(rate(http_requests_total{job=~"sample-api.*"}[5m]))`
- **Errors:** `sum(rate(http_requests_total{job=~"sample-api.*",status=~"5.."}[5m]))`
- **Duration:** `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{job=~"sample-api.*"}[5m])) by (le))`

```yaml
# Example PrometheusRule for alerting
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: sample-api-alerts
  namespace: monitoring
  labels:
    prometheus: kube-prometheus
    role: alert-rules
spec:
  groups:
    - name: sample-api
      rules:
        - alert: SampleApiHighErrorRate
          expr: |
            sum(rate(http_requests_total{job="sample-api",status=~"5.."}[5m]))
            /
            sum(rate(http_requests_total{job="sample-api"}[5m])) > 0.05
          for: 2m
          labels:
            severity: warning
            nexus.io/autonomy-level: "0"
          annotations:
            summary: "sample-api error rate exceeds 5%"
```

**Grafana dashboard sidecar:**
```yaml
# ConfigMap with dashboard JSON
apiVersion: v1
kind: ConfigMap
metadata:
  name: sample-api-red-dashboard
  namespace: monitoring
  labels:
    grafana_dashboard: "1"
data:
  sample-api-red.json: |
    { ... dashboard JSON ... }
```

### 8. Autonomy-Level Annotations in Observability

**Decision:** Tag all observability resources with `nexus.io/autonomy-level: "0"`.
Include autonomy-level labels in Prometheus alerts for correlation with the
Autonomy Ladder (ADR-003).

**Rationale:**
- Observability components are platform infrastructure — they do not participate in
  self-healing decisions (Level 0 is correct)
- Alert labels include `nexus.io/autonomy-level` so the AI operator knows the trust
  level of the affected service when correlating metrics with incidents
- Kyverno policy `nexus-autonomy-level` (Audit mode) validates that all Deployments
  in `monitoring` carry the annotation

```yaml
# Alert with autonomy-level correlation
- alert: ServiceLatencyHigh
  expr: histogram_quantile(0.95, ...) > 0.5
  labels:
    severity: warning
    nexus.io/autonomy-level: "{{ $labels.nexus_io_autonomy_level }}"
  annotations:
    runbook_url: "https://wiki.nexus/runbooks/high-latency"
```

## Consequences

- **Positive:** Complete observability stack (metrics + logs + dashboards + alerts) is
  GitOps-managed and auditable
- **Positive:** RED dashboards provide immediate visibility into sample-api health
  without custom instrumentation
- **Positive:** PrometheusRule CRDs make alerts version-controlled and reviewable
- **Positive:** Resource limits ensure the stack fits within single-node k3s constraints
- **Positive:** Autonomy-level labels in alerts enable the AI operator to respect
  trust boundaries (ADR-003)
- **Negative:** Disabling k3s control plane ServiceMonitors reduces cluster-level
  observability (no etcd metrics, no scheduler latency)
- **Negative:** 15-day Prometheus retention limits historical analysis for long-term
  trend detection
- **Negative:** Loki filesystem storage is not replicated — logs are lost if the Loki
  pod is deleted without PVC backup
- **Negative:** Alertmanager is single-replica in dev — no high availability for alert
  delivery

## References

- kube-prometheus-stack Helm Chart: https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack
- Grafana Loki Helm Chart: https://github.com/grafana/helm-charts/tree/main/charts/loki-stack
- RED Method: https://grafana.com/blog/2018/08/02/the-red-method-how-to-monitor-your-microservices/
- Prometheus Operator CRDs: https://prometheus-operator.dev/docs/getting-started/introduction/
- k3s Monitoring Notes: https://docs.k3s.io/installation/kube-dashboard
- ADR-004 (GitOps Strategy — ArgoCD sync policy and self-heal)
- ADR-006 (Crossplane Design — SHA-pinning workaround and resource constraints)
- ADR-003 (Autonomy Ladder — alert correlation with trust levels)
- ADR-009 (Incident Flight Recorder — alert events feed the timeline)

(End of file)
