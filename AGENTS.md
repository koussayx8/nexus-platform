# NEXUS Platform — Agent Context File
# Automatically read by OpenCode at session start.
# Do not delete. Update via Librarian (GLM 5.1) only.
# Last updated: Week 3 complete + cleanup PASS

## Identity
- Project: NEXUS Platform (PFE thesis — ESPRIT, Tunisia)
- Owner: Koussay Belhouchet (koussayx8@github)
- Repo: https://github.com/koussayx8/nexus-platform
- WSL2 path: ~/nexus-platform
- Full brief: docs/NEXUS_OpenCode_Master_Brief_v4.md

## Current Status
- Week 1: ✅ COMPLETE (CI + sample-api + K8s + ADRs)
- Week 2: ✅ COMPLETE (ArgoCD + Kyverno + Terraform)
- Week 3: ✅ COMPLETE (Backstage + catalog + cleanup PASS)
- Week 4: ⬜ NOT STARTED → next task

## Next Task
Week 4 — Crossplane Resource Provisioning
FIRST ACTION: Write docs/ADR-006-crossplane-design.md
DO NOT install Crossplane before ADR-006 is committed.

## Stack Running Locally
- k3s: running, node koussay, v1.34.6+k3s1
- ArgoCD: 7/7 pods, AppProject: nexus
- Kyverno: 4/4 pods, nexus-autonomy-level (Audit mode)
- sample-api: 1/1 Running, nexus-apps, ArgoCD-managed
- Backstage: cd platform/backstage && ./dev.sh
- Terraform: validated, NOT applied (DO NOT apply without instruction)
- DigitalOcean: $205 credit active

## Hard Rules (memorize these)
1. Write ADR before implementing — no exceptions
2. Never :latest image tags — always SHA digest
3. Never terraform apply without explicit user instruction
4. Every Deployment needs nexus.io/autonomy-level: "0"
5. Kyverno stays Audit until Week 11
6. ADR-003 + ADR-009 coupled — level transitions → FlightRecorderEvent
7. ArgoCD project = nexus, never default
8. Never commit terraform.tfstate
9. Never git add -A — always specific files

## Agent Roles (Oh-My-OpenCode-Slim)
- Prometheus (Qwen 3.7 Max): ADRs + planning ONLY, never touches files
- Sisyphus (Kimi K2.6): all implementation, kubectl, git commits
- Hephaestus (DeepSeek V4 Pro): end-of-week review only
- Sisyphus-Junior (MiniMax M3): boilerplate YAML, test stubs
- Librarian (GLM 5.1): README, NEXUS_STATUS.md, docs

## Session Rituals
START: Read this file + docs/NEXUS_STATUS.md → confirm week + next task
END: Librarian updates NEXUS_STATUS.md → commit → push

## What NOT to Add
Istio, Argo Rollouts, Kafka, Harbor, Prophet, multi-cloud, Tekton
See CUT_LIST.md for full list.

## Key Paths
- K8s: apps/sample-api/k8s/base/deployment.yaml
- ArgoCD: platform/argocd/applications/sample-api.yaml
- Kyverno: platform/kyverno/policies/autonomy-level.yaml
- Backstage: platform/backstage/dev.sh
- Terraform: infra/terraform/doks/
- ADRs: docs/ADR-00*.md
- Status: docs/NEXUS_STATUS.md
