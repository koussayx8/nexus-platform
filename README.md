# NEXUS — AI-Native Internal Developer Platform

**PFE Project — Engineering Thesis**
**Author:** Koussay Belhouchet — ESPRIT
**Duration:** 24 weeks

## What NEXUS Is

A Kubernetes-native Internal Developer Platform implementing
closed-loop AIOps principles. Combines developer self-service
(Backstage + Crossplane + ArgoCD), DevSecOps automation
(Vault + Kyverno + Cosign), and AI-driven incident response
(LSTM anomaly detection + LangChain agent + kopf operator).

## Research Contribution

Three-way anomaly detection comparison (Z-score vs Isolation Forest vs LSTM)
and LLM-based incident reasoning operator — measured via controlled
chaos engineering experiments.

## Stack

| Layer | Technology |
|-------|-----------|
| IDP | Backstage + Crossplane + ArgoCD |
| Security | Vault + Kyverno + Cosign + Falco |
| Observability | OpenTelemetry + Grafana Cloud |
| AI | Groq Llama 70B + Claude + LSTM + ChromaDB |
| Infra | k3s (dev) + DigitalOcean DOKS (prod) |

## Status

- [x] Week 0 — Environment setup
- [ ] Week 1 — k3s + GitHub Actions pipeline
- [ ] Week 2 — ArgoCD GitOps
- [ ] ...
