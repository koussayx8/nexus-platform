# TASKS — NEXUS

**Milestone:** M0 — verify, stabilise, govern (spec §25, §27).
**Current phase:** M0-3 Git governance — done, waiting at its gate.
**Rules:** `CLAUDE.md`. **Evidence:** `docs/CURRENT_STATE.md` (M0-1 snapshot `docs/state/20260925T064759Z/`).

**Strategy — converge in Git, then rebuild.** The cluster holds no persistent data (no PV, no PVC),
and M0-5 rebuilds it anyway. So M0-4 changes Git only: Git describes the complete target state,
validated offline with `kustomize build`, `helm template` with the values files, and `kubeconform`.
Nothing is removed from the live cluster by hand: no finalizer edits, no `helm uninstall`, no
Application cascades. The live cluster stays untouched as the "before" reference. In M0-5, after a
final capture, you run the k3s uninstall and `bootstrap.sh` (both need sudo). **M0 is done when
`verify-state.sh` exits 0.**

Each phase ends at a gate: stop, report in the CLAUDE.md format, and wait. Every decision is
recorded as an ADR in `docs/adr/` when its phase lands.

**Standing rule — `main` is never left red.** A failing workflow on `main` is fixed before the next
pull request merges.

## M0-1 Capture and triage — done

- [x] Safety net verified — tag `backup/pre-m0` (`8fc5d26`) local and on origin; `~/nexus-backup/` complete.
- [x] `scripts/capture-state.sh`, snapshot `20260925T064759Z` (56 checks, exit 0, leak check clean).
- [x] `docs/CURRENT_STATE.md`, this file, and the triage proposals.

## M0-2 Triage decisions applied — branch `chore/m0-triage`

- [x] Commit the spec, `CLAUDE.md`, `.claude/settings.json`, `scripts/capture-state.sh` and `docs/state/.gitignore`.
- [x] `AGENTS.md` becomes one line pointing to `CLAUDE.md`.
- [x] Ignore `*:Zone.Identifier` and delete the one copy.
- [x] Narrow A2 in the capture script: a file that only names `kind: Secret` is readable.
- [x] Commit the alert, kube-state-metrics key and ServiceMonitor fixes, plus `platform/observability/config/kustomization.yaml`.
- [x] Drop the dashboard change (`git restore` is denied by `.claude/settings.json`, so it is parked as a stash and kept in the backup), `observability-config.yaml` and `.omo/`.
- [x] `git rm` `digitalocean-creds.yaml` and `postgresql-manual.yaml`. No history rewrite.
- [x] Redact every `data:`/`stringData:` value in ADR-006 and ADR-007 by script, with no value printed — **done when** a verify pass finds 0 unredacted values (done).
- [x] `git mv docs/ADR-*.md docs/adr/` and fix the README links; ADR-010 records these decisions.
- [x] Report on Grafana `adminPassword`: a 5-character **literal** in `observability.yaml:76` and `kube-prometheus-stack-values.yaml:84`, since `103676e`.
- [x] Record the credential status in ADR-010: the DigitalOcean token and the `postgresql-manual.yaml` credentials are dead; the Grafana password is exposed, not reused, and replaced at the rebuild.
- [x] Confirm read-only that Grafana is reachable only from this machine: `ClusterIP`; no Ingress, IngressRoute or Gateway route.
- [x] Tag `cbe9f8e` as `archive/pre-m0-cleanup` and push the tag.
- [x] Archive with `git rm`, one commit each: `infra/terraform`, `platform/vault`, `ai/`, the OpenCode brief, `platform/backstage`. Node artefacts are git-ignored; about 1.9 GB of Backstage build output remains on disk, untracked.
- [x] Park `platform/crossplane` with `PARKED.md`; nothing under `k8s/` changes.
- [x] ADR-011 "Repository cleanup" lists every archived and parked path with its restore command.
- [x] No Ansible is tracked, so there is nothing to do there.
- [ ] Push `chore/m0-triage`, open a PR to `main`, and merge with a merge commit, not a squash — **done when** `origin/main` contains every branch commit and the PR diff touches none of `apps/sample-api/k8s`, `platform/crossplane/k8s`, `platform/argocd`.
- [ ] Close the Backstage Dependabot PRs still open after the merge, each with a comment pointing to ADR-011 — **done when** `gh pr list --state open` shows none under `platform/backstage`.
- **GATE M0-2** — approved; the last two items run with the push.

**Until the M0-5 rebuild, nothing merged to `main` may change a path a live Application tracks:
`apps/sample-api/k8s`, `platform/crossplane/k8s`, `platform/argocd`.**

## M0-3 Git governance — done

Order: the lint PR, then the governance PR, then protection and the experiment branch.
The governance PR changes only `.github/`, `docs/adr/` and `TASKS.md`, which no live Application tracks.

1. [x] **Secret scanning and push protection** are both enabled (`gh api repos/koussayx8/nexus-platform --jq .security_and_analysis`).
2. [x] **Auto-delete head branches** enabled; `chore/m0-triage` deleted after its merge.
3. [x] **Lint PR** [#40](https://github.com/koussayx8/nexus-platform/pull/40), `fix/sample-api-lint`: `ruff==0.16.9` pinned in `apps/sample-api/requirements-dev.txt`, which CI installs; imports sorted; executable bit cleared on `main.py` and `test_main.py`. Merged as `311ad81`. `main` run 36136557684 is **green**, and build, push and Cosign signing succeeded.
   - Image for M0-4: `ghcr.io/koussayx8/nexus-platform/sample-api@sha256:45e7a88c950a40fd6ce37a6544d95bc435c76a6aa5af2eefcc9463363150d57c`.
4. [x] **`repo-checks`** (`.github/workflows/repo-checks.yml` + `.github/scripts/repo-checks.sh`):
   - GitLeaks over the PR range and the committed tree, never the full history;
   - `kustomize build` outside parked paths;
   - `kubeconform -strict` for Kubernetes 1.34.6 with pinned core and CRD schemas;
   - tools checksum-verified, actions pinned by SHA.

   Validated locally, negative controls included (ADR-012). **Done when** its own PR shows `repo-checks` green.
5. [x] **`ci.yml`** runs lint and tests on pull requests to `dev`; build, push and sign stay `main`-only.
6. [x] **ADR-012** (required checks) and **ADR-013** (experiment branch).
7. [x] Governance PR [#41](https://github.com/koussayx8/nexus-platform/pull/41) merged as `a12c202`, with `repo-checks` green on the PR. On `main`, `repo-checks` (run 36137823639) and `ci.yml` (run 36137823324) are both green. That `ci.yml` run signed one more image, which M0-4 does not use.
8. [x] **`dev`** created from `main` at `a12c202`; its `repo-checks` push run is green.
9. [x] **`main` and `dev` protected**. Read back from `gh api …/branches/{main,dev}/protection`:
   - PR required, 0 approvals;
   - required check `repo-checks` (not strict);
   - `enforce_admins: true`;
   - no force-push, no deletion;
   - no linear-history requirement.

   Read back on both: `checks=[repo-checks] strict=false enforce_admins=true pr_required=true approvals=0 force_push=false deletions=false linear=false`.
10. [x] **`experiment/dev-state`** created from `main` at `a12c202`. Ruleset `experiment-dev-state-no-force-push` (id 23998158): `enforcement=active`, rules `[non_fast_forward, deletion]`, `bypass_actors=[]`, `current_user_can_bypass=never`. Direct pushes stay allowed.
11. [x] Outcome recorded in `TASKS.md` through this pull request, the first merged under protection.
- **GATE M0-3**

## M0-4 Git convergence — Git only, validated offline, never applied to the live cluster

1. [ ] **Root Application** over `platform/argocd/applications/`. It is written here and applied **only by `bootstrap.sh` on the rebuilt cluster**, never to the current one, where its first sync would prune and cascade.
2. [ ] **`platform`**: namespaces with their levels and the AppProject.
   - Levels: `nexus-prod` `"1"` and `nexus-data` `"0"` on `main`; `nexus-dev` `"0"` on `experiment/dev-state`, raised to `"3"` by commit in M2.
   - `nexus-system`, `nexus-reasoner` and `nexus-load` also exist.
   - AppProject `nexus` destinations are the §3 namespaces; drop `nexus-apps`, `nexus-apps-prod` and `crossplane-system`.
3. [ ] **`kyverno`**: chart 3.8.0 / v1.18.0 with `reportsController` disabled, because nothing consumes policy reports (ADR). Remove both ClusterPolicies from Git, since they implement the superseded workload-annotation design. K1–K6 arrive in M3.
4. [ ] **`observability`** (keeps its name), multi-source:
   - the chart, the values file in Git and `platform/observability/config`, with **no inline values**;
   - the Loki datasource removed; Grafana admin from an existing Secret created by bootstrap, with the literal `adminPassword` removed from Git;
   - Prometheus on a `local-path` PVC with 15-day retention;
   - the dashboard with a title and namespace-agnostic queries.
5. [ ] **`sample-api-dev`** (`experiment/dev-state`) and **`sample-api-prod`** (`main`), with overlays for `nexus-dev` and `nexus-prod`:
   - 2 replicas, a PDB with `maxUnavailable: 1`, `automountServiceAccountToken: false`;
   - `ignoreDifferences` on `/spec/replicas` plus `RespectIgnoreDifferences=true`;
   - the digest pinned **in the overlays**: `sha256:45e7a88c950a40fd6ce37a6544d95bc435c76a6aa5af2eefcc9463363150d57c`, the signed image from green `main` run 36136557684 (`311ad81`). Record the `cosign verify` output when pinning it.
6. [ ] **No** `loki` and **no** `crossplane-infrastructure` Application. Crossplane manifests stay parked in Git, referenced by no Application (ADR). Backstage is not deployed until the final steps. `dependency-db` arrives in M1 with `/items`, its first user.
7. [ ] Settle and record:
   - Is the ghcr package public? If not, bootstrap creates a pull secret.
   - Is the repository public? **Yes, observed in M0-2**, so no ArgoCD repository credentials are needed.
   - Does any Ingress use Traefik? If not, the k3s install disables `traefik` and `servicelb`.
8. [ ] Offline validation — **done when** `kustomize build` of every kustomization, `helm template` of each chart with its values file, and `kubeconform` over all output pass. A CI job runs the same checks.
9. [ ] ADRs: converge-then-rebuild, the Kyverno reports controller, Crossplane parked, the multi-source observability Application, autonomy levels.
- **GATE M0-4**

## M0-5 Bootstrap, rebuild, verify

1. [ ] Move `k`, `h`, `g`, `redact` and `check` into `scripts/lib/readonly.sh`. Add a `gh` wrapper that allows only `run list` and GET `api` calls. `capture-state.sh` sources the library.
2. [ ] **`scripts/verify-state.sh`** is built on that library. Its M0 checks:
   - every Application Synced/Healthy;
   - exactly one default Grafana datasource;
   - no Loki, Crossplane or `sample-db`;
   - the sample-api digest equals Git, and `/metrics` returns 200 with the request counter and the latency histogram;
   - namespaces and levels as declared;
   - no failing pods;
   - the audit log growing;
   - the Kill Switch `active`.

   It writes `CURRENT_STATE.md` and exits non-zero on any failure.
3. [ ] **`scripts/bootstrap.sh`**, with every sudo step marked (you run it):
   - k3s install with the §14 audit policy and audit-log flags;
   - ArgoCD pinned to v3.3.8;
   - runtime Secrets from `~/.nexus/keys.env`, including the Grafana admin;
   - the runtime ConfigMaps `nexus-killswitch` (`active`) and `nexus-operator-config`;
   - the root Application, then a wait for Synced/Healthy;
   - then `verify-state.sh`.
4. [ ] Final capture of the old cluster, then merge `main` into `experiment/dev-state`. You run the k3s uninstall and `bootstrap.sh`.
5. [ ] `verify-state.sh` exits 0 on the rebuilt cluster, then set the baseline tag on `main` — **done when** both hold. **M0 complete.**
6. [ ] ADR for bootstrap and the audit policy.
- **GATE M0-5**

## Later — out of scope for M0

- `verify-state.sh` gains checks per milestone: K1–K6 in Enforce, the Incident CRD and CEL, operator and Reasoner Ready, N1–N6.
- M1: sample-api fault hooks and `NEXUS_FAULTS_ENABLED`, `/items` and `dependency-db`, a readiness check that is local only.
- M3: WSL2 changes the node IP on restart, so NetworkPolicies template it at bootstrap and never hardcode `172.19.233.100`. N1–N6.
- M3: CODEOWNERS on `platform/policies/`, `platform/rbac/` and the Action Catalogue (§19), once those paths exist.
- CI per §19: Trivy scans the pushed digest, not `:latest`; add a digest-bump PR step.
- CI per §19: "Dependabot opens weekly pull requests into `dev`". That needs a `dependabot.yml` with `target-branch: dev`. Today only security updates run, against `main`.
- Spec v1.1 (ADR plus version bump): the `experiment/dev-state` sequencing and forward-commit reset (§13, ADR-013), the branch ruleset (§19, ADR-013), and the unfiltered required check (§19, ADR-012).
- `repo-checks`: on a push that creates a branch, the range falls back to `-1 <sha>`. For a merge commit that scans 0 commits (seen when `dev` was created); the tree scan still ran. Make that path scan `origin/main..<sha>`, or accept it.
- Docs pass: `README.md` still describes Backstage, Crossplane and the old autonomy ladder. `docs/NEXUS_STATUS.md` and `docs/CUT_LIST.md` are OpenCode-era; decide whether to rewrite or archive them.
- Local only: about 1.9 GB of ignored Backstage build output remains in `platform/backstage/` (`node_modules`, `dist`, Yarn state). Delete it whenever you like.
- The stash `m0-2: dropped dashboard change` can be dropped once M0-4 rebuilds the dashboard. `git stash drop` is denied to agents, so you drop it.
