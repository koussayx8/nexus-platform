> **PFE re-plan, rev 1 (task 0, 2026-11-02), from the owner's binding decisions of 2026-11-02. One revision round
> at most; frozen at approval. Current state lives in `TASKS.md`.** Line references are at `dev` `d20b04b`.

# PFE re-plan: M2, M3, M4 and the A1 demo

## Sources

| Tag | Source |
|---|---|
| Owner-11-02 | The owner's decisions of 2026-11-02 (below), binding |
| Spec | `docs/architecture/final-spec.md` v1.0, frozen |
| M2 plan | [`m2-plan.md`](m2-plan.md) rev 3 (GATE M2-0); its scope trace stands, its phases and host are replaced here |
| ADR-030 | ADR-030 and its 2026-11-02 amendment |

**Evidence, this session:**
- `git log --oneline -25` on `dev` `d20b04b`: no commit after the M2-1/M2-1b plan gate (#133) except a Dependabot
  bump (#134). No M2-1 or M2-1b work exists.
- `git ls-remote --heads origin`: `dev`, `experiment/dev-state`, `main` only. `replication/x86` and
  `replication/x86-dev-state` were never created.
- `ls`: no `reasoner/`, `runbooks/`, `nexusctl/`, `baseline/` or `demo/` directory yet.

## Owner decisions (2026-11-02)

| # | Decision |
|---|---|
| O1 | **Full spec scope stays:** L2, K5, `approvalTTL`, `nexusctl`, NetworkPolicies N1–N6, `verifyImages`, CODEOWNERS, BM25, the Haiku arm, the effort sweep, the FR dashboard, spec v1.1, Hygiene |
| O2 | **Every measurement runs on WSL2.** The M1b WSL2 calibration stands (C ≥ 200, B = 80, ADR-026/027/028) |
| O3 | **Cut:** the x86 replication (M2-1b, recorded as not done under its stop rule), M2-1 (step 0), the new-host bootstrap fixes (M2-3) |
| O4 | **A1 is the defense demo host only:** built in January 2027 with a demo overlay, labelled demo. No measurement on A1 |
| O5 | **"Quiet when healthy" is a stated limitation.** M4's no-fault runs count false alarms |
| O6 | **Process:** one plan per milestone; at most one revision round; pre-registration only for M4's measured runs |
| O7 | **Tests:** automated tests for the safety invariants (RBAC boundaries, K1–K6, K5 binding, write-ahead plus `Audit-Id`, Kill Switch, verification, I1/I2); normal unit tests elsewhere; no custom harnesses or calibration campaigns |

## Milestones and dates

| Milestone | Date | Done when | Plan |
|---|---|---|---|
| Re-plan | 2026-11-02 | Task 0 merged | this file |
| **M2** — the operator acts | **live by 2026-11-20** | M2 exit criterion on WSL2 (`m2-outline.md`) | [`m2-outline.md`](m2-outline.md) |
| **M3** — L2, policies, network | **by 2026-12-06** | M3 exit criterion on WSL2 | [`m3-outline.md`](m3-outline.md) |
| Code complete | **2026-12-20** | Every M4 component merged; spec v1.1 merged; M4 pre-registration merged | [`m4-outline.md`](m4-outline.md) |
| **M4** — evaluation | 2026-12-21 → **~2027-01-10** | Phases A–D run on WSL2 as pre-registered; results exported | [`m4-outline.md`](m4-outline.md) |
| A1 demo | **early January 2027** | Owner builds A1 with the demo overlay; demo runs, labelled demo | [`m4-outline.md`](m4-outline.md), "A1 demo" |

The defense date is not recorded in the repository: UNKNOWN.

## Tasks

One branch `claude/task-<n>-<short-name>` and one PR to `dev` per task. Expected paths follow spec §25.

| Task | Content | Milestone | Depends on | Expected paths |
|---|---|---|---|---|
| **T0** | This re-plan | — | — | `docs/plans/`, `TASKS.md`, `docs/adr/ADR-030-cloud-hosting.md` |
| **T1** | `flight-recorder-db` manifests, the §14 schema, the Secrets script; the Writer with the write-ahead intent row; the `Audit-Id` client wrapper; the Grafana FR datasource and dashboard JSON | M2 | — | `platform/` (FR DB, monitoring), `libs/nexus-client/` or `operator/`, `scripts/` (Secrets script) |
| **T2** | Operator act path, L0/L1/L3 (L2 stays `Escalated`): the catalogue, the Executor, Safety Gate stages 1–7, the breaker and cool-down; the Verifier against the raw baseline, and escalate; the R1 rules engine; dev-only RBAC. Codes against T1's Writer interface. No Dockerfile change | M2 | T1's Writer interface | `operator/`, `platform/rbac/` |
| **T3** | Kyverno K1–K6 with `kyverno` CLI tests (K5 approver stamping and hash binding); `verifyImages` in Audit mode; NetworkPolicies N1–N6; CODEOWNERS; `nexusctl`; the approval schema and `approvalTTL`. The operator's L2 states come after T2 merges | M3 | T2 merged (L2 follow-up only) | `platform/policies/`, `platform/network/`, `.github/CODEOWNERS`, `nexusctl/` |
| **T4** | M4 harness: the runner (inject, reset, ArgoCD refresh); §21 metrics and statistics on fixtures; the reasoner client (`proposal/v1`, redaction, timeout); BM25 over the runbooks; arm configs incl. the Haiku arm and the effort sweep | M4 | — | `experiments/`, `reasoner/` (retriever, client) |
| **T5** | Hygiene, supply chain, multi-arch: SHA-pinned actions and `cosign-installer`; Trivy on the pushed digest and the digest-bump step; Dependabot to `dev`; shellcheck in CI; ruff `src`; the `requirements-dev` leftovers; the Starlette test client; the `repo-checks` range; the sample-api base pinned by digest, a slimmer base and the Trivy comparison; the operator Dockerfile's passwd entry for UID 10001; amd64 + arm64 builds and signing for sample-api and nexus-operator | M3 (needed before A1) | — | `.github/`, `apps/sample-api/`, `operator/Dockerfile` |
| **T6** | G4 (the `ignoreDifferences` boundary check), G5 (the minimal `audit_id` join script), G6 (the KSM alerts, `group_by`, F5) | G4, G5: M2 exit; G6: by 12-20 | — | `.github/`, `scripts/`, `platform/monitoring/` |

## In scope outside T1–T5 (gaps), with owners

In scope under O1 or the spec, but in no task T1–T5. The owner assigned each one on 2026-11-02 (last column).
T6 is a new task for G4–G6. "Local" means done in the owner's own Claude Code session on WSL (it needs the live cluster, or comes after the
cloud credit), still delivered as a PR to `dev` through its gate.

| # | Item | Source | Needed by | Proposal | Owner (2026-11-02) |
|---|---|---|---|---|---|
| G1 | Evidence Collector (`evidence/v1`) and where redaction runs before a bundle is stored (NF-24). T4 lists redaction in the reasoner client; R1 in T2 needs the bundle | §4, §14, §16 | M2 | Collector in T2; Redactor stage in T2 or T4 (Q1) | T2, second PR |
| G2 | Compensator (scale back, itself a gated request); `Verifying` leads only to `Resolved` or `Compensating` (I11) | §9, I11 | M2 | T2 (Q2) | T2, second PR |
| G3 | Operator metrics and ServiceMonitor (U3); config keys `advisoryChecks`, `breakerEpoch`, `reasonerEndpoint` | ADR-025 | M2 | T2 follow-up | T2, second PR |
| G4 | CI boundary check: every field reachable through the catalogue and RBAC is delegated by `ignoreDifferences` with `RespectIgnoreDifferences=true`, or never declared (I12) | §9, §13 | M2 | new task | T6 |
| G5 | Minimal `audit_id` join script for the M2 exit | M2 plan | M2 exit | T1 follow-up | T6 |
| G6 | KSM alerts `NexusRolloutStuck`, `NexusCrashLooping` and Alertmanager `group_by: [nexus_target]`; the F5 SIGKILL decision | §3, ADR-022 | M4 (S3, C4 map) | new task, by 12-20 | T6 |
| G7 | `verify-state.sh` checks per milestone (FR DB Ready, staged RBAC, rollout strategy, levels; K1–K6 Enforce, N1–N6; Reasoner Ready) | §25 | each exit | with each milestone's live step | local, at each milestone exit |
| G8 | `nexus-prod` Scale/Eviction grants with K1–K6 in Enforce; the operator's L2 states | M2 plan, §11 | M3 | T3 follow-up (stated in T3) | T3 follow-up |
| G9 | The Reasoner service: Deployment in `nexus-reasoner` with no token, Model Adapter, `/healthz` with the index hash, image (multi-arch); eight runbooks (S5 held out), runbook lint, `make index`, the 30 labelled queries and retrieval evaluation | §15, §16, I1 | 12-20 | new task | T4 |
| G10 | Static baseline C4: `nexus-baseline` identity and receiver | §20 | 12-20 | new task | T4 |
| G11 | Containment suite: coverage matrix, fail-closed, Kill Switch, compromised operator (Phase D) | §20 | 12-20 | new task | T3 follow-up |
| G12 | ~~K8sGPT capture (C6)~~ — moved to the improvements backlog (owner, 2026-11-04; ADR-036) | §20 | — | — | — |
| G13 | `reconcile-audit`, `export-results`, `make results` | §14, §25 | 12-20 | T4 follow-up | T4 |
| G14 | Detector comparison: Z-score against Isolation Forest, offline | §21 | evaluation | new task | local, December |
| G15 | R0 seeded adversarial generator; `kyverno` CLI replay with mocked context for Phase B | §16, §20, §27 | 12-20 | T4 (arm configs) or follow-up | T4 |
| G16 | `experiments/ground-truth.yaml` and the M4 pre-registration PR | §20 | 12-20 | docs task after spec v1.1 | local, December, after G17 |
| G17 | Spec v1.1: one docs PR (ADR plus version bump), contents below | Spec §0 | before G16 | docs task | local, December, before G16 |
| G18 | A1 demo overlay and `demo/reset.sh` | O4, §25 | early January | new task | local, January |

## Where the GATE M2-0 phases go

| M2 plan phase | New home |
|---|---|
| M2-0 plan | Done (#132) |
| M2-1 step 0 | **Cut** (O3) |
| M2-1b x86 replication | **Cut; recorded as not done** under its stop rule (not calibrated by 2026-11-01). The prerequisite scripts (offset-safe timestamp parsing, `ramp.py` per-step counters) and the Locust autostart decision move to T4 (runner) and the M4 pre-registration |
| M2-2 arm64 and images | T5 |
| M2-3 `bootstrap.sh` before the first cloud bootstrap | **Cut** (O3): operator-config ordering, the readyz wait, the `observability` timeout, secretbox, audit-log `maxbackup`. `maxbackup` is re-raised only if M4's retention check needs it (M4 outline) |
| M2-4 ADR-030 addendum, Spec v1.1 | ADR-030 amendment in T0; Spec v1.1 → G17 |
| M2-5 `Audit-Id` spike | T1 (client wrapper, offline); the live dry-run is an M2 live step |
| M2-6 `flight-recorder-db` | T1 |
| M2-7 operator M2 stages | T2, plus G1–G4 |
| M2-8 KSM alerts, F5, verify checks | G6, G7 |
| M2-9 A1 host and calibration | **Cut** (O2, O4). A1 is built in January for the demo only (G18). The Grafana sidecar resources, the connection-cost measurement and the new-host re-checks are not run (O7: no calibration campaigns); they stay in `TASKS.md` Later |
| M2-10 exit | M2 exit on WSL2 by 2026-11-20; the defense recording: Q4 |

The item-by-item placement of the M2 section's "Placed from Later" items is in `TASKS.md` (each item's new tag).

## Contradictions with spec v1.0 (for spec v1.1, G17)

Reported, not resolved here. The owner's decisions govern until spec v1.1.

| # | Spec v1.0 | Owner-11-02 or repository |
|---|---|---|
| X1 | Arms R0–R3; R2 and R3 on Groq (§16); no Haiku arm | O1 adds a Haiku arm. Provider, model id and phase are fixed in the M4 pre-registration |
| X2 | "Low reasoning effort", frozen (§16, §25) | O1 adds an effort sweep. Levels and phase fixed in the M4 pre-registration |
| X3 | Phases A–D (§20) have no no-fault runs | O5: no-fault runs count false alarms. Count and duration fixed in the M4 pre-registration |
| X4 | Demo fallback: Local Model via Ollama, offline (§16) | O4: an A1 demo host, labelled demo (ADR-030 contradiction 2) |
| X5 | Host: WSL2 (§3) | O2 agrees for every measurement; A1 is a demo-only deviation (ADR-030 amendment) |
| X6 | FR datasource and NEXUS Operations dashboard (§3) | No contradiction; moved from M4 to T1 |
| X7 | I16: an approval binds a proposal hash, "enforced by K3, K5, C2"; the §11 K3 row lists `actionId`, replicas and `expiresAt` but not the hash; K5's row has stamping and TTL only | The owner's T3 says "K5 approver stamping and hash binding". The spec is internally inconsistent about which policy checks `proposalHash`. T3 settles it in its PR; spec v1.1 records it |
| X8 | Carried from GATE M2-0: Z-score 3-min lag and in-flight gauge; Application name `observability`; `experiment/dev-state` sequencing; branch ruleset; unfiltered required check; ADR-030 contradiction 3 (Bedrock) moot | Unchanged; listed in `TASKS.md` |
| X9 | "Quiet when healthy" is not a stated limitation in v1.0 | O5 makes it one |
| X10 | C6 (K8sGPT, §20) is a required configuration; K8sGPT CLI is MUST (§22) | Owner 2026-11-04: C6 deferred, no experimental K8sGPT comparison; a theoretical study instead (ADR-036). Spec v1.1 records it |

## Test policy (O7)

Automated tests are required for each safety invariant below; everything else gets normal unit tests. No custom
harness and no calibration campaign.

| Invariant | Test | Task |
|---|---|---|
| RBAC boundaries (I4) | Manifest test: the operator's Scale/Eviction grants exist only in `nexus-dev` (M2), then `nexus-dev` and `nexus-prod` (M3); envtest `SubjectAccessReview` | T2, G8 |
| K1–K6 (I5–I8) | `kyverno test`, both directions per §11 "Policy tests" | T3 |
| K5 binding (I16) | Approval by the operator or a non-approver denied; identity and time stamped; expiry > 15 min denied; altered or replayed hash denied; C2 write-once | T3 |
| Write-ahead plus `Audit-Id` (I14, I10) | FR DB down ⇒ `Blocked` / `recorder_unavailable` and no request sent; the intent row precedes the request; the `Audit-Id` of the response is stored with the row | T1, T2 |
| Kill Switch (I6) | `halted` and absent ⇒ no request, compensation included (operator stage 5); K4 both directions | T2, T3 |
| Verification (I11, I15) | Failed verification never reaches `Resolved`; a forged `expectedEffect` is ignored; verification against the raw pre-fault baseline, not the alert state | T2 |
| I1/I2 | Reasoner manifest: `automountServiceAccountToken: false`, no RoleBinding for its ServiceAccount; NetworkPolicy tests for N1–N3, N5, N6; live `auth can-i --list` empty (live check pending) | T3, G9 |

## Process (O6)

- One plan per milestone: the three outlines here. Each gets at most one revision round, then it is frozen.
- Gates: each task PR, then each milestone exit. Merges, tags and forward-merges need the owner's typed approval
  naming the full SHA (CLAUDE.md rule 11). No agent merges.
- Pre-registration only for M4's measured runs (G16). The M2 and M3 exits are demonstrations, not measurements.
- Live steps: no cluster in the agent sessions. Every live check is marked "live check pending" and run by, or
  with the approval of, the owner on WSL2.
- ADR-029's per-gate session rule (§4) predates O6. Q5 (owner, 2026-11-02): one sentence in ADR-029, added in this PR.

## Schedule risks

- **The scope outside T1–T5 (G1–G18)** is larger than T1–T5 itself. G9 (the Reasoner service and runbooks), G10, G11 and G16 are on
  the critical path to 2026-12-20.
- **Evaluation volume on one laptop host:** Phase A 120 runs, C 72, D 24 plus the suite (§20), plus the no-fault
  runs, before the Haiku arm and the effort sweep. At up to 20 min of pre-check per run (§25), A + C + D need about
  216 runs × 30–45 min ≈ 108–162 h of WSL2 time inside about 3 weeks that include holidays. The Haiku arm and the
  effort sweep run in Phase B only (Q3). Run unattended (below), the 204 automatable runs plus the no-fault runs
  take about 5–10 days at 24 h a day, the attempt cap included; C5 and the suite stay attended.
- **WSL2 host faults** (VM pauses, clock steps; ADR-026, ADR-028): the timed-run rule (pause gap and `boot_id` at
  start and end) applies to every M4 run; discard rules are pre-registered.
- **A1 in January** runs on a stack whose new-host bootstrap fixes were cut (O3). A fresh bootstrap may need the
  operator-config workaround noted in `TASKS.md` (M2-3 item); it is handled at the A1 build, not before.

## Unattended runs (owner, 2026-11-02)

- **T4's runner** keeps its queue on disk, resumes after a WSL2 reboot, voids and requeues a run on a `boot_id` or
  pause-gap change, and respects the 15-attempt cap per cell (§25).
- **G16** (the M4 pre-registration) grants the owner's approval for the whole batch of runner mutations (S1–S6
  injection, the §20 reset, the `advisoryChecks` toggle), as CLAUDE.md rule 3 requires.
- **Owner setup before 2026-12-21:** WSL started at logon; sleep off overnight.

## Owner decisions on Q1–Q6 (2026-11-02)

Each accepted as proposed.

| # | Question | Decision |
|---|---|---|
| Q1 | Where does the Evidence Redactor run before a bundle is stored: in the operator (T2) or with the reasoner client (T4)? | Operator stage in T2 (spec §4); T4 reuses it |
| Q2 | Is the Compensator part of T2? | Yes |
| Q3 | The Haiku arm and the effort sweep: live, offline replay, or both? | Phase B only |
| Q4 | The defense recording: of the A1 demo (labelled demo) or of a WSL2 measured run? | Both, each labelled with its host |
| Q5 | Does ADR-029 need an addendum for O6? | One sentence at the next ADR PR |
| Q6 | Which namespace runs L2 for the M3 exit? | `nexus-dev` set to `"2"` for the demo by commit on `experiment/dev-state`, then back to `"3"` |

## Improvements backlog

Not scheduled; none is a gate for M4.

| # | Item | When |
|---|---|---|
| I-1 | (was G12) K8sGPT or similar as an extra, redacted evidence source for the Evidence Collector. The experimental C6 comparison is deferred (ADR-036) | After M4 or after the PFE |
