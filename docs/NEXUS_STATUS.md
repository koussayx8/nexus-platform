# NEXUS Platform — Living Status File
# Updated by Librarian (GLM 5.1) at end of every session.
# Read at start of every session alongside AGENTS.md.

## Current State
**Date:** June 2026
**Current Week:** 3 ✅ COMPLETE
**Next Action:** Week 4 — Write ADR-006, then install Crossplane
**Active Blocker:** None
**DigitalOcean:** $205 credit active — Terraform NOT applied

## Week Checklist
- [x] Week 1: CI pipeline + sample-api + K8s manifests + ADRs 001/002/003/008/009
- [x] Week 2: ArgoCD GitOps + Kyverno + DOKS Terraform skeleton
- [x] Week 3: Backstage IDP + catalog + Golden Path + cleanup (Hephaestus PASS)
- [ ] Week 4: Crossplane resource provisioning
- [ ] Week 5-6: Observability (Prometheus + Grafana + Loki)
- [ ] Week 7-8: Vault secrets management
- [ ] Week 9-10: AI anomaly detection (Z-score + IsoForest + LSTM)
- [ ] Week 11-12: kopf operator (Autonomy Ladder implementation)
- [ ] Week 13-14: Flight Recorder (PostgreSQL + Redis + Grafana timeline)
- [ ] Week 15-16: Chaos Benchmark Pack (3 scenarios × 3 levels)
- [ ] Week 17-20: Production DOKS deployment
- [ ] Week 21-24: Thesis writing + defense prep

## Last Session Summary (Week 3 cleanup)
### What was done
- Removed .venv and .kilo from git tracking
- Added requirements.txt to sample-api with prometheus-fastapi-instrumentator
- Added /metrics endpoint to main.py
- Created AI skeleton structure (ai/anomaly-detector, ai/rag, ai/agent)
- Created infra/k3s/ install scripts
- Fixed Backstage isolated-vm via postinstall script
- Created platform/backstage/dev.sh startup script
- Created platform/backstage/catalog/org.yaml (Group + User entities)
- Fixed ArgoCD plugin URL to use ARGOCD_BASE_URL env var
- Created AGENTS.md for session persistence

### Review Result
Hephaestus (DeepSeek V4 Pro): **PASS** — all 8 checks passed
Minor nit: .kilo/ in .gitignore has leading whitespace (cosmetic only)

## Cluster Health (last verified)
```
k3s:       1 node (koussay), Ready, v1.34.6+k3s1
ArgoCD:    7/7 pods Running, sample-api Synced+Healthy
Kyverno:   4/4 pods Running, nexus-autonomy-level Ready
nexus-apps: sample-api 1/1 Running
Backstage: runs via ./dev.sh (Node 20, Yarn 4.4.1)
```

## ADRs Written
| ADR | Title | Status |
|-----|-------|--------|
| 001 | Kyverno over OPA | ✅ |
| 002 | CI Pipeline Design | ✅ |
| 003 | Autonomy Ladder | ✅ (cross-refs ADR-009) |
| 004 | GitOps Strategy | ✅ |
| 005 | IDP Design (Backstage) | ✅ |
| 006 | Crossplane Design | ⬜ NEXT |
| 007 | Observability Stack | ⬜ Week 5 |
| 008 | k3s over kind | ✅ |
| 009 | Incident Flight Recorder | ✅ (cross-refs ADR-003) |
| 010 | Vault Secrets Strategy | ⬜ Week 7 |
| 011 | AI Anomaly Detection | ⬜ Week 9 |
| 012 | kopf Operator Design | ⬜ Week 11 |
| 013 | Flight Recorder Impl | ⬜ Week 13 |
| 014 | Chaos Engineering | ⬜ Week 15 |

## Git Log (last 5 commits)
```
(update this after each session with: git log --oneline | head -5)
```

## Open Issues
| # | Issue | Priority | Week |
|---|-------|----------|------|
| 1 | HCP Terraform remote state configured (providers.tf) | ✅ Done | - |
| 2 | .kilo/ .gitignore leading whitespace | Low | next cleanup |
| 3 | Golden Path template not yet tested end-to-end | Medium | Week 4 |
| 4 | No second service created from template | Medium | Week 4 |

## Resource Budget
| Resource | Budget | Used | Remaining |
|----------|--------|------|-----------|
| DigitalOcean credit | $205 | $0 | $205 |
| Amazon Bedrock | $120 | TBD | TBD |
| Agent Router | $270 | TBD | TBD |
| Devin AI | $8.41 | ~$0 | ~$8 (save for final audit) |
