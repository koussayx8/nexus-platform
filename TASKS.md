# TASKS — NEXUS

**Milestone:** M0 — verify, stabilise, govern (spec §25, §27).
**Current phase:** M0-3 Git governance — planned below, waiting for approval. M0-2 is done.
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

## M0-3 Git governance — plan, waiting for approval

`gh` is authenticated (`repo`, `workflow`, `read:packages`). Work happens on branch
`ci/m0-3-governance` from the updated `main`, one commit per concern, merged by PR with a merge
commit. The only paths it changes are `.github/`, `docs/adr/` and `TASKS.md`, none of which a live
Application tracks.

Facts behind the plan (observed in M0-2):
- Secret scanning and push protection are **already enabled**.
- All 10 kustomizations build offline.
- The existing GitLeaks job sits in the path-filtered `ci.yml`, so it never ran on `719ade0`, which touched only `platform/crossplane/**`. This is the likely answer to the Later item on GitLeaks.
- `gitleaks`, `kubeconform` and a standalone `kustomize` are not installed locally.

1. [x] **Secret scanning and push protection** — **done when** `gh api repos/koussayx8/nexus-platform --jq .security_and_analysis` shows both `enabled`. Done 2026-09-25: both enabled, so nothing for you to change. Validity checks and non-provider patterns are disabled; optional.
2. [ ] **Required-check workflow** `.github/workflows/repo-checks.yml`:
   - triggers on `pull_request` to `main` and `dev`, with no path filter, and on `push` to `main` and `dev`;
   - a single job `repo-checks`, the context the protection rules require, with `contents: read` and actions pinned by commit SHA;
   - **GitLeaks CLI**, pinned version, checksum verified: `gitleaks git --redact` over the PR commit range, plus `gitleaks dir --redact .` over the tree. Findings in dead history belong in a reviewed `.gitleaksignore` by fingerprint, never by path;
   - **`kustomize build`** of every directory with a `kustomization.yaml`;
   - **`kubeconform -strict -summary`** on the output, for Kubernetes 1.34 plus a CRD schema catalog (monitoring.coreos.com today);
   - validated locally first: tools downloaded into the scratchpad, never installed system-wide.

   **Done when** the workflow's own PR shows `repo-checks` green.
3. [ ] **`ci.yml`**: `pull_request.branches: [main, dev]`. `push` stays `main` only, and `build-and-push` and `sign` keep `if: push && main` — **done when** the diff shows only the trigger change and the PR runs lint and tests.
4. [ ] **ADR-012 — required checks.** One unfiltered required workflow beside the path-filtered component workflows. This departs from §19's path-filtered-only design, because path-filtered workflows cannot be required. It goes into spec v1.1.
5. [ ] **ADR-013 — experiment branch.**
   - `experiment/dev-state` is created from `main` and never force-pushed;
   - the runner resets with the forward commit `chore(exp): reset to baseline`, skipped when there is no diff;
   - `main` is merged into it before the rebuild, and the baseline tag is set after `verify-state.sh` passes (M0-5);
   - §13 sequencing goes into spec v1.1.
6. [ ] Merge `ci/m0-3-governance` into `main` with a merge commit.
7. [ ] **Create `dev`** from the new `main` (`git push origin main:refs/heads/dev`) — **done when** `git ls-remote origin refs/heads/dev` equals `main`.
8. [ ] **Protect `main` and `dev`** (`gh api -X PUT …/branches/{main,dev}/protection`):
   - a PR is required with 0 approvals (§19: self-merge after green checks is allowed);
   - the required check is `repo-checks`, not strict;
   - no force-push, no deletion;
   - `enforce_admins: true`, so no direct pushes, the owner included (Q1);
   - linear history is **not** required, so merge commits keep one commit per decision.

   **Done when** a GET on both protection endpoints shows these settings.
9. [ ] **Create `experiment/dev-state`** from `main` — **done when** `git ls-remote origin refs/heads/experiment/dev-state` equals `main`. §19 keeps it **unprotected**; "never force-pushed" is a runner rule (Q2).
10. [ ] Update `TASKS.md` with the M0-3 outcome.
- **GATE M0-3**

Open points for M0-3:
- **Q1.** Is `enforce_admins: true` acceptable? It blocks your own direct pushes too. An emergency needs an admin to lift it in the UI.
- **Q2.** Should `experiment/dev-state` stay unprotected as §19 says, or get a ruleset that blocks only force-push and deletion? A ruleset would be a §19 deviation, and needs an ADR.

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
   - the digest pinned **in the overlays**: the latest successful `main` build that includes `prometheus-fastapi-instrumentator`, found with `gh`.
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
- Confirm why GitLeaks let four Secret-bearing files through. Likely cause: the GitLeaks job is inside the path-filtered `ci.yml`, and `719ade0` touched only `platform/crossplane/**`. ADR-012 closes the gap.
- Spec v1.1 (ADR plus version bump): the `experiment/dev-state` sequencing (§13) and the unfiltered required-check workflow (§19).
- Docs pass: `README.md` still describes Backstage, Crossplane and the old autonomy ladder. `docs/NEXUS_STATUS.md` and `docs/CUT_LIST.md` are OpenCode-era; decide whether to rewrite or archive them.
- Local only: about 1.9 GB of ignored Backstage build output remains in `platform/backstage/` (`node_modules`, `dist`, Yarn state). Delete it whenever you like.
- The stash `m0-2: dropped dashboard change` can be dropped once M0-4 rebuilds the dashboard. `git stash drop` is denied to agents, so you drop it.
