# NEXUS — Current State

Assembled on 2026-09-25 from snapshot `docs/state/20260925T064759Z/`, produced by
`scripts/capture-state.sh` (56 checks, all exit 0, leak self-check clean). Every row cites
its evidence file, which is relative to that snapshot directory. Snapshots are git-ignored
(`docs/state/.gitignore`). From M0-5, `verify-state.sh` generates this file.

The safety-net facts come from the Step 1 commands of this session and are not in the snapshot.
Amendment A2 applies throughout: files containing `kind: Secret` are never shown, so
`platform/argocd/applications/observability.yaml` and `platform/argocd/projects/nexus.yaml`
were not read.

## 1. State by area

### Environment

| Item | Observed | Evidence |
| --- | --- | --- |
| Host | WSL2 kernel 6.18.33.2-microsoft-standard-WSL2, Ubuntu 24.04.3 LTS | `00-environment.txt` |
| Repository | `/home/azure/nexus-platform`, not under `/mnt/c`; `.venv` Python 3.12.3 | `00-environment.txt` |
| Tools present | kubectl 1.34.1, helm, git 2.43.0, curl, jq, python3 | `00-environment.txt` |
| Tools absent | `gh`, `argocd`, `shellcheck`, `kyverno` CLI, `k8sgpt` | `00-environment.txt` |
| Identity | kubeconfig default path; `auth can-i '*' '*'` = yes (cluster admin) | `00-environment.txt` |

### Git

| Item | Observed | Evidence |
| --- | --- | --- |
| Branch · HEAD | `main` · `cbe9f8e`, equal to local and remote `origin/main`; 0 ahead, 0 behind | `01a-git-refs.txt` |
| Remote | `origin` https://github.com/koussayx8/nexus-platform.git | `01a-git-refs.txt` |
| Remote branches | `main`, plus 21 on origin under `dependabot/npm_and_yarn/platform/backstage/*` (32 local tracking refs). No `dev`, no `experiment/dev-state` | `01a-git-refs.txt` |
| Tags | `backup/pre-m0` → `8fc5d26` (stash commit "WIP on main", parents HEAD and the index), local and origin | `01a-git-refs.txt`, Step 1 |
| Backup files | `~/nexus-backup/`: `status.txt` 366 B, `unstaged.patch` 4409 B, `staged.patch` 0 B (nothing staged), `worktree-2026-09-25.tgz` 329 MB, 222,967 entries | Step 1 |
| Last 20 commits | The four latest are "ci: trigger rebuild with prometheus-fastapi-instrumentator" (2026-06-28) | `01b-git-log.txt` |
| Uncommitted | 5 modified files (12+/10−), nothing staged | `01c-git-status.txt`, `01d-git-diff.txt` |
| Untracked | 24 files: `.omo/` 16, `.claude/` 1, CLAUDE.md, spec + Zone.Identifier, 2 observability-config files, and this session's `scripts/capture-state.sh` and `docs/state/.gitignore` | `01c-git-status.txt`, `01e-git-untracked.txt` |
| CI | GitHub Actions `.github/workflows/ci.yml` only; no `.gitlab-ci.yml`. Triggers: push and PR to `main` on `apps/sample-api/**`. Jobs: lint → test, Semgrep, GitLeaks → build and push to ghcr (tags sha, branch, `latest`) → Trivy on `:latest` → Cosign sign by digest. No step writes the digest back to Git | `01f-git-ci.txt` |
| CI run history · branch protection | UNKNOWN (`gh` absent) | `01f-git-ci.txt` |
| Secret-bearing files | Tracked: `platform/crossplane/secrets/digitalocean-creds.yaml` (under `secrets/`, never opened, added `719ade0` on 2026-06-09, even though `.gitignore:24` says `secrets/`). `apps/sample-api/infrastructure/postgresql-manual.yaml`, `docs/ADR-006-*.md` and `docs/ADR-007-*.md` have `kind: Secret` **with** data/stringData. `platform/argocd/applications/observability.yaml` and `platform/argocd/projects/nexus.yaml` have `kind: Secret` with no data | `01g-git-secret-files.txt` |
| Layout vs §25 | Present: `apps/sample-api`, `platform/argocd`, `scripts`. Absent: everything else, including `docs/adr`, `overlays/`, `platform/namespaces`, `operator/`, `reasoner/`, `Makefile`. Legacy top-level: `ai/`, `infra/`, `platform/{backstage,crossplane,kyverno,observability,vault}` | `01h-git-layout.txt` |

### k3s

| Item | Observed | Evidence |
| --- | --- | --- |
| Version | Server v1.34.6+k3s1, containerd 2.2.2; one node `koussay`, control-plane, age 151 d | `02a-k8s-version.txt` |
| Capacity | 12 CPU, 16,298,636 Ki memory, 110 pods; swap 4 GiB | `02b-node-resources.txt` |
| Load now | 0.64 cores and 5.14 GiB used (metrics API); requests 1535m (12 %) and 2364 Mi (14 %); limits 2500m and 5546 Mi | `02b-node-resources.txt` |
| Server flags | node-args `["server"]`; unit `ExecStart=/usr/local/bin/k3s server` with no flags; `/etc/rancher/k3s/config.yaml` absent | `02c-k3s-server-flags.txt` |
| Audit flags | **None** in any readable source. `k3s.service.env` and `server/logs/` need sudo (see `SUDO-REQUIRED.txt`) | `02c-k3s-server-flags.txt` |
| Restarts | Every pod restarted about 68 min before capture (restart counts 11–68); inferred to be a host or WSL restart | `03e-pods-unhealthy.txt` |

### Cluster-wide

| Item | Observed | Evidence |
| --- | --- | --- |
| Namespaces | argocd, crossplane-system, default, kube-node-lease, kube-public, kube-system, kyverno, monitoring, nexus-apps. **No NEXUS §3 namespace exists**, and no namespace has `nexus.io/autonomy-level` | `03a-namespaces.txt` |
| CRDs | 162 in total: 94 Crossplane/DigitalOcean/`platform.nexus.io`, 18 Kyverno (+2 reports, 2 wgpolicy), 10 monitoring.coreos.com, 23 Traefik, 6 Gateway API, 3 ArgoCD, 4 k3s/helm.cattle. No `incidents.nexus.io` | `03b-crds.txt` |
| Helm releases | `crossplane` 2.3.1 (crossplane-system), `kyverno` chart 3.8.0 / app v1.18.0, `loki` loki-stack 2.10.3 (rev 3, 2026-06-26), `observability` kube-prometheus-stack 86.2.2 (rev 2, 2026-06-26), `traefik` + `traefik-crd` (k3s bundled) | `03c-helm-releases.txt` |
| Pods not Ready | One: `kyverno/kyverno-reports-controller` in CrashLoopBackOff, 62 restarts (cache-sync and lease timeouts) | `03e-pods-unhealthy.txt`, `03f-logs-kyverno-*.txt` |
| Warning events | Reports-controller back-off; XR `sample-api-database-zr74q` fails to compose; `function-apply:v0.1.0` cannot be resolved | `03g-events-warning.txt` |
| APIServices | All Available | `03h-apiservices.txt` |

### ArgoCD

| Item | Observed | Evidence |
| --- | --- | --- |
| Version · tracking | v3.3.8; `resourceTrackingMethod: annotation`; no ApplicationSets | `04d-argocd-config.txt`, `04a-argocd-apps.txt` |
| Applications | 4, all Synced/Healthy, automated with selfHeal and prune, each with an OrphanedResourceWarning (see table below) | `04a-argocd-apps.txt` |
| Root Application | None. The four Applications are not reconciled from Git | `04a-argocd-apps.txt`, `04e-argocd-git-vs-cluster.txt` |
| Git vs cluster | `observability-config` exists only as an untracked file. `observability` shows as "cluster only" because A2 excluded it from the grep; it is at `origin/main:platform/argocd/applications/observability.yaml` | `04e-argocd-git-vs-cluster.txt` |
| AppProject `nexus` | Destinations `nexus-apps`, `nexus-apps-prod`, `crossplane-system`, `monitoring`; three source repos | `04c-argocd-projects.txt` |
| Not tracked by any Application | Kyverno, the Crossplane install, `sample-db`, the `sample-api-database` claim, the sample-api ServiceMonitor, alert and dashboard | `04b-argocd-app-resources.txt`, `03c-helm-releases.txt` |

| Application | Source · path/chart · revision | Helm values | Tracks | Orphaned |
| --- | --- | --- | --- | --- |
| `crossplane-infrastructure` | repo · `platform/crossplane/k8s/overlays/dev` · `main` | n/a | 1 (Namespace crossplane-system) | 11 |
| `loki` | grafana charts · `loki-stack` · 2.10.3 | inline | 14 | 9 |
| `observability` | prometheus-community · `kube-prometheus-stack` · 86.2.2 | inline; `ServerSideApply=true`; 2 ignoreDifferences (webhook Secrets) | 98 | 9 |
| `sample-api` | repo · `apps/sample-api/k8s/overlays/dev` · `main` | n/a | 3 (Namespace, Service, Deployment in nexus-apps) | 3 |

### Grafana

| Item | Observed | Evidence |
| --- | --- | --- |
| Live datasources | One labelled ConfigMap `monitoring/observability-kube-prometh-grafana-datasource`: Prometheus (`isDefault: true`), Alertmanager (no isDefault), Loki (`isDefault: false`). **Exactly one default.** No datasource Secrets | `05a-grafana-datasource-cms.txt`, `05c-grafana-ds-secrets.txt`, `05f-grafana-isdefault.txt` |
| Workload | `observability-grafana` 1/1, Grafana 13.0.1-security-01, datasource sidecar label `grafana_datasource=1`; grafana container restarts=11, last termination Unknown | `05d-grafana-workload.txt` |
| Crash cause | "Only one datasource" is absent from the current and previous 200 log lines | `05e-grafana-logs.txt` |
| Other log errors | Every ~30 s: `sample-api-red.json` fails with "Dashboard title cannot be empty" | `05e-grafana-logs.txt` |
| Git side | `platform/observability/k8s/base/kube-prometheus-stack-values.yaml:98-104` adds Loki with `isDefault: false`. The inline values ArgoCD renders, in `observability.yaml`, are **not captured** (A2) | `05f-grafana-isdefault.txt`, `04a-argocd-apps.txt` |
| Helm user values | Same Loki `additionalDataSources`; `adminPassword` set (value redacted) | `03c-helm-releases.txt` |

### sample-api

| Item | Observed | Evidence |
| --- | --- | --- |
| Deployed | `nexus-apps` only: 1 replica (the overlay patches base 2 → 1). Image `ghcr.io/koussayx8/nexus-platform/sample-api@sha256:c693838d…c97bb` | `06a-sample-api-deployed.txt` |
| Running digest vs Git | Pod imageID `sha256:c693838d…` **MATCHES** the digest pinned in `base/deployment.yaml:36` at HEAD and at origin/main. The overlay has no image override | `06a-…`, `06b-sample-api-git-digest.txt` |
| `/metrics` | Through a temporary port-forward (svc 80 → 8000): **HTTP 404** `{"detail":"Not Found"}`. Port-forward stopped and confirmed gone | `06c-sample-api-metrics.txt` |
| Spec details | No env at all (no `NEXUS_FAULTS_ENABLED`); readiness `/ready`, liveness `/health`; `automountServiceAccountToken` unset | `09c-sample-api-env.txt` |

### Kyverno

| Item | Observed | Evidence |
| --- | --- | --- |
| Version | v1.18.0 (four controllers), chart kyverno-3.8.0, Helm release, not in ArgoCD | `07a-kyverno-install.txt`, `03c-…` |
| ClusterPolicies | `nexus-autonomy-level`: 2 validate rules, `validationFailureAction: Audit`, failurePolicy unset (default Fail). `mutate-autonomy-level-default`: 1 mutate rule. Both Ready | `07b-kyverno-clusterpolicies.txt` |
| Other policy kinds | None: no namespaced Policies and no `policies.kyverno.io` objects | `07c-kyverno-other-policies.txt` |
| Webhooks | Kyverno resource and policy webhooks `failurePolicy=Fail`, timeout 10 s | `07d-webhooks.txt` |
| Git | `platform/kyverno/policies/{autonomy-level,mutate-autonomy-default}.yaml`; no Application references them | `07e-kyverno-git.txt` |

### Crossplane and Loki

| Item | Observed | Evidence |
| --- | --- | --- |
| Crossplane install | Helm `crossplane` 2.3.1; 4 Deployments in crossplane-system (core, rbac-manager, `provider-upjet-digitalocean` v0.3.2, `function-patch-and-transform` v0.2.0); `function-apply` v0.1.0 not installed | `08c-…`, `08e-…` |
| Crossplane objects | XRD `xpostgresqlinstances.platform.nexus.io`; Compositions `-dev` and `-prod` (7 revisions); ProviderConfig `default` → Secret `digitalocean-creds`; claim `nexus-apps/sample-api-database` Ready=False; XR `sample-api-database-zr74q` Synced=False (ReconcileError) | `08e-…`, `09d-db-claims.txt` |
| Managed resources | **None** (`kubectl get managed`: no resources), so no DigitalOcean resource is tracked from this cluster | `08f-crossplane-managed.txt` |
| Crossplane footprint | 94 CRDs, 26 APIServices, 20 ClusterRoles, 5 ClusterRoleBindings, webhook `crossplane-no-usages` (Fail), 9 Secrets in crossplane-system (names only) | `08d-…`, `08g-…`, `08c-…` |
| Loki footprint | StatefulSet `loki` (grafana/loki 2.6.1), DaemonSet `loki-promtail` (3.5.1), 3 Services, 2 SAs, Role and RoleBinding, ClusterRole and Binding `loki-promtail`, ConfigMap `loki-loki-stack-test`, Secrets `loki`, `loki-promtail`. **No PVC**. Also the Loki datasource entry in Grafana | `08a-…`, `08b-loki-objects.txt` |
| Dual management | `loki` and `observability` are both ArgoCD Applications **and** Helm releases | `03c-…`, `04a-…` |

### Database

| Item | Observed | Evidence |
| --- | --- | --- |
| Today's "database" | Deployment `nexus-apps/sample-db`: `busybox:1.36`, command `echo "Mock PostgreSQL running on port 5432"; while true; do sleep 3600; done`, no volumes. Service `sample-db:5432` | `09a-db-workloads.txt`, `09b-db-specs.txt` |
| What it runs | Nothing: a sleep loop with no listener. No StatefulSet and no PVC anywhere | `09b-…`, `10b-storage.txt` |
| Git | `apps/sample-api/infrastructure/database.yaml` (Crossplane claim) and `postgresql-manual.yaml` (Secret-bearing, not read); neither is referenced by an Application | `09e-db-git.txt` |
| sample-api use | No database, postgres or DB_ references in `apps/sample-api` code or requirements; no env | `09e-db-git.txt`, `09c-…` |

### Network and storage

| Item | Observed | Evidence |
| --- | --- | --- |
| Pod CIDR | 10.42.0.0/16 (node podCIDR 10.42.0.0/24; 29 pod IPs in 10.42) | `10c-cidrs.txt` |
| Service CIDR | 10.43.0.0/16 (ServiceCIDR `kubernetes`); `kubernetes` 10.43.0.1, `kube-dns` 10.43.0.10 | `10c-cidrs.txt` |
| Node IP · CNI | 172.19.233.100 (WSL2) · flannel vxlan; no CIDR, flannel or `disable-network-policy` flag | `10c-cidrs.txt` |
| NetworkPolicies | 7, all in `argocd` (chart defaults). None in kyverno, monitoring, nexus-apps, crossplane-system or kube-system | `10a-networkpolicies.txt` |
| Storage | No PVC and no PV. StorageClass `local-path` (default, Delete) | `10b-storage.txt` |

## 2. Spec §27 — IMPLEMENTATION TO VERIFY

| Item | Status | Evidence, or the command that settles it |
| --- | --- | --- |
| Uncommitted work in the WSL2 tree | **VERIFIED** | Tag `backup/pre-m0` (stash `8fc5d26`) local and origin; 4 backup files, tgz non-empty (Step 1, `01a`) |
| ArgoCD Applications and sync state | **VERIFIED** (none missing vs `origin/main`; all Synced/Healthy) | `04a`, `04e`. Gaps: no root Application; `observability-config` untracked; OrphanedResourceWarning on all 4 |
| CI system in use | **VERIFIED**: GitHub Actions, no GitLab | `01f`. Run history UNKNOWN → `gh run list -L 10` |
| `sample-api` stack, metric names, deployed digest | Digest **VERIFIED** (running = Git `c693838d…`). `/metrics` **CONTRADICTED** (404). Metric names UNKNOWN | `06a`–`06c`. Settle after the M0-4 digest bump: `curl -s localhost:<pf>/metrics \| grep '^# TYPE'` |
| Grafana datasources | Live **VERIFIED**: exactly one default (Prometheus), no crash signature. Git declaration **UNKNOWN** | `05a`, `05e`, `05f`. Settle: approve reading `observability.yaml` (see Q3), then `git grep -n -E 'isDefault\|additionalDataSources' -- platform/argocd/applications/observability.yaml` |
| Existing CRDs and namespaces, including Crossplane's | **VERIFIED**: 162 CRDs (94 Crossplane-related), 9 namespaces | `03a`, `03b`, `08d`, `08e` |
| Node CPU and memory of the WSL2 VM | Capacity **VERIFIED** (12 CPU, ~15.5 GiB). Under Locust baseline UNKNOWN | `02b`. Settle in M1: ramp test with node metrics |
| Pod and service CIDRs, NetworkPolicy enforcement under WSL2 | CIDRs **VERIFIED** (10.42/16, 10.43/16; k3s defaults, as §18 assumes). Enforcement UNKNOWN | `10c`. Settle in M3: N1–N6 |
| k3s audit flags and CEL transition rules | Audit flags **CONTRADICTED**: absent from every readable source. CEL UNKNOWN | `02c`. Settle: `sudo cut -d= -f1 /etc/systemd/system/k3s.service.env`; `sudo ls -la /var/lib/rancher/k3s/server/logs/`. CEL: rejected spec patch in M1 |
| Kopf status-based persistence and standalone mode | UNKNOWN | M1 one-day spike |
| kube-state-metrics series and Alertmanager v2 API | UNKNOWN (KSM and Alertmanager pods Running) | `03d`. M1: queries against the live stack |
| Locust capacity and baseline load | UNKNOWN (no Locust, no `nexus-load`) | M1 ramp test |
| Kubernetes client exposes `Audit-Id` | UNKNOWN | M2 dry-run spike |
| Kyverno version and features (scale/eviction matching, lookups, context, time, failed lookup) | Version **VERIFIED** v1.18.0. Features UNKNOWN | `07a`. M3: `kyverno test` and the coverage matrix |
| Kyverno CLI with subresources and mocked context | UNKNOWN (CLI not installed) | `00`. M4 trial on K1–K4 |
| Groq model ids, JSON-schema output, reasoning, limits | UNKNOWN | M4 smoke test |
| K8sGPT backend options | UNKNOWN (not installed) | `00`. M4: `k8sgpt` backend listing |
| Fork-bot account and token scope | UNKNOWN | M6 |

## 3. Spec vs reality — reported, not resolved

| Spec | Reality | Evidence |
| --- | --- | --- |
| §3: 8 namespaces (`nexus-system`, `-reasoner`, `-dev`, `-prod`, `-data`, `-load`, …) | Only `nexus-apps` | `03a` |
| §3: Applications `platform`, `nexus`, `monitoring`, `sample-api-dev`, `sample-api-prod`, `dependency-db`, `backstage` | `crossplane-infrastructure`, `loki`, `observability`, `sample-api`; no root Application | `04a` |
| §3 / §25: `sample-api` has 2 replicas, a PDB, the faults flag and no SA token | 1 replica, no env, `automountServiceAccountToken` unset; PDB UNKNOWN (`kubectl get pdb -A`) | `06a`, `09c` |
| §3: `sample-api` exposes `/metrics` | 404 on the deployed digest | `06c` |
| §3: Dependency DB is PostgreSQL in `nexus-data` | busybox sleep loop `sample-db` in `nexus-apps`, outside GitOps | `09a`, `09b`, `04b` |
| §3: `/items` reads the Dependency DB | No DB code in `apps/sample-api` | `09e` |
| §12: level = namespace label `nexus.io/autonomy-level`, missing = L0 | No namespace label. Policies act on workload labels or annotations; a mutate policy adds a default | `03a`, `07b` |
| §14: k3s audit policy through API-server args | No audit flags | `02c` |
| §23: Loki REMOVED, Crossplane DEFERRED | Both installed and running | `08a`–`08g` |
| Rule 2 (Git is the only desired-state path) | Kyverno and Crossplane are Helm-only; `sample-db`, the claim, the ServiceMonitor, alert and dashboard are outside any Application; `loki` and `observability` are both Helm and ArgoCD | `03c`, `04b` |
| §25: `docs/adr/` | ADRs in `docs/ADR-00x-*.md` | `01h` |
| §19: path-filtered workflows per component | One workflow for sample-api; Trivy scans `:latest`, not the pushed digest | `01f` |
| `.gitignore` excludes `secrets/` | `platform/crossplane/secrets/digitalocean-creds.yaml` is tracked | `01g` |

## 4. Triage — uncommitted and untracked files (proposals only)

| Path | St. | What it changes (captured) | Fits frozen architecture? | Proposal |
| --- | --- | --- | --- | --- |
| `AGENTS.md` | M | Rewrites the Oh-My-OpenCode agent roster (models and roles) | No: another agent's instructions; CLAUDE.md now governs | **Drop** the change; retiring AGENTS.md goes to Later |
| `platform/observability/alerts/sample-api-error-rate.yaml` | M | Removes the `nexus.io/autonomy-level: "0"` alert label | Yes: §12 levels are namespace labels | **Commit on branch** |
| `platform/observability/dashboards/sample-api-red.json` | M | Pins three queries to `namespace="nexus-apps"` | No: `nexus-apps` goes away in M0-4; the dashboard already fails ("title cannot be empty") | **Drop**; M0-4 rebuilds the dashboard |
| `platform/observability/k8s/base/kube-prometheus-stack-values.yaml` | M | Moves `kubeStateMetrics:` → `kube-state-metrics:` (subchart key) | Yes (monitoring values), but ArgoCD renders the inline values, not this file | **Commit on branch**; M0-4 keeps one values source |
| `platform/observability/monitors/sample-api-servicemonitor.yaml` | M | Adds label `release: observability` | Yes (the Prometheus selector is UNKNOWN: `kubectl get prometheus -n monitoring -o jsonpath='{.items[0].spec.serviceMonitorSelector}'`) | **Commit on branch** |
| `.claude/settings.json` | ?? | Claude Code deny-list: mutating kubectl, helm, argocd, terraform, k3s-uninstall, sudo, `rm -rf` (30 of 53 lines captured) | Yes: enforces rules 2, 3 and 6 | **Commit on branch** |
| `CLAUDE.md` | ?? | Project instructions for Claude Code | Yes | **Commit on branch** |
| `docs/architecture/final-spec.md` | ?? | Frozen spec v1.0 (2414 lines) | Yes: the source of truth | **Commit on branch** |
| `docs/architecture/final-spec.md:Zone.Identifier` | ?? | 25-byte Windows ADS artefact | No | **Drop** + `.gitignore` pattern (decided) |
| `docs/state/.gitignore` | ?? | `*`, `!.gitignore` (this session) | Yes | **Commit on branch** (decided) |
| `scripts/capture-state.sh` | ?? | The read-only capture script (this session) | Yes: §25 `scripts/` | **Commit on branch** |
| `platform/argocd/applications/observability-config.yaml` | ?? | New Application `observability-config` → `platform/observability/config`, ns monitoring; not in the cluster | No: §3 has one `monitoring` Application | **Drop**; its content folds into `monitoring` in M0-4 |
| `platform/observability/config/kustomization.yaml` | ?? | Kustomization over `../monitors`, `../alerts`, `../dashboards` | Yes: this is `monitoring` content | **Commit on branch**; M0-4 wires it into `monitoring` |
| `.omo/boulder.json` | ?? | OpenCode session state (plan week5, "active") | No | **Drop** (decided) |
| `.omo/check-7-8.py`, `check2-prom.py`, `loki-status.py`, `probe-paths.py`, `verify-images.py` | ?? | Ad-hoc kubectl/port-forward probes | No | **Drop** (decided) |
| `.omo/extract-inline.py`, `validate-values.py` | ?? | Read inline Helm values from Application manifests | No | **Drop** (decided) |
| `.omo/ksm-patch.yaml` | ?? | Deployment patch adding an autonomy annotation to KSM | No: manual patch outside Git | **Drop** (decided) |
| `.omo/patch-helm-cluster-scoped.sh`, `patch-helm-ownership.sh` | ?? | `kubectl annotate/label` to make Helm adopt resources | No: mutating, outside Git | **Drop** (decided) |
| `.omo/commit-msg.txt`, `commit-msg2.txt` | ?? | Messages of commits `3b4e0df`, `b31812b` | No | **Drop** (decided) |
| `.omo/evidence/task-2-capacity.txt` | ?? | Capacity notes from 2026-06-09 | No | **Drop** (decided) |
| `.omo/plans/week4-crossplane.md`, `week5-observability.md` | ?? | Plans for Crossplane (84 KB) and Loki/observability (56 KB) | No: rule 8 | **Drop** (decided) |

`docs/CURRENT_STATE.md` and `TASKS.md` were created after the capture. They are committed with the M0-2 triage branch.
