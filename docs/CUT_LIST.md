# NEXUS Scope Contract — Locked Week 0

## KEEP (Core — non-negotiable)
- Backstage IDP + Crossplane (K8s provider) + ArgoCD
- GitHub Actions: Semgrep + Trivy + Cosign + GitLeaks + Kyverno
- HashiCorp Vault dynamic secrets
- OpenTelemetry + Grafana Cloud (Prometheus + Loki + Tempo)
- Anomaly detection: Z-score + Isolation Forest + LSTM (all three)
- LangChain agent (Groq primary, Claude fallback)
- kopf self-healing operator (Python)
- 3 chaos experiments: PodDelete + CPUHog + NetworkLatency
- 3-way MTTR baseline: Manual vs Static Rules vs NEXUS AI

## CUT (Future work — do not add back)
- Istio → replaced by Cilium NetworkPolicies
- Argo Rollouts → Chapter 6 future work
- OPA Gatekeeper → Kyverno only (ADR-001)
- Prophet cost forecasting → disconnected from thesis
- Harbor registry → using ghcr.io
- Redpanda/Kafka → using Redis Streams

## DEFER (Only if ahead of schedule at Week 10)
- Crossplane Azure provider
- DORA metrics pipeline (Week 19)
