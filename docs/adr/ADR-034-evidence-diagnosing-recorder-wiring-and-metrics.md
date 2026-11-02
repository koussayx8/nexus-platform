# ADR-034: Evidence Collector, Diagnosing, Writer Wiring and Operator Metrics

**Status:** Proposed (task 2b, PR to `dev`)
**Date:** 2026-11-02
**Spec:** §4, §7, §9, §11, §14, §16, §17; ADR-024, ADR-031, ADR-032; Q1 (redaction in the operator), U3 (metric names), I11

## Decisions

| # | Decision |
| --- | --- |
| 1 | **The real Writer is wired in.** `ReconnectingRecorder` (in `nexus_operator/recorder/`) connects on first use and again after any failure; the password is read from a mounted file at each attempt. With no `recorderHost`, or while the DB is down, every call raises `RecorderUnavailable` and a mutation ends `Blocked` / `recorder_unavailable` before any request. The operator itself still starts, so a DB outage never takes detection down (§17). |
| 2 | **Config keys** in `nexus-operator-config`: `recorderHost`, `recorderPort`, `recorderDatabase`, `recorderUser`, `recorderPasswordFile` (default `/var/run/nexus/flight-recorder-writer/password`), `prometheusURL`, `metricsPort`. The password is never a ConfigMap value. The Deployment mounts only the Secret `flight-recorder-writer`, optional, read by `fsGroup` 10001. |
| 3 | **asyncpg 0.31.0** is a runtime dependency in `operator/requirements.txt` with the cp312 `manylinux_2_28` hashes for `x86_64` and `aarch64`, each checked with `pip download --require-hashes --only-binary=:all: --platform …` (the method of the aarch64 hashes already in the file). It leaves `requirements-dev.txt`. |
| 4 | **Evidence Collector (`evidence/v1`).** Read-only: Deployment, ReplicaSets, pods, events (15 min, at most 50), logs (200 lines, 3 pods, current and previous), Argo CD Application, and Prometheus Z-scores. Anything unavailable is `null`, never invented. `metrics.errorRatio` (the absolute `…error_ratio:rate2m`) is an addition to the §16 example, because R1 rule 5 needs an absolute 1 % and the bundle otherwise holds Z-scores only (**spec v1.1 note**). `gitops.lastCommit.message` is `null`: the Application does not carry it. |
| 5 | **Redaction happens in the operator, before anything is hashed, stored or handed on (Q1).** `redact.py` scrubs private keys, URL credentials, JWTs, bearer/basic tokens, vendor tokens and keys, `password=`-style assignments, emails and IP addresses in every string at any depth, counts each class, and the redacted bundle is what is hashed, written to `evidence_bundles`, and given to R1. The raw bundle exists only inside the Diagnosing call. A test poisons every free-text field and checks the bundle, proposal, transitions, gate rows and Incident status, both on a fake recorder and in a real PostgreSQL. |
| 6 | **Diagnosing.** `Detected` → `Diagnosing` once the intake level is known and is 1-3; collect, redact, store, run R1, record the proposal, then `Proposed` with `status.evidence`, `reasoner` (`mode: rules`), and `proposal`. Failures retry each tick until the 60 s Diagnosing timeout: an unreadable target ends `Escalated` / `evidence_error`; an unreachable recorder ends `Blocked` / `recorder_unavailable`. `status.evidence.baseline` is the raw mean and standard deviation of the p95 recording rule at detection, which the Verifier compares against (ADR-024). The Reasoner Client is M4's: `reasonerEndpoint` stays unread. |
| 7 | **Metrics (U3), proposed names**, prefix `nexus_operator_`, served by aiohttp on 9102 with no new dependency: `incidents_total{phase,reason}`, `gate_decisions_total{stage,verdict}`, `actions_total{action_id,result}`, `recorder_errors_total`, `recorder_up`, `redactions_total{class}`, `diagnose_seconds_{sum,count}`, `loop_age_seconds{loop}`, `alertmanager_errors`, `killswitch_halted`. Labels are spec ids and reason codes, never free text. A ServiceMonitor (`platform/observability/monitors/`) and a Service select the `metrics` port; NetworkPolicy `operator-ingress` now admits Prometheus on 9102. |
| 8 | **RBAC reads.** The cluster-wide read row of §11 is granted as a ClusterRole (`nexus-operator-evidence`: pods, pods/log, events, services, endpoints, deployments, replicasets, endpointslices; get, list, watch), plus Argo CD Applications in `argocd`. This supersedes ADR-032 decision 5 and keeps ADR-032 decisions 1-4: **mutations stay `nexus-dev`-only**, and there is no namespaced right in `nexus-prod` or `nexus-data`. The reads are what lets an Incident in `nexus-prod` (L1) be diagnosed and recommended. |
| 9 | **G2: the Compensator (I11).** `Verifying` leads only to `Resolved` or `Compensating`. The existing scale-back covers the Compensator: a failed `scale_v1` goes `Verifying` → `Compensating` → `Escalated`, the scale-back is a gated request (kill switch, dry-run, write-ahead row of class `compensation`), a halted kill switch ends `compensation_blocked`, and a failed `restart_v1` passes through `Compensating` with nothing to undo (the catalogue has no compensation for it) and ends `verification_failed`. One gap was open: the machine was only a convention in the code. `act.LEGAL` now lists D9's edges and `_move` refuses and logs any other, so I11 holds in code; `test_transitions.py` checks every illegal pair, every edge the scenarios take, and that a failed scale is compensated before hand-off. |

## Assumptions to confirm

- `breakerEpoch` is Unix seconds (§4 gives no unit).
- A completion write that fails on the step that ends an Incident stays incomplete; reconciliation flags it.
- `execution_timeout` is the reason for an execution error other than a 403 or 429.

## Consequences

- With the Secret present and `recorderHost` set, the image after the next digest bump records for real; **live check pending** for the DB, `Audit-Id` on a live API server, the ServiceMonitor target, Kyverno and the PDB.
- `Diagnosing` writes to the Incident status and to the recorder; it never mutates a workload.
