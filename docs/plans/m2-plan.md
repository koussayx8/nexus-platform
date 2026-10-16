> **Draft for GATE M2-0 (2026-10-16), rev 3, with the GATE M2-0 decisions recorded. Frozen at approval; current state lives in TASKS.md.**
> Rev 2 applied the owner's plan-review revisions 1–8. Rev 3 applies revisions 1–5 and the resolution of
> finding k (all 2026-10-16). Line references to `TASKS.md` are at `dev` `e5d16d2`.

# M2 plan: the operator acts (rev 3, for GATE M2-0)

## Context

M1b is complete: `v0.3.0` (annotated tag object `d038c34…`) dereferences to
`86cea844dff5dac8cbd3f61f57a3ffc94aac2838` (`git ls-remote origin 'refs/tags/v0.3.0*'`).
The README's M2 row reads: "Mutation path through the Scale and Eviction subresources (RBAC grants none
today), Safety Gate checks, autonomy level raised on `nexus-dev`".

- **Process:** the ADR-029 addendum (2026-10-16). Every merge, tag and forward-merge needs the owner's typed
  approval naming the full SHA (CLAUDE.md rule 11), checked against ADR-029 §1's conditions at the moment of the
  action. The current classic protection and ruleset 23998158 stay unchanged. The pasted-gate-replies rule holds
  until the M2 exit (ADR-029 §5).
- **Host:** ADR-030 (a). M2 and the defense demo run on an OCI Always Free A1 node as a new calibration. Nothing
  measured on WSL2 carries over.

Every scope line below names its source. A line with no source is UNKNOWN.

## Sources

| Tag | Source |
|---|---|
| README | The README's M2 row |
| §n | The spec sections the row implies: §4 stages, §9 Action Catalogue and Safety Gate, §11 RBAC, §12 autonomy, §14 Flight Recorder, §16 Rules Engine R1, §25 resources, the §27 `Audit-Id` item |
| ADR-025 | ADR-025's M2 configuration keys and operator metrics |
| Owner-M2-0 | The owner's GATE M2-0 decisions (2026-10-16), including the confirmation that `flight-recorder-db` is in M2. It replaces the untraced 2026-09-29 statement as the source |
| ADR-029 add. | The ADR-029 addendum, the M2 process (owner gate decision) |
| ADR-030 | ADR-030 (a)–(d) and the billing guard (owner gate decisions) |
| Owner-10-16 | The owner's plan-review decisions on this draft, 2026-10-16 |

## Decisions

**Owner, plan review (2026-10-16):**
- **RBAC staging.** M2 grants `apps/deployments/scale` and `pods/eviction` in `nexus-dev` only. The `nexus-prod`
  grants come in M3, with Kyverno in Enforce. An ADR at M2-7 records this as a staging of §11, as ADR-025 staged
  the M1b rows. §11's end state is unchanged. The exit checks with `auth can-i` that the operator cannot mutate
  `nexus-prod`.
- **L2 moves to M3 with K5 (AD-06):** the L2 flow, the operator's approval-content validation and `approvalTTL`.
  In M2 an L2 namespace stays `Escalated`, as in M1b; the reason code is fixed at M2-7's gate. In M2, L0 is
  `Recorded`, L1 `Recommended` and L3 acts. Finding h is settled: M3.
- **The proposal source (U1):** the Evidence Collector, the Evidence Redactor and the embedded Rules Engine (R1,
  §16). The Reasoner Client stays off, with `reasonerEndpoint` unset, until M4.
- **Levels (U2):** `nexus-dev` goes to `"3"` (`TASKS.md:103`, ADR-013, ADR-018), by commit on
  `experiment/dev-state`. `nexus-prod` stays at L1.
- **Where M2 runs (U10, U11):**
  - Live validation runs on WSL2 until M2-9.
  - Once A1 passes `verify-state.sh`, the owner stops WSL2's k3s (stopped, not deleted). It stays the exit
    fallback.
- **The exit (U7, U8):**
  - It runs on "the M2 host": A1 by default, or WSL2 if A1 is not provisioned and passing `verify-state.sh`
    by 2026-11-20 (GATE M2-0).
  - It uses a minimal `audit_id` join script. Full reconciliation (`reconcile-audit`, `audit_links`) stays in M4.
- **Later milestones (U9):** `nexusctl` goes to M3, as the L2 approval interface (§16A). The Flight Recorder
  datasource and the NEXUS Operations dashboard (§3) go to M4.
- **ADR-030's deliverables are traced** (former U6):
  - step 0 → M2-1;
  - (b) → M2-1b;
  - host topology → M2-4;
  - the A1 calibration → M2-9;
  - the demo recording → M2-10.
- **M2-1b, the x86 replication (ADR-030 (b)):**
  - It runs `v0.3.0` plus a pre-registered, parameter-only diff (re-derived limits, Locust users and autostart).
  - The scripts run on the VM, mirroring M1b's single-host topology. Any deviation is pre-registered.
  - **Branches (finding k, settled in approach).** The details are fixed at the M2-1b pre-registration. Two
    branches are cut from tag `v0.3.0`:
    - `replication/x86`: `root.yaml`, the child Applications and `bootstrap.sh`'s `origin/main` reads point to it;
    - `replication/x86-dev-state`: `sample-api-dev` tracks it, and the S5 inject and reset commit there.
  - **Note on S5.** In M1b, S5 was injected inside the database, not by a commit (spec §20; ADR-025 addendum, t0
    `ALTER ROLE app_dev NOLOGIN`). The pre-registration fixes which commits, if any, land on
    `replication/x86-dev-state`.
  - **Diff check.** A script runs `git diff v0.3.0` on each branch. It accepts the pre-registered parameter
    paths, plus exactly those ref lines, and nothing else.
  - **Owner actions in GitHub:**
    - lock `replication/x86` after the pre-registration commit;
    - protect `replication/x86-dev-state` like ruleset 23998158 (no force push, no deletion).
  - **The pre-registration names two commits:**
    - the replication head, the system under test;
    - the `dev` commit holding the timed-run scripts, the harness.
  - **Stop rule.** If the x86 host is not calibrated by 2026-11-01, (b) is recorded as not done.
  - **Billing guard.** Every trial resource is deleted before 2026-11-09: the instance, the boot and block
    volumes, any reserved IP and any paid load balancer.

**Owner, GATE M2-0 (2026-10-16):**
1. **(c) is dropped.** The AWS credit lapses on 2026-11-03, and nothing is created on AWS. The M2-4 ADR-030
   addendum records the drop, and Spec v1.1 notes contradiction 3 as moot.
2. **The A1 date is 2026-11-20.** If A1 is not provisioned and passing `verify-state.sh` by then, the exit runs on
   WSL2 (U7).
3. **The placements are accepted as drafted** (below).
4. **`flight-recorder-db` is in M2** (owner confirmation, 2026-10-16). This is the source of scope line 9, in place
   of the untraced 2026-09-29 statement.

**Deferred UNKNOWNs (each has its deciding gate):**
- U3, the operator metric names: ADR-025 names none. Decided at M2-7's gate.
- U4, durable absorb: ADR-025, "left to M2" (it needs the poller to write status, against the single writer).
  Decided at M2-7's gate.
- U5, from ADR-026 and ADR-027. Decided at the M2-9 pre-registration:
  - R2, the per-pod `/work/cpu` capacity, deferred to M2/S2 (owner, 2026-10-06, `TASKS.md:780`);
  - the "twice per-pod capacity" reading;
  - `scale_v1`'s "CPU per pod below 70 % of limit", which now means 700m.

## Scope, traced

| # | Scope line | Source | Phase |
|---|---|---|---|
| 1 | The §11 operator rows not granted today. The cluster reads: `pods`, `pods/log`, `events`, `services`, `endpoints`; `apps/deployments`, `apps/replicasets`; `endpointslices`. Plus `argoproj.io/applications` in `argocd`. `deployments/scale` (get, patch, update) and `pods/eviction` (create) in `nexus-dev` only, staged; `nexus-prod` in M3. None of them is granted today (`platform/rbac/nexus-operator.yaml`) | README, §11, Owner-10-16 | M2-7 |
| 2 | Action Catalogue `operator/catalogue.yaml`: the four frozen entries with bounds, preconditions, verification criteria and compensation | §9, §25 | M2-7 |
| 3 | Evidence Collector (`evidence/v1`), Evidence Redactor, embedded Rules Engine R1. The output is validated as `proposal/v1`. Redacted bundles are stored in `evidence_bundles` (NF-24) | §4, §14, §16, Owner-10-16 | M2-7 |
| 4 | Action Executor: the intent row and `status.execution.idempotencyKey` are written before one Scale or Eviction request; on resume it checks the real replica count or pod UIDs | README, §4, §14 | M2-7 |
| 5 | Safety Gate stages 1–7, advisory, in fixed order; the first failing stage and its reason are recorded; `advisoryChecks` on/off. The Kill Switch is read in stage 5, advisory; K4 is authoritative in M3. Stage 8 and L2 are M3 | §9, ADR-025, Owner-10-16 | M2-7 |
| 6 | Circuit Breaker (2 per target per 15 min, 6 per hour) and cool-down (5 min), counted from the Flight Recorder since `breakerEpoch` | §4, §9, ADR-025 | M2-7 |
| 7 | Verifier (`restart_v1`, `scale_v1`) against the raw pre-fault baseline recorded at detection (ADR-024), never the alert state. Compensator (scale back, itself a gated request). Escalator (terminal hand-off in status; the fork-PR path is a SHOULD, M6 in §27) | §9 | M2-7 |
| 8 | Levels: L0 `Recorded`, L1 `Recommended`, L2 `Escalated` (M1b behaviour), L3 acts. `nexus-dev` → `"3"` by commit on `experiment/dev-state`; `nexus-prod` at L1; the stage-4 pre-check | README, §12, Owner-10-16 | M2-7, M2-10 |
| 9 | `flight-recorder-db`: a PostgreSQL StatefulSet with a persistent volume in `nexus-system`, through the `nexus` Application. The §14 schema; the Flight Recorder Writer (idempotent, one row per transition); the write-ahead rule (`Blocked` / `recorder_unavailable`) | Owner-M2-0, §14, §25 | M2-6, M2-7 |
| 10 | The `Audit-Id` spike with a dry-run request; the id is stored per request (NF-05) | §27 | M2-5 |
| 11 | The operator reads `advisoryChecks`, `breakerEpoch` and `reasonerEndpoint` (unset, so the client is off). `approvalTTL` is M3 | ADR-025, Owner-10-16 | M2-7 |
| 12 | Operator metrics and their ServiceMonitor (names: U3) | ADR-025 | M2-7 |
| 13 | A CI boundary check: every field reachable through the catalogue and the operator's RBAC is delegated by `ignoreDifferences` with `RespectIgnoreDifferences=true`, or never declared | §9 (the scale precondition is "CI-checked"), §13 | M2-7 |
| 14 | Step 0, the (b) replication, the host topology and CNI, the A1 calibration, the demo recording | ADR-030 | M2-1, M2-1b, M2-4, M2-9, M2-10 |
| 15 | A minimal `audit_id` join script for the exit | Owner-10-16 | M2-10 |

## Gate reports a–k (commands run this session, read-only)

**a) Precondition — VERIFIED.**
- `git rev-parse HEAD` on `dev` → `e5d16d261c946a97ff9a54e2efff23dd20244d0b`; the tree is clean.
- After `git fetch`: `origin/dev` `e5d16d2…`, `origin/main` `86cea844…`, `origin/experiment/dev-state`
  `0ceb290e…`. `v0.3.0^{}` = `86cea844…`.

**b) The explicit rollout strategy — CONTRADICTED as an open item.**
- `git log -S'maxUnavailable: 0'` → `fc2255b` (2026-09-29, "explicit RollingUpdate strategy, maxSurge 1,
  maxUnavailable 0").
- `apps/sample-api/k8s/base/deployment.yaml:16-20` holds it, and `git merge-base --is-ancestor` puts it on
  `origin/main`, `origin/experiment/dev-state` and `origin/dev`.
- The Later item's "today it is the default" is stale. Proposal: a `verify-state.sh` check of the live strategy
  before the first Scale (M2-8), then close.

**c) envtest harness renewal — CONTRADICTED as open work.**
- `b9f62f0` ("envtest renews its PKI when a certificate expires within 24 h", `scripts/tests/envtest.sh`) is an
  ancestor of `dev`.
- Proposal: one envtest run at M2-7 confirms it, then close.

**d) The sample-api lock file — VERIFIED merged.** `apps/sample-api/requirements.txt` carries 571 `hash=` entries
(the hash-pinned closure, ADR-022). Close.

**e) The operator image's "base pinned by digest" — CONTRADICTED in part.**
- `operator/Dockerfile:4,9` already pins `python:3.12-slim@sha256:dddfd7e0…` (an index digest, ADR-025).
- `apps/sample-api/Dockerfile:2,8` uses `python:3.12-slim` by tag; its pin goes to M2-2.
- The open operator part is the UID 10001 passwd entry (`USER 10001:10001` at line 16, with the `USER` env
  workaround in the manifest).

**f) The §3 kube-state-metrics alerts — VERIFIED open.** `grep -rn 'NexusRolloutStuck\|NexusCrashLooping\|group_by'
platform/` returns nothing.

**g) Audit-log retention — VERIFIED open.**
- `scripts/bootstrap.sh:204-206`: `audit-log-maxage=30`, `audit-log-maxbackup=10`, `audit-log-maxsize=100`.
- The §14 audit policy already covers `deployments/scale` and `pods/eviction` (`bootstrap.sh:178-180`).
- A fresh bootstrap on the new host applies a new value without a k3s restart.

**h) `approvalTTL`'s milestone — settled: M3** (owner, 2026-10-16). ADR-025:107 listed it among the M2 keys; the
template comment in `platform/bootstrap-templates/nexus-operator-config.yaml` says M3. The template comment
stands.

**i) Operator metrics — VERIFIED absent.** No prometheus import in `operator/requirements.txt` or
`operator/nexus_operator/`. Today only Kopf's liveness endpoint listens (`:8080/healthz`).

**j) Stale status lines.**
- `TASKS.md:3-4` still named M1b-0; this PR fixes it.
- The README's M1b row reads "In progress", and the M1b-9 L2 and closing-PR boxes are unticked after the M1b exit.
  These go to the Hygiene PR.

**k) The replication branch and the tracked refs — CONTRADICTED, settled in approach.**
- At `v0.3.0` (`git grep targetRevision v0.3.0 -- platform/argocd`), `root.yaml:24` and every child Application
  track `main`, except `sample-api-dev`, which tracks `experiment/dev-state`.
- `bootstrap.sh:151-162` (the merge-order guard) and `:439-446` (the AppProject and `root.yaml`) read
  `origin/main`.
- So a parameter-only diff alone would leave the x86 cluster following `main` as M2 moves it.
- The owner's resolution is in Decisions (M2-1b): two branches, ref lines on the diff-check allow list, and a
  GitHub lock and protection.

## Design notes

Each phase is planned in detail at its own gate. The notes below fix only what the scope implies.

- **RBAC.**
  - The staged Role and RoleBinding for `deployments/scale` and `pods/eviction` live in `nexus-dev` only. The
    cluster reads go in a ClusterRole, and `argoproj.io/applications` in a Role in `argocd`.
  - Never granted: delete, patch on Incident spec or metadata, and writes to namespaces, ConfigMaps, Secrets, RBAC
    or policies (§11). The `impersonate`, `bind` and `escalate` verbs are also never granted.
  - RBAC is checked with `kubectl auth can-i --list`; the `kubectl * create *` deny would catch a per-verb check.
- **Flight Recorder.**
  - The `nexus-system` Secret follows the ADR-019/ADR-020 generate-or-reuse pattern.
  - The operator gets the credentials as a pod-level mount, never through Secrets RBAC.
  - The AppProject widens in the same PR (standing rule).
  - An unavailable recorder blocks the mutation (§17, fail closed).
- **Rules Engine.** R1 is implemented from the §16 table. The runbooks (§15) come in M4 and must agree with it.
  `status.reasoner.mode` records `rules`.
- **`Audit-Id`.** The operator's own client is aiohttp (ADR-025), not the official Python client. The M2-5 spike
  reads the response header on a `dryRun=All` request against envtest, then once live.
- **The M2 host.** Each host is bootstrapped by the owner (`bootstrap.sh` needs sudo). The agent creates no cloud
  resource and runs no Terraform (ADR-030 Consequences).

## Where the Later items go

Accepted as drafted at GATE M2-0 (owner, 2026-10-16). Each item moves verbatim, with its phase tag.

| Later item | Placement |
|---|---|
| Explicit sample-api rollout strategy | M2 core, M2-8 (a verify check, then close; see b) |
| M2: repair verification against the raw baseline | M2 core, M2-7 |
| M2: F5, SIGKILL after the grace period | M2 core, M2-8 |
| §3 KSM alerts and `group_by: [nexus_target]` | M2 core, M2-8 |
| `verify-state.sh` gains checks per milestone | M2 core, M2-8: FR-db Ready, `auth can-i` staging, the strategy, the levels. K1–K6 and N1–N6 → M3; Reasoner → M4 |
| Operator image: passwd entry for UID 10001 | M2 core, M2-2 |
| Base images: digest pins (sample-api) | M2 core, M2-2. The slimmer base and its Trivy comparison → M3, with `verifyImages` |
| Supply chain: actions by tag; sample-api base by tag | M2-2 (ADR-030 track: actions pinned by SHA; the Dockerfile pin as above) |
| `cosign-installer` pinned by SHA | ADR-030 track, M2-2 |
| CI per §19: Trivy on the pushed digest; the digest-bump PR step | ADR-030 track, M2-2 (both parts) |
| envtest harness PKI | M2 core, M2-7 (confirm and close; see c) |
| Audit-log `maxbackup` | M2 core, M2-3 |
| Spec v1.1 notes (Z-score lag; in-flight signal; `observability`; dev-state sequencing, ruleset, unfiltered check; ADR-030's three contradictions) | M2 core, M2-4: one Spec v1.1 PR |
| Fresh bootstrap: operator config ordering | ADR-030 track, M2-3 |
| `bootstrap.sh` hardening | Part (2), the readyz wait → ADR-030 track, M2-3. Part (1), the mounts preflight → Hygiene (WSL2 stays the fallback host) |
| `observability` timeout | ADR-030 track, M2-3 |
| Secretbox encryption | ADR-030 track, M2-3 |
| Grafana sidecar resources | ADR-030 track, M2-9 |
| M2: the client-side connection cost | ADR-030 track, M2-9 |
| Timed-run analysis: timestamp parsing | M2-1b, with the timed-run scripts moved into the repo |
| M1b-9 leftovers | Tooling: the scripts in the repo and the `ramp.py` per-step counters → M2-1b. `lddq8` and the zero-traffic question → re-check on the new host, M2-9. Server-host replication → Close (now M2-1b) |
| Locust `--autostart` | M2-1b (the decision) |
| `AlertmanagerClusterCrashlooping` | Re-check on the new host, M2-9 |
| Gate rule: dashboards closed | Standing rule, dashboards closed during timed runs |
| M2: approval-content validation until K5 | M3, with K5 and the L2 flow (owner, 2026-10-16) |
| ArgoCD pickup and the runner's refresh | M4 (runner milestone, as labelled) |
| Re-measure audit growth | M4, as labelled |
| M4: runner pre-check `baseline_stddev15m` | M4, as labelled |
| M3: NetworkPolicies and the node IP; CODEOWNERS; `verifyImages` | M3, as labelled |
| Locust master API unauthenticated | M3 |
| Automate post-deploy verification | M3 |
| shellcheck in CI; `capture-state.sh:508`; ruff `src`; the `requirements-dev` leftovers (still at lines 9–12); the Starlette test client; Dependabot targeting `dev`; the `repo-checks` range; the Crossplane exclusions; the historical docs | Hygiene, one PR that gates nothing |
| A CI job for `incident-crd.sh` on envtest | Hygiene (coverage; it gates nothing) |
| Stale status lines (j) | Hygiene |
| Grafana starvation | Close: done in M1b-3 |
| ADR-021 write-up | Close: done in M1b-0 |
| The S5 pin guard | Close: removed (#131) |
| WSL2 VM pause cause | Close: closed 2026-10-02 |
| sample-api lock file | Close: merged (d) |
| Agent kubeconfig | Close: rejected (ADR-021) |
| Local allow-list regrowth | Close: owner, 2026-10-16 |
| The 2026-09-26 WSL VM reboot | Close: informational; the timed-run pause and `boot_id` rule covers it |
| Repository About and topics | Close: an owner action outside Git; nothing depends on it |
| Local Backstage build output | Close: local and untracked, the owner's |

## Phases and gates

| Phase | Content | Cluster effect | Gate |
|---|---|---|---|
| **M2-0** | This PR: the plan, the `TASKS.md` M2 section, the Later items placed | none | **GATE M2-0** |
| **M2-1** | ADR-030 step 0 on WSL2. With Locust stopped (an approved live step): node CPU and memory use, and the summed pod requests and limits. A screen only | Locust stop and resume | **GATE M2-1**; a decision gate if there is no room on 2 OCPU / 12 GB |
| **M2-1b** | ADR-030 (b), the x86 replication. Prerequisites on `dev`: the timed-run scripts (offset-safe timestamps, `ramp.py` per-step counters); the Locust autostart decision; the diff-check script, with fixtures, accepting only the parameter paths and the ref lines. Then `replication/x86` and `replication/x86-dev-state` from tag `v0.3.0`, and the pre-registration PR: shape and size, path list, ref lines, attempt count, pass and void rules, scripts on the VM, and the two named commits. Owner: lock and protect the branches, create and bootstrap the VM. Then the protocol: R1 → C, B and limits → the clean-baseline checkpoints → S5. Stop rule 2026-11-01; billing guard before 2026-11-09 | x86 host (owner) | **GATE M2-1b** pre-registration (decision); **GATE M2-1b** result |
| **M2-2** | arm64: `ci.yml` and `operator.yml` build `linux/amd64` and `linux/arm64`; actions and `cosign-installer` pinned by SHA; Trivy scans the pushed digest; the digest-bump PR step. The operator rebuild (UID 10001 passwd entry; the `USER` env and its test inverted); sample-api multi-arch with its base pinned by digest. Re-pins to the multi-arch indexes after `cosign verify` (ADR-017, two-merge) | WSL2 pods roll | **GATE M2-2** |
| **M2-3** | `bootstrap.sh` before the first cloud bootstrap: operator-config ordering, the readyz wait, the `observability` timeout, secretbox, audit-log `maxbackup`. `--plan` with stubs and the fault-injection rerun in a throwaway worktree | none | **GATE M2-3** |
| **M2-4** | ADR-030 addendum: host topology for (a), the CNI (flannel unless (d)'s three conditions hold), the (c) drop. Then the one Spec v1.1 PR | none | **GATE M2-4** (decision) |
| **M2-5** | The `Audit-Id` spike: a `dryRun=All` request on envtest, then one approved live dry-run on WSL2; an ADR | one dry-run | **GATE M2-5** |
| **M2-6** | `flight-recorder-db`: StatefulSet with a PV, the §14 schema, the Secrets script and its bootstrap call, the `nexus` Application, the AppProject widened; an ADR. Validation: kustomize, `kubeconform -strict`, the render check, then a server-side dry-run with approval | DB live on WSL2 | **GATE M2-6** |
| **M2-7** | The operator's M2 stages, offline first (unit tests and envtest): scope 1–13, the RBAC-staging ADR, envtest renewal confirmed. U3 and U4 are decided here. PR A (code) and PR B (deploy), as in M1b-8. Deployed with `nexus-dev` still at L0 | WSL2: operator rolls; dev RBAC widens | **GATE M2-7** |
| **M2-8** | The §3 KSM alerts and `group_by: [nexus_target]` with promtool tests; F5 (the SIGKILL decision; two-merge); the M2 `verify-state.sh` checks | WSL2: rules reload; sample-api rolls | **GATE M2-8** |
| **M2-9** | A1. The owner bootstraps it; then `verify-state.sh`. The calibration under ADR-030 (a) steps 2–5: idle footprint, C and B by R1, limits by the ADR-027/ADR-028 principle; the Grafana sidecar resources, the connection-cost measurement, the three re-checks. The pre-registration PR, which settles U5. Then the owner stops WSL2's k3s | A1 live; WSL2 stopped | **GATE M2-9** (decision) |
| **M2-10** | On the M2 host: `nexus-dev` → `"3"` (commit on `experiment/dev-state`); the exit demo (below); the defense recording (ADR-030 (a)); `CURRENT_STATE.md`; `CHANGELOG` [0.4.0]; the tag (separate typed approval) | level raised | **GATE M2 exit** |

- The Hygiene PR runs in parallel and gates nothing.
- **Proposed rule:** M2-9 calibrates the stack as M2-6 to M2-8 leave it. A later change to its steady state is a
  decision gate.
- **The exit criterion (U7, proposed):** on the M2 host, under baseline load B:
  - S2 (or S1) is injected on `nexus-dev` at L3, and the Incident ends `Resolved` / `remediation_verified` through
    the catalogue action;
  - the intent row precedes the request, and its `audit_id` matches an API Audit Log event (the minimal join
    script);
  - the same fault class in `nexus-prod` ends `Recommended`;
  - `kubectl auth can-i --list` shows no `deployments/scale` or `pods/eviction` for the operator in `nexus-prod`,
    and the audit extract shows no operator mutation there.

## Verification

- **Offline:**
  - `ruff`, `pytest` (operator and sample-api), the envtest integration tests;
  - `kustomize build`, `kubeconform -strict`, the render check, `.github/scripts/repo-checks.sh`;
  - promtool tests, `bootstrap.sh --plan` with stubs, shellcheck;
  - the M2-1b diff check against its fixtures.
- **Live, each step approved:**
  - server-side dry-runs;
  - after each gate: every Application `Synced`/`Healthy` and stable for at least 60 s, and `verify-state.sh`
    exits 0;
  - RBAC with `kubectl auth can-i --list --as=system:serviceaccount:nexus-system:nexus-operator`, in
    `nexus-dev` and `nexus-prod`.
- **Behaviour:** the exit demo, with the audit extract joined to the Flight Recorder by `audit_id`.

## Assumptions (open ones only)

- The API server returns `Audit-Id` on every response, dry-run included, and aiohttp exposes it. Checked in M2-5.
- envtest evaluates the PDB on an eviction against Pod objects without a kubelet. Checked in M2-7.
- Both hosts provide `local-path` PVs, as Prometheus uses today. Checked in M2-6.
- The metrics port does not contradict §3's "the operator exposes no inbound surface". That sentence is about
  Alertmanager receivers, and Kopf's liveness already listens on 8080.
- Until M3 there are no NetworkPolicies, so any pod can reach `flight-recorder-db` on 5432 and the metrics port.
- The python base index `dddfd7e0…` includes `linux/arm64`. Checked in M2-2.
- `systemctl stop k3s` may leave pods running, Locust traffic included (UNVERIFIED for `v1.34.6+k3s1`). The k3s
  docs stop containers with `k3s-killall.sh`, which the agent's deny list blocks. The owner picks the method at
  M2-9.

## Risks

- **Until M3, `nexus-dev`'s floor, bounds and Kill Switch are advisory operator checks.** RBAC (staged to
  `nexus-dev`) and the PDB are the authoritative layers, so an operator defect is confined to `nexus-dev`.
- **A WSL2-fallback exit** uses the M1b WSL2 calibration (B = 80), whose checkpoint B failed twice (ADR-026,
  ADR-028).
- **A second PostgreSQL** has to fit A1's 2 OCPU / 12 GB.
- **(b)'s dates:** calibration by 2026-11-01, every trial resource deleted before 2026-11-09.
- **(b)'s branches:**
  - Until the owner locks `replication/x86` and protects `replication/x86-dev-state`, nothing in GitHub stops a
    push to them.
  - The diff check catches a change outside its allow list only when it runs, so it runs at every (b) gate.
  - Which commits land on `replication/x86-dev-state` is fixed at the pre-registration (see the S5 note).
- **Two clusters track `main` until M2-9.** Every gate merge deploys to WSL2 while it runs.
- **Size:** M2 spans many PRs. Each phase is planned at its own gate, and its details may move a phase boundary,
  but not this scope without a decision.
