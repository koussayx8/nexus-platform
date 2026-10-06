# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses milestone tags (`v0.N.0`, one per milestone exit).

## [Unreleased]

M1b — detection and the operator skeleton — is in progress. Its entries are written at the M1b exit,
which is released as `0.3.0`. Progress is tracked in [`TASKS.md`](TASKS.md).

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
