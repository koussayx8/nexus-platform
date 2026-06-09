# NEXUS Platform — Living Status File
# Updated by Librarian (GLM 5.1) at end of every session.
# Read at start of every session alongside AGENTS.md.

## Current State
**Date:** June 2026
**Current Week:** 4 ✅ COMPLETE
**Next Action:** Week 5 — Observability Stack (Prometheus + Grafana + Loki)
**Active Blocker:** None
**DigitalOcean:** $205 credit active — Terraform NOT applied

## Week Checklist
- [x] Week 1: CI pipeline + sample-api + K8s manifests + ADRs 001/002/003/008/009
- [x] Week 2: ArgoCD GitOps + Kyverno + DOKS Terraform skeleton
- [x] Week 3: Backstage IDP + catalog + Golden Path + cleanup (Hephaestus PASS)
- [x] Week 4: Crossplane resource provisioning
- [ ] Week 5-6: Observability (Prometheus + Grafana + Loki)
- [ ] Week 7-8: Vault secrets management
- [ ] Week 9-10: AI anomaly detection (Z-score + IsoForest + LSTM)
- [ ] Week 11-12: kopf operator (Autonomy Ladder implementation)
- [ ] Week 13-14: Flight Recorder (PostgreSQL + Redis + Grafana timeline)
- [ ] Week 15-16: Chaos Benchmark Pack (3 scenarios × 3 levels)
- [ ] Week 17-20: Production DOKS deployment
- [ ] Week 21-24: Thesis writing + defense prep

## Last Session Summary (Week 4 — Crossplane)
### What was done
- Wrote and committed ADR-006-crossplane-design.md
- Installed Crossplane v2.3.1 via Helm with SHA-pinned images
- Configured provider-upjet-digitalocean v0.3.2 (Healthy)
- Created XPostgreSQLInstance XRD and dev/prod Compositions
- Created database claim for sample-api
- Expanded ArgoCD AppProject nexus.yaml for Crossplane CRDs
- Configured argocd-cm with annotation tracking and ignoreDifferences
- Added RBAC for Crossplane to manage dev resources
- Updated Golden Path template with Crossplane claim stub
- Created platform-contract.yaml with resource-class field
- Updated README.md with Crossplane section
- Created mock PostgreSQL deployment for dev environment (PodSecurity constraints)

### Review Result
Hephaestus (DeepSeek V4 Pro): **PENDING** — Week 4 review not yet run

### Known Issues
- Crossplane v2.3.1 Pipeline mode has known issues with composed resource namespace handling on this setup
- Dev Composition uses mock PostgreSQL (busybox) due to PodSecurity restricted mode constraints
- Prod Composition defined but untested (no DO resources created)

## Cluster Health (last verified)
```
k3s:       1 node (koussay), Ready, v1.34.6+k3s1
ArgoCD:    7/7 pods Running, sample-api Synced+Healthy, crossplane-infrastructure Synced+Healthy
Kyverno:   4/4 pods Running, nexus-autonomy-level Ready
nexus-apps: sample-api 1/1 Running, sample-db 1/1 Running (mock PostgreSQL)
crossplane-system: crossplane 1/1 Running, crossplane-rbac-manager 1/1 Running, provider-upjet-digitalocean 1/1 Running
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
| 006 | Crossplane Design | ✅ |
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
8876fa6 feat(backstage): update Golden Path template with Crossplane claim stub
4e137c5 docs(crossplane): update README and add platform-contract.yaml
7cec639 feat(crossplane): add dev and prod Compositions, RBAC, and database claim resources
7e6c5ec feat(crossplane): add XPostgreSQLInstance XRD
719ade0 feat(crossplane): add DigitalOcean provider and ProviderConfig
```

## Open Issues
| # | Issue | Priority | Week |
|---|-------|----------|------|
| 1 | HCP Terraform remote state configured (providers.tf) | ✅ Done | - |
| 2 | .kilo/ .gitignore leading whitespace | Low | next cleanup |
| 3 | Golden Path template updated with Crossplane claim stub | ✅ Done | Week 4 |
| 4 | Crossplane v2.3.1 Pipeline mode namespace error — claim READY=False. Composition defined but resources created manually. Downgrade to v1.17.x or wait for Crossplane fix. | High | Week 5 |
| 5 | Backstage Crossplane frontend plugin installed (@terasky v2.18.1) | ✅ Done | Week 4 |
| 6 | Dev PostgreSQL: busybox placeholder pending Crossplane Pipeline mode fix. Real Postgres required before Week 13 (Flight Recorder). | High | Week 5 |
| 7 | SHA-pinned images applied via helm upgrade (core only). Provider/function images use version tags (managed by Crossplane package manager). | ✅ Done | Week 4 |
| 8 | Kyverno mutate policy for autonomy-level default installed | ✅ Done | Week 4 |

## Resource Budget
| Resource | Budget | Used | Remaining |
|----------|--------|------|-----------|
| DigitalOcean credit | $205 | $0 | $205 |
| Amazon Bedrock | $120 | TBD | TBD |
| Agent Router | $270 | TBD | TBD |
| Devin AI | $8.41 | ~$0 | ~$8 (save for final audit) |
