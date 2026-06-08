# NEXUS Platform — Master Briefing Document
**Version:** 4.0 | **Date:** June 2026 | **Author:** Platform Engineering Team  
**Repo:** `https://github.com/koussayx8/nexus-platform` | **Branch:** `main`

---

## 0. WHO YOU ARE AND WHAT THIS IS

You are an AI coding agent working on **NEXUS Platform** — a cloud-native Internal Developer Platform (IDP) with AI-powered self-healing capabilities. This is a final-year engineering thesis (PFE) at ESPRIT university, Tunisia, by Koussay Belhouchet.

You are **not starting from scratch**. Weeks 1–3 are complete and reviewed. Read `AGENTS.md` and `docs/NEXUS_STATUS.md` at the start of every session to restore context.

**Read this entire document before writing a single line of code.**

---

## 1. PROJECT IDENTITY

### What NEXUS Is
- GitOps-managed Kubernetes platform (k3s local + DOKS production)
- Internal Developer Portal (Backstage IDP)
- AI-powered anomaly detection (LSTM + Z-score + Isolation Forest)
- Self-healing operator (kopf) with 5-level Autonomy Ladder
- Incident Flight Recorder (immutable audit trail)
- Reproducible Chaos Benchmark Pack (scientific validation)

### The Three Thesis Differentiators

**1. Autonomy Ladder (ADR-003)**
```
Level 0: Observe only
Level 1: Diagnose only
Level 2: Recommend action
Level 3: Require human approval
Level 4: Auto-execute low-risk actions
```
Every Deployment carries `nexus.io/autonomy-level: "0"`. Kyverno validates it. The kopf operator reads it before acting.

**2. Incident Flight Recorder (ADR-009)**
```
anomaly_detected → metrics_queried → runbook_retrieved →
llm_diagnosis → action_selected → remediation_result → mttr_calculated
```
Every Autonomy Ladder level transition emits a `ladder_transition` FlightRecorderEvent with `from_level`, `to_level`, `reason`, `triggered_by`.

**3. Chaos Benchmark Pack**
Three fixed scenarios: `PodDelete`, `CPUHog`, `NetworkLatency`
Run at Autonomy Levels 0, 2, 4. Output: reproducible CSV/JSON report.

---

## 2. CURRENT STACK STATE (Week 3 complete)

### Infrastructure
```
Local (WSL2 — azure@KOUSSAY, ~/nexus-platform)
├── k3s v1.34.6+k3s1 — node "koussay", Ready
├── ArgoCD v2.14.11 — 7/7 pods, AppProject: nexus
├── Kyverno v1.18.0 — 4/4 pods, nexus-autonomy-level (Audit)
├── sample-api — 1/1 Running, nexus-apps, ArgoCD-managed
└── Backstage — start with: cd platform/backstage && ./dev.sh

Production (DigitalOcean — NOT YET APPLIED)
├── $205 credit active ($200 GitHub Student + $5 Trial)
├── HCP Terraform: nexus_pfe/nexus-doks (validated, not applied)
└── Apply only when explicitly instructed
```

### Repository Structure
```
nexus-platform/
├── AGENTS.md                          # AI agent session context (read at start)
├── .github/workflows/ci.yml           # 7-stage CI pipeline (all green)
├── apps/sample-api/
│   ├── k8s/base/                      # Hardened Deployment + Service + Namespace
│   ├── k8s/overlays/dev/              # k3s overlay
│   ├── catalog-info.yaml              # Backstage registration
│   ├── main.py                        # FastAPI + /metrics endpoint
│   ├── requirements.txt               # All Python deps including prometheus
│   ├── Dockerfile                     # Multi-stage, hardened, non-root
│   └── test_main.py                   # Pytest
├── ai/
│   ├── anomaly-detector/              # Skeleton: detector.py (Week 9-10)
│   ├── rag/                           # Skeleton: retriever.py (Week 11)
│   └── agent/                         # Skeleton: operator.py (Week 11-12)
├── platform/
│   ├── argocd/applications/           # sample-api Application CR
│   ├── argocd/projects/               # nexus AppProject
│   ├── backstage/                     # Full Backstage (Node 20, Yarn 4.4.1)
│   │   ├── dev.sh                     # Single-command startup script
│   │   ├── start-with-argocd.sh       # Startup with ArgoCD port-forward
│   │   ├── catalog/org.yaml           # Group + User entities
│   │   └── templates/fastapi-service/ # Golden Path template
│   ├── kyverno/policies/              # autonomy-level ClusterPolicy
│   └── vault/                         # Placeholder (Week 7-8)
├── infra/
│   ├── k3s/                           # Install scripts (install-k3s.sh etc.)
│   └── terraform/doks/                # DOKS cluster (validated, not applied)
└── docs/
    ├── NEXUS_STATUS.md                # Living status file (update each session)
    ├── NEXUS_OpenCode_Master_Brief_v4.md
    ├── ADR-001 through ADR-009
    ├── gitops-validation-log.md
    └── screenshots/                   # Real PNGs
```

### CI Pipeline (all green)
| Stage | Tool | Status |
|-------|------|--------|
| Lint | Ruff | ✅ |
| Test | Pytest (3 tests) | ✅ |
| SAST | Semgrep | ✅ |
| Secrets | GitLeaks | ✅ |
| Build | Docker multi-stage → GHCR | ✅ |
| Scan | Trivy | ✅ |
| Sign | Cosign keyless (Sigstore) | ✅ |

**Image:** `ghcr.io/koussayx8/nexus-platform/sample-api@sha256:c693838d...`

### Backstage Known Issues (fixed)
- `isolated-vm` stubbed via `postinstall` script in `package.json` ✅
- Startup now single command: `./dev.sh` ✅
- ArgoCD URL uses `${ARGOCD_BASE_URL}` env var ✅
- `nexus-platform-team` Group + User registered in catalog ✅

---

## 3. ARCHITECTURAL DECISIONS

Never contradict without writing a new ADR first.

| ADR | Decision | Rejected | Reason |
|-----|----------|---------|--------|
| 001 | Kyverno over OPA | OPA/Gatekeeper | YAML-native, simpler |
| 002 | Ruff+Semgrep+Cosign | flake8, key signing | Speed, accuracy, keyless |
| 003 | 5-level Autonomy Ladder | Binary AI | Enterprise trust, thesis differentiator |
| 004 | Single ArgoCD Application | App-of-apps | Defer until ≥3 services |
| 005 | Backstage over Port/Cortex | SaaS IDPs | No vendor lock-in |
| 008 | k3s over kind | kind, minikube | Persistent, WSL2 stable |
| 009 | Flight Recorder (immutable) | Simple logging | Auditable, defensible |

---

## 4. HARD RULES — NEVER VIOLATE

1. **Write ADR before implementing.** No exceptions.
2. **Never `:latest` image tags.** Always SHA digest.
3. **Never `terraform apply` without explicit user instruction.**
4. **Every Deployment must have `nexus.io/autonomy-level: "0"`.**
5. **Kyverno stays Audit until Week 11** (kopf operator exists).
6. **ADR-003 and ADR-009 are coupled.** Level transitions → FlightRecorderEvent.
7. **ArgoCD project = `nexus`**, never `default`.
8. **Never commit `terraform.tfstate`.** Remote state in HCP Terraform.
9. **Commit `.terraform.lock.hcl`.**
10. **Never `git add -A`.** Always add specific files.

---

## 5. QUALITY GATE PROCESS (Oh-My-OpenCode-Slim)

The quality gate uses a **multi-model review pipeline** via Oh-My-OpenCode-Slim. Every week must pass this gate before proceeding.

### The Five Agents

```
PROMETHEUS  (Qwen 3.7 Max)   = THE ARCHITECT
├── Writes ADRs before any implementation
├── Designs schemas and acceptance criteria
├── Reviews architectural decisions
└── NEVER touches files or runs commands

SISYPHUS    (Kimi K2.6)      = THE ENGINEER  
├── Executes the week plan
├── Writes all code, manifests, scripts
├── Runs kubectl, helm, terraform commands
└── Commits and pushes to git

HEPHAESTUS  (DeepSeek V4 Pro) = THE REVIEWER
├── Audits every commit (replaces Kilocode)
├── Finds security regressions
├── Validates ADR cross-references
└── Outputs PASS / NEEDS REVISION / FAIL

SISYPHUS-JR (MiniMax M3)     = THE SCAFFOLDER
├── Generates boilerplate YAML
├── Writes test stubs
├── Creates catalog-info.yaml files
└── Bulk repetitive generation

LIBRARIAN   (GLM 5.1)        = THE DOCUMENTER
├── Writes README sections
├── Updates ADRs with implementation notes
├── Writes NEXUS_STATUS.md updates
└── Generates changelogs
```

### Task → Agent Mapping

| Task Type | Agent | Model |
|-----------|-------|-------|
| Write ADR | Prometheus | Qwen 3.7 Max |
| Design schema / acceptance criteria | Prometheus | Qwen 3.7 Max |
| Install Helm charts | Sisyphus | Kimi K2.6 |
| Write K8s manifests | Sisyphus | Kimi K2.6 |
| Write Python code | Sisyphus | Kimi K2.6 |
| Run kubectl/terraform | Sisyphus | Kimi K2.6 |
| Git commit + push | Sisyphus | Kimi K2.6 |
| End-of-week review | Hephaestus | DeepSeek V4 Pro |
| Generate YAML boilerplate | Sisyphus-Junior | MiniMax M3 |
| Write catalog-info.yaml | Sisyphus-Junior | MiniMax M3 |
| Write README/docs | Librarian | GLM 5.1 |
| Update NEXUS_STATUS.md | Librarian | GLM 5.1 |

### Weekly Flow
```
YOU: "Start Week N"
        ↓
PROMETHEUS writes ADR + acceptance criteria
        ↓
SISYPHUS executes the plan (all implementation)
        ↓
SISYPHUS-JR generates any boilerplate
        ↓
HEPHAESTUS reviews all commits → PASS or fix list
        ↓  (if NEEDS REVISION → back to SISYPHUS)
LIBRARIAN updates NEXUS_STATUS.md + README
        ↓
YOU: confirm PASS → start Week N+1
```

### Hephaestus Review Prompt (use at end of every week)
```
You are Hephaestus, senior DevOps reviewer for NEXUS platform.
Review all commits from this week's work session.

Check each item:
1. All acceptance criteria met? (list them)
2. Code quality per file: CRITICAL / WARNING / INFO
3. Security: SHA-pinned images, readOnlyRootFilesystem,
   resource limits, namespace pod-security labels
4. ADR written before implementation? ADRs cross-referenced?
5. nexus.io/autonomy-level annotation on all Deployments?
6. Hard Rules violated? (list from section 4 of master brief)
7. NEXUS_STATUS.md updated?

Output format:
- PASS or NEEDS REVISION or FAIL
- If not PASS: numbered fix list with severity (CRITICAL/WARNING/INFO)
- Do not proceed to next week until verdict is PASS
```

### Token Budget (Go Plan — 1,150 req/5hr limit)
- Call Prometheus only for ADR writing (expensive, use sparingly)
- Call Hephaestus once per week at the end (not after every commit)
- Use Sisyphus-Junior for all YAML/boilerplate (cheaper than Kimi)
- Keep prompts under 400 words
- Never paste file contents — use file paths and let agent read

---

## 6. WEEK-BY-WEEK ROADMAP

### ✅ WEEK 1 — COMPLETE
CI pipeline + sample-api + K8s manifests + ADRs 001/002/003/008/009

### ✅ WEEK 2 — COMPLETE
ArgoCD GitOps + Kyverno + DOKS Terraform skeleton + screenshots

### ✅ WEEK 3 — COMPLETE
Backstage IDP + catalog + ArgoCD plugin + Golden Path + cleanup

---

### ⬜ WEEK 4 — CROSSPLANE

**Agent assignment:**
- Prometheus → ADR-006
- Sisyphus → install + configure + manifests
- Sisyphus-Junior → boilerplate XRD YAML
- Hephaestus → end-of-week review
- Librarian → README + NEXUS_STATUS.md

**Acceptance Criteria:**
| # | Criteria |
|---|----------|
| 1 | ADR-006 committed before Crossplane installation |
| 2 | Crossplane installed via Helm in k3s |
| 3 | DigitalOcean provider configured |
| 4 | `XPostgreSQLInstance` XRD + Composition |
| 5 | Claim: `apps/sample-api/infrastructure/database.yaml` |
| 6 | Golden Path template updated with Crossplane claim stub |
| 7 | `platform-contract.yaml` updated with `resource-class` field |
| 8 | README updated |
| 9 | DOKS Terraform NOT applied |

---

### ⬜ WEEKS 5–6 — OBSERVABILITY

**ADR to write first:** ADR-007 (OpenTelemetry + Prometheus + Grafana + Loki)

**Acceptance Criteria:**
| # | Criteria |
|---|----------|
| 1 | ADR-007 before installation |
| 2 | kube-prometheus-stack via Helm |
| 3 | Prometheus scraping sample-api /metrics |
| 4 | Grafana: sample-api RED dashboard |
| 5 | Loki collecting nexus-apps logs |
| 6 | Alert: error rate > 5% for 2min |
| 7 | All components ArgoCD-managed |

---

### ⬜ WEEKS 7–8 — VAULT

**ADR to write first:** ADR-010 (Vault agent injector vs CSI vs ESO)

**Acceptance Criteria:**
| # | Criteria |
|---|----------|
| 1 | ADR-010 before installation |
| 2 | Vault deployed (dev mode acceptable locally) |
| 3 | Kubernetes auth configured |
| 4 | sample-api reads secret from Vault at runtime |
| 5 | Kyverno warns on plain-text env var secrets |
| 6 | Golden Path template includes Vault path |

---

### ⬜ WEEKS 9–10 — AI ANOMALY DETECTION

**ADR to write first:** ADR-011 (algorithm selection + consensus mechanism)

**Acceptance Criteria:**
| # | Criteria |
|---|----------|
| 1 | ADR-011 before code |
| 2 | Z-score on error rate |
| 3 | Isolation Forest on multivariate metrics |
| 4 | LSTM on latency time-series |
| 5 | Consensus: all 3 agree → incident triggered |
| 6 | Incident published to Redis channel |
| 7 | False positive rate < 5% on normal traffic |
| 8 | Grafana panel: real-time anomaly scores |

---

### ⬜ WEEKS 11–12 — KOPF OPERATOR (THESIS CORE)

**ADR to write first:** ADR-012 (kopf design + LLM + RAG + action taxonomy)

**Acceptance Criteria:**
| # | Criteria |
|---|----------|
| 1 | ADR-012 before code |
| 2 | kopf operator in nexus-ops namespace |
| 3 | Subscribes to Redis incident channel |
| 4 | Reads `nexus.io/autonomy-level` before acting |
| 5 | Level 0: log + FlightRecorderEvent only |
| 6 | Level 2: recommendation to webhook |
| 7 | Level 4: pod restart / replica scaling |
| 8 | RAG retrieves runbook (top-1 > 70% accuracy) |
| 9 | Kyverno switched to Enforce mode |
| 10 | End-to-end demo: inject → detect → heal |

---

### ⬜ WEEKS 13–14 — FLIGHT RECORDER IMPLEMENTATION

**ADR to write first:** ADR-013 (PostgreSQL schema + Redis + Grafana timeline)

**FlightRecorderEvent schema:**
```python
@dataclass
class FlightRecorderEvent:
    incident_id: str
    event_type: str  # anomaly_detected|metrics_queried|runbook_retrieved|
                     # llm_diagnosis|action_selected|remediation_result|
                     # ladder_transition|mttr_calculated
    timestamp: datetime
    payload: dict
    autonomy_level: int
    triggered_by: str  # "operator"|"human"|"policy"
```

---

### ⬜ WEEKS 15–16 — CHAOS BENCHMARK PACK

**ADR to write first:** ADR-014 (Litmus Chaos + scenario selection + metrics)

**Output:** CSV per run: `scenario, autonomy_level, detection_time_s, mttr_s, correct_diagnosis, false_positives`

---

### ⬜ WEEKS 17–20 — PRODUCTION (DOKS)
`terraform apply` → everything on DigitalOcean → full demo recorded

### ⬜ WEEKS 21–24 — THESIS WRITING + DEFENSE

---

## 7. ENVIRONMENT SETUP

### Backstage (single command)
```bash
nvm use 20
cd ~/nexus-platform/platform/backstage
./dev.sh                          # starts backend + frontend
./start-with-argocd.sh            # starts with ArgoCD port-forward
```

### Cluster verification
```bash
kubectl get pods -A | grep -v Running | grep -v Completed
# Must return empty
```

### ArgoCD UI
```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
# https://localhost:8080
# Password: kubectl -n argocd get secret argocd-initial-admin-secret \
#   -o jsonpath="{.data.password}" | base64 -d
```

### Terraform (never apply without instruction)
```bash
cd ~/nexus-platform/infra/terraform/doks
terraform plan -out=plan.tfplan
```

---

## 8. COMMIT CONVENTION

```
type(scope): description

Types: feat, fix, chore, docs, refactor, test, ci
Scopes: week1-16, backstage, argocd, kyverno, terraform,
        crossplane, observability, vault, ai, operator,
        flight-recorder, chaos, docs, ci
```

---

## 9. WHAT NOT TO ADD (CUT LIST)

❌ Istio | ❌ Argo Rollouts | ❌ Prophet forecasting | ❌ Harbor
❌ Kafka/Redpanda | ❌ Multi-cloud Crossplane | ❌ Multiple language templates
❌ Argo Workflows | ❌ Tekton

---

## 10. SESSION RITUALS

### Start of every session (3 lines max):
```
Read AGENTS.md and docs/NEXUS_STATUS.md.
Confirm: what week are we on and what is the next task?
Do not start work until confirmed.
```

### End of every session (tell Librarian):
```
Update docs/NEXUS_STATUS.md with what was completed.
Commit: git commit -m "chore(docs): update NEXUS_STATUS.md"
Push.
```

---

*Single source of truth for NEXUS. When in doubt, read this first.*
