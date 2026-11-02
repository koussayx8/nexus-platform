> **M4 plan outline, rev 1 (task 0, 2026-11-02). One revision round at most; frozen at approval. Current state lives
> in `TASKS.md`. The only pre-registration in the project is M4's, for its measured runs (re-plan O6).**

# M4 outline: the evaluation, code complete 2026-12-20, runs until ~2027-01-10, the A1 demo in early January

## Goal

Run the spec §20 evaluation on WSL2 and produce the §21 metrics: containment violations zero for every reasoner,
effectiveness compared between reasoners. Code complete by **2026-12-20**; measured runs **2026-12-21 → ~2027-01-10**;
the A1 demo in **early January 2027**.

## Scope

| # | Item | Source | Task |
|---|---|---|---|
| 1 | Experiment Runner: inject S1–S6, reset (§20 order), hard ArgoCD refresh after each runner commit; injection time = when ArgoCD applied the revision; pre-checks (§20) with `baseline_stddev15m` and the 20-min budget; timestamps parsed with offsets | §20, Later (M4), M2-1b leftovers | T4 |
| 2 | §21 metrics and statistics (Wilson 95 %, rule of three, median and IQR per MTTR phase, per scenario) on fixtures | §21 | T4 |
| 3 | Reasoner client: `proposal/v1` validation, redaction, 30 s timeout, one attempt, R1 fallback | §16 | T4 |
| 4 | BM25 retriever over the runbooks (k1 1.5, b 0.75, top 3, score floor frozen after calibration) | §15 | T4 |
| 5 | Arm configs: R0–R3, the Haiku arm, the effort sweep | §16, O1 | T4 |
| 6 | Reasoner service, eight runbooks (S5 held out), runbook lint, `make index`, 30 labelled queries, retrieval evaluation | §15, §16 | G9 |
| 7 | Static baseline C4 (`nexus-baseline`) | §20 | G10 |
| 8 | Containment suite: coverage matrix, fail-closed, Kill Switch, compromised operator | §20 | G11 |
| 9 | K8sGPT capture (C6) | §20 | G12 |
| 10 | `reconcile-audit` (full, `audit_links`), `export-results`, `make results` | §14, §25 | G13 |
| 11 | Detector comparison, Z-score against Isolation Forest, offline | §21 | G14 |
| 12 | R0 generator; `kyverno` CLI replay with mocked context (Phase B) | §16, §20 | G15 |
| 13 | KSM alerts `NexusRolloutStuck`, `NexusCrashLooping`, `group_by`; F5 | §3, ADR-022 | G6 |
| 14 | `experiments/ground-truth.yaml`, committed and hashed before any model run; the pre-registration | §20 | G16 |
| 15 | Spec v1.1, merged before the pre-registration | Spec §0 | G17 |
| 16 | A1 demo overlay, `demo/reset.sh` | O4 | G18 |

## The pre-registration (G16), before any measured run

One PR, merged with the owner's typed approval before the first measured run. It fixes:
- the system-under-test commit and the harness commit;
- `ground-truth.yaml` and its hash;
- the arms: R0–R3 per §16; **the Haiku arm** (provider, model id, settings; re-plan X1); **the effort sweep**
  (levels and which arms; X2); the phase of each (proposal: Haiku and the sweep in Phase B only, re-plan Q3);
- **no-fault runs** (O5, X3): count, duration, and the false-alarm definition (an Incident created with no injection);
- volumes per phase (§20: A 120, B ~240 calls plus the additions, C 72, D 24 plus the suite), 10 valid runs per cell,
  at most 15 attempts (§25);
- pass, void and discard rules, including the timed-run rule (pause gap and `boot_id` at start and end) and the
  Locust baseline and autostart setting;
- the audit-log retention check: today's `maxbackup` covers about 2.19 idle days (ADR-019 addendum);
  the pre-registration states whether reconciliation runs per run (then retention suffices) or `maxbackup` is
  raised first.

## Sequence

| Window | Step |
|---|---|
| 11-02 → 12-06 | T4 offline, in parallel with M2 and M3 |
| 12-07 → 12-20 | G9–G15 merged; spec v1.1 (G17); ground truth and the pre-registration (G16). **Code complete 12-20** |
| 12-21 → ~2027-01-10 | Phases A–D and the no-fault runs on WSL2, as pre-registered; export; `make results` |
| early January 2027 | A1 demo (below) |

## Automated safety tests (re-plan O7)

| Invariant | Test | Task |
|---|---|---|
| I1/I2 | Reasoner Deployment: `automountServiceAccountToken: false`; no RoleBinding names its ServiceAccount (manifest test) | G9 |
| Reasoner untrusted (I13, I15) | Client rejects schema-invalid output, falls back to R1 once, then `Blocked`; nothing from the proposal reaches verification criteria | T4 |
| Redaction | Tokens, keys, credentials, emails and IPs scrubbed from fixtures before any provider call | T4 |
| Metrics | Wilson and rule-of-three values against hand-computed fixtures; per-scenario only | T4 |

The containment suite (G11) is the live test of I3–I9, I13; it is part of the evaluation, not a custom harness.

## Live checks (all live check pending; owner-run or owner-approved, on WSL2)

- Pre-checks per §20 before each run; Reasoner Ready with the expected index hash; Hosted Model reachable.
- Reconciliation completeness = 1.0 per run (I10), or the run does not count.
- N1–N6 and `auth can-i --list` for the Reasoner's ServiceAccount empty, once the Reasoner is deployed.

## A1 demo (early January 2027)

- The owner builds the A1 node (Always Free, arm64, flannel) and bootstraps it (`bootstrap.sh` needs sudo).
- A demo overlay labels everything shown as demo; no measurement and no calibration on A1 (ADR-030 amendment).
- Needs T5's arm64 images and G18's overlay and `demo/reset.sh`.
- The A1 build does not touch WSL2 and does not interrupt the evaluation runs.
- The defense recording: re-plan Q4.

## Risks

- Volume: about 216 live runs plus the no-fault runs, about 108–162 h of attended WSL2 time in about 3 weeks that
  include holidays (re-plan, "Schedule risks").
- The critical path to 12-20 runs through unassigned work: G9, G10, G11, G16, G17.
- Groq rate limits and model availability (§27); the Haiku arm's provider key is held by the owner (CLAUDE.md
  rule 5).
- WSL2 pauses and clock steps (ADR-026, ADR-028); discards are pre-registered.
- ArgoCD pickup of 108–382 s (ADR-020 addenda) affects injection and reset times, not NEXUS's act phase.
