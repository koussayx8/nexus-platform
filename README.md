# NEXUS — AI-Native Internal Developer Platform

[![CI Pipeline](https://github.com/koussayx8/nexus-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/koussayx8/nexus-platform/actions/workflows/ci.yml)

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
chaos engineering experiments with reproducible benchmark packs.

## Key Architecture Decisions

- **Autonomy Ladder** — Five-level trust model (Observe → Diagnose → Recommend → Approve → Auto-Heal) for graded AI autonomy ([ADR-003](docs/ADR-003-autonomy-ladder.md))
- **Incident Flight Recorder** — Immutable audit trail for every AI decision ([ADR-009](docs/ADR-009-incident-flight-recorder.md))
- **Security-hardened CI** — Ruff + Semgrep + GitLeaks + Trivy + Cosign ([ADR-002](docs/ADR-002-ci-pipeline-design.md))

## Stack

| Layer | Technology |
|-------|------------|
| IDP | Backstage + Crossplane + ArgoCD |
| Security | Vault + Kyverno + Cosign + Falco |
| Observability | OpenTelemetry + Grafana Cloud |
| AI | Groq Llama 70B + LSTM + ChromaDB |
| Infra | k3s (dev) + DigitalOcean DOKS (prod) |
| CI/CD | GitHub Actions + GHCR + Trivy + Cosign |

## Repository Structure

```
nexus-platform/
├── apps/
│   └── sample-api/         # FastAPI service (CI/CD validation)
│       ├── k8s/             # Kustomize manifests (base + overlays)
│       ├── main.py          # Health/ready endpoints
│       ├── Dockerfile       # Multi-stage, hardened
│       └── test_main.py     # Pytest suite
├── platform/                # Platform components (ArgoCD, Backstage, etc.)
├── infra/                   # Infrastructure (k3s, Terraform)
├── ai/                      # AI components (agent, anomaly-detector, RAG)
├── docs/                    # ADRs + contribution guide
└── .github/workflows/       # CI pipeline
```

## Status

- [x] Week 0 — Environment setup (k3s, ArgoCD, API keys, venv)
- [x] Week 1 — CI pipeline + sample-api deployed to k3s
- [ ] Week 2 — ArgoCD GitOps + Terraform for DigitalOcean DOKS
- [ ] ...

## Quick Start (Dev)

```bash
# Deploy sample-api to local k3s
kubectl apply -k apps/sample-api/k8s/overlays/dev/

# Verify
kubectl get pods -n nexus-apps
curl http://<pod-ip>:8000/health
```
