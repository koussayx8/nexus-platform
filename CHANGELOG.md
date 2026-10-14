# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses milestone tags (`v0.N.0`, one per milestone exit).

## [Unreleased]

## [0.3.0] - 2026-10-14

M1b — detection and the operator skeleton (spec §3, §11, §25, §27): Z-score detection against a
lagged baseline, the Incident CRD, the operator's Alert Poller and status-only reconcile loop, the
sample-api fault hooks, and a Locust baseline load. Exit demo: S5 (`app_dev` `NOLOGIN`) under
B = 80 req/s per namespace; `NexusErrorRateAnomaly` fired for `nexus-dev` and one Incident ended
`Recorded` (`level_observe`, L0) 129.7 s after the injection (ADR-025 addendum, 2026-10-14).

### Added
- Detection: the spec §3 Z-score recording rules against a lagged baseline (`[15m] offset 3m`,
  27-sample guard) and the four anomaly alerts in `nexus-detection.yaml`, with promtool tests in
  `repo-checks` ([#79](https://github.com/koussayx8/nexus-platform/pull/79), ADR-024).
- The Incident CRD with CEL transition rules C1–C4 and `Prune=false,Delete=false` ([#86](https://github.com/koussayx8/nexus-platform/pull/86)); the
  Kopf status-persistence spike ([#87](https://github.com/koussayx8/nexus-platform/pull/87), ADR-023); the envtest harness and the detection-inputs
  checker ([#84](https://github.com/koussayx8/nexus-platform/pull/84), [#85](https://github.com/koussayx8/nexus-platform/pull/85)).
- The operator skeleton: Alert Poller with episode dedupe, a 5 s status-only loop (L0 → `Recorded`,
  L1–L3 → `Escalated` after 20 s), unit and envtest tests, image signed with Cosign ([#96](https://github.com/koussayx8/nexus-platform/pull/96)); its
  deploy through the `nexus` Application with staged §11 RBAC ([#99](https://github.com/koussayx8/nexus-platform/pull/99), ADR-025).
- sample-api 0.3.0: fault hooks `/fault/hang`, `/work/cpu`, `/fault/inject-logs` behind
  `NEXUS_FAULTS_ENABLED`, in-flight gauge, blinding, hash-pinned runtime set ([#88](https://github.com/koussayx8/nexus-platform/pull/88), [#91](https://github.com/koussayx8/nexus-platform/pull/91), ADR-022).
- Locust in `nexus-load` and the R1 capacity ramp `experiments/calibration/ramp.py` with offline
  tests ([#106](https://github.com/koussayx8/nexus-platform/pull/106), [#108](https://github.com/koussayx8/nexus-platform/pull/108), [#117](https://github.com/koussayx8/nexus-platform/pull/117), [#118](https://github.com/koussayx8/nexus-platform/pull/118), ADR-026).
- The S5 pin guard (`repo-checks` step 9): the 4 rule-defining files must equal `d351d964`, and every
  listed path must exist at the pin ([#96](https://github.com/koussayx8/nexus-platform/pull/96), [#124](https://github.com/koussayx8/nexus-platform/pull/124), ADR-025).

### Changed
- Locust baseline: B = 12 ([#109](https://github.com/koussayx8/nexus-platform/pull/109)), then B = 80 req/s per namespace, `--autostart --users=160`
  ([#119](https://github.com/koussayx8/nexus-platform/pull/119), ADR-026, ADR-027).
- sample-api 0.3.1: latency buckets no wider than 10 ms at 10–50 ms ([#111](https://github.com/koussayx8/nexus-platform/pull/111), [#113](https://github.com/koussayx8/nexus-platform/pull/113), ADR-022 addendum).
- sample-api CPU limit 200m → 1000m ([#115](https://github.com/koussayx8/nexus-platform/pull/115), ADR-027); `dependency-db` CPU limit 500m → 2000m
  ([#121](https://github.com/koussayx8/nexus-platform/pull/121), ADR-028).
- Grafana resources sized to an open dashboard: CPU limit 1000m, memory 1Gi, readiness timeout 5 s
  ([#93](https://github.com/koussayx8/nexus-platform/pull/93), ADR-016 addendum).
- `.claude/settings.json` guard model: allow and deny patterns, merge and tag procedure
  ([#82](https://github.com/koussayx8/nexus-platform/pull/82), ADR-021).

### Removed
- The interim `SampleAPIHighErrorRate` alert, replaced by `NexusErrorRateAnomaly` ([#79](https://github.com/koussayx8/nexus-platform/pull/79)).

### Fixed
- The operator starts as image UID 10001 without a passwd entry (`USER` set; the #100 crash loop)
  ([#101](https://github.com/koussayx8/nexus-platform/pull/101)).

### Known limitations
- The 60-min clean window at B = 80 was not reached: checkpoint B failed twice (A1, A2, D1, D2);
  "quiet when healthy" is open (ADR-026 M1b-9 summary, ADR-027 note, ADR-028).
- S5's first fire came at +119.5 s against a predicted +105 s (recorded, not analysed).

## [0.2.0] - 2026-09-29

M1 — the Dependency DB and `/items` (spec §3, §20, §25): a PostgreSQL Dependency DB in
`nexus-data`, and `sample-api` `/items` reading it, so that scenario S5 (application role
`NOLOGIN`, sessions terminated) is a gray failure: `/items` 5xx while readiness stays green
(NF-23). The rest of what was tagged M1 moves to M1b.

### Added
- `sample-api` `/items`: a new connection per request, `connect_timeout=2`,
  `statement_timeout=500`, a per-pod `BoundedSemaphore(5)` (503 `db_slots_exhausted`), 503
  `db_unavailable` with the server's error text logged on one line; version 0.2.0
  ([#69](https://github.com/koussayx8/nexus-platform/pull/69), ADR-020).
- `dependency-db` Application: a PostgreSQL 17.11 StatefulSet (pinned digest, `emptyDir`,
  restricted Pod Security), headless and ClusterIP Services, an init script creating the
  per-environment roles `app_dev` / `app_prod` (`CONNECTION LIMIT 35`), probes gated on an
  init-done marker; the AppProject admits `apps/StatefulSet`
  ([#70](https://github.com/koussayx8/nexus-platform/pull/70), ADR-020).
- `scripts/dependency-db-secrets.sh`, called by `bootstrap.sh`: creates the three DB Secrets
  if absent from `~/.nexus`, prints names only, and refuses to regenerate while any Secret exists
  ([#72](https://github.com/koussayx8/nexus-platform/pull/72), ADR-020).
- `scripts/lib/apps-stable.jq`: every Application Synced, Healthy **and at the expected commit**
  in one snapshot; `bootstrap.sh` step h and `verify-state.sh` M1 both require it for 60 s
  ([#72](https://github.com/koussayx8/nexus-platform/pull/72)).
- `verify-state.sh`: the `dependency-db` Application and pod check (M9), container restart
  counts (I1) ([#72](https://github.com/koussayx8/nexus-platform/pull/72)); the `/items` check
  (M10, `NEXUS_VERIFY_ITEMS_NAMESPACES`) with offline tests in `repo-checks`
  ([#75](https://github.com/koussayx8/nexus-platform/pull/75)).
- `bootstrap.sh`: UTC timestamps on every step header, `set -e` as a backstop
  ([#72](https://github.com/koussayx8/nexus-platform/pull/72)).

### Changed
- `sample-api` in both environments: digest `sha256:8ea896c2…e0e9af` (cosign-verified), DB
  environment from `secretKeyRef` ([#75](https://github.com/koussayx8/nexus-platform/pull/75)).
- `verify-state.sh` Application bound, derived as additive terms: 660 s
  ([#72](https://github.com/koussayx8/nexus-platform/pull/72)) → 840 s after the pull allowance
  rose to 300 s ([#75](https://github.com/koussayx8/nexus-platform/pull/75)) → 1140 s after the
  measured ArgoCD pickup delay ([#77](https://github.com/koussayx8/nexus-platform/pull/77));
  `bootstrap.sh` DB wait 900 s ([#75](https://github.com/koussayx8/nexus-platform/pull/75)).
  ADR-020 addenda.
- `pytest`, `pytest-asyncio` and `httpx` moved out of the runtime image into
  `requirements-dev.txt` ([#69](https://github.com/koussayx8/nexus-platform/pull/69)).
- Development tooling configuration ([#71](https://github.com/koussayx8/nexus-platform/pull/71),
  [#78](https://github.com/koussayx8/nexus-platform/pull/78)).

### Security
- k3s Secrets encryption at rest: `bootstrap.sh` writes `secrets-encryption: true` before the
  first server start ([#75](https://github.com/koussayx8/nexus-platform/pull/75), ADR-019
  addendum); confirmed `Enabled` after the M1 exit rebuild.

### M1-exit evidence
- A from-empty rebuild on 2026-09-28 reached `bootstrap: done` and `verify-state.sh` 10/10 in
  28 min 25 s, after a first attempt failed on a Docker Desktop mount that breaks the kubelet
  (Later item). The DB image pull took 377.3 s under contention; the 300 s allowance is scoped to
  an uncontended pull (ADR-020 addendum).
- S5 on `app_dev`: `/items` 503 in 12–17 ms with the `FATAL` text logged, `/ready` 200, prod
  unaffected, reset to 200, DB pod unchanged.
- 24 h audit window: retention **2.19 days** (uptime-based, about 500 MiB per idle day) against
  change 7's ~10 (ADR-019 addendum). The VM paused 2,921.9 s with Windows sleep off; the window
  was kept by owner override D1, normalized per uptime second.
- `verify-state.sh` after the window (2026-09-29T06:57:46Z): 10/10, exit 0, in 77 s
  (`docs/CURRENT_STATE.md`).

## [0.1.0] - 2026-09-26

M0 — verify, stabilise, govern (spec §25, §27): the first governed baseline. Git is the only way
to change desired state; Kyverno admission control and least-privilege RBAC groundwork are in
place; the whole target state is scripted from an empty node through `scripts/bootstrap.sh` and
checked by `scripts/verify-state.sh`.

### Added
- Repository governance: secret scanning and push protection, branch protection on `main`/`dev`
  (required `repo-checks`, no force-push, no deletion), the `experiment/dev-state` branch with a
  no-force-push ruleset ([#41](https://github.com/koussayx8/nexus-platform/pull/41), ADR-012,
  ADR-013).
- `repo-checks`: GitLeaks over the PR range and the tree, `kustomize build` outside parked paths,
  `kubeconform -strict` against pinned schemas, checksum-verified tools, SHA-pinned actions
  ([#41](https://github.com/koussayx8/nexus-platform/pull/41), ADR-012).
- `ci.yml`: lint and tests on pull requests to `dev`; build, push and Cosign keyless signing on
  `main` only ([#41](https://github.com/koussayx8/nexus-platform/pull/41)).
- Render check: every Application rendered as ArgoCD would (`helm template` + `kustomize build`),
  checked against its AppProject, then `kubeconform -strict`
  ([#45](https://github.com/koussayx8/nexus-platform/pull/45), ADR-012).
- `platform` Application: namespaces with autonomy levels, the AppProject, and the bootstrap-only
  root Application `platform/argocd/root.yaml`
  ([#46](https://github.com/koussayx8/nexus-platform/pull/46), ADR-014, ADR-018).
- `kyverno` Application, chart 3.8.0 / v1.18.0, reports controller and report features disabled
  ([#47](https://github.com/koussayx8/nexus-platform/pull/47), ADR-015).
- `observability` Application, multi-source: kube-prometheus-stack 86.2.2, Grafana admin from a
  Secret (not a literal in Git), persistent Prometheus (15d / 9GB, 10Gi `local-path`)
  ([#48](https://github.com/koussayx8/nexus-platform/pull/48), ADR-016).
- `sample-api-dev` / `sample-api-prod` Applications: `overlays/{dev,prod}`, 2 replicas, PDB
  `maxUnavailable: 1`, a cosign-verified pinned digest
  ([#49](https://github.com/koussayx8/nexus-platform/pull/49), ADR-017).
- `scripts/lib/readonly.sh`: shared read-only helpers (`k`, `h`, `g`, `gh_ro`, `redact`,
  `leak_check`) restricted to non-mutating subcommands; `capture-state.sh --backup`
  ([#51](https://github.com/koussayx8/nexus-platform/pull/51)).
- `scripts/verify-state.sh`: the M0 target-state checker — Applications Synced/Healthy, exactly one
  default Grafana datasource, no Loki/Crossplane/`sample-db`, sample-api digest and `/metrics`,
  namespace autonomy levels (ADR-018), pod readiness, an audit-log probe (§14), Kill Switch state
  ([#52](https://github.com/koussayx8/nexus-platform/pull/52)).
- `scripts/bootstrap.sh`: scripted bootstrap from an empty k3s node to the full M0 target state,
  `--plan` dry-run mode, the §14 audit policy with a non-sudo-readable, rotation-safe audit log
  (`640 root:adm`, pre-created before k3s's first start), a merge-order guard against
  `origin/main`/`origin/experiment/dev-state`
  ([#53](https://github.com/koussayx8/nexus-platform/pull/53), ADR-019).
- `CHANGELOG.md` (this file).

### Changed
- `bootstrap.sh`: configurable ArgoCD-rollout and Application-wait timeouts
  (`NEXUS_ARGOCD_ROLLOUT_TIMEOUT`, `NEXUS_WAIT_TIMEOUT_*`), richer rollout-timeout diagnostics
  ([#58](https://github.com/koussayx8/nexus-platform/pull/58)).

### Fixed
- `bootstrap.sh`: every real command failure is fatal immediately, not deferred
  ([#55](https://github.com/koussayx8/nexus-platform/pull/55)).
- `repo-checks`: a push that creates a new branch now scans from `merge-base(origin/main)` instead
  of falling back to `-1 <sha>` ([#44](https://github.com/koussayx8/nexus-platform/pull/44)).
- ArgoCD `kyverno` Application stuck permanently `OutOfSync`: `ServerSideDiff=true`
  ([#59](https://github.com/koussayx8/nexus-platform/pull/59), ADR-015 addendum).
- ArgoCD `root` Application stuck permanently `OutOfSync` for the same underlying reason
  (`kyverno`'s `pre-delete` hook finalizers never declared in `root`'s own comparison):
  `ServerSideDiff=true` ([#62](https://github.com/koussayx8/nexus-platform/pull/62), ADR-014
  addendum).

### Removed
- The `loki` and `crossplane-infrastructure` Applications, and both superseded ClusterPolicies
  ([#46](https://github.com/koussayx8/nexus-platform/pull/46),
  [#47](https://github.com/koussayx8/nexus-platform/pull/47), ADR-014).
- The literal Grafana admin password from Git, replaced by a Kubernetes Secret
  ([#48](https://github.com/koussayx8/nexus-platform/pull/48)).

### M0-exit evidence
- A from-empty rebuild (`scripts/bootstrap.sh`, no `--plan`, no prior partial state) reached
  `verify-state.sh` 8/8 on 2026-09-26 — the first attempt to do so from a genuine empty state; two
  earlier attempts surfaced the timeout and `kyverno`/`root` `ServerSideDiff` bugs fixed above.
- The ADR-019 audit-log rotation test ran against that same cluster: both a restart-triggered and a
  size-triggered log rotation preserved `640 root:adm`, with non-sudo read access confirmed on every
  resulting file.

[Unreleased]: https://github.com/koussayx8/nexus-platform/compare/v0.2.0...main
[0.2.0]: https://github.com/koussayx8/nexus-platform/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/koussayx8/nexus-platform/releases/tag/v0.1.0
