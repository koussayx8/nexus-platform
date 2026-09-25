# TASKS — NEXUS

**Milestone:** M0 — verify, stabilise, govern (spec §25, §27).
**Current phase:** M0-2 Triage decisions applied — done on `chore/m0-triage`, waiting at its gate.
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
- [ ] Record the credential status of the removed files in ADR-010 (your answer).
- [ ] Repository cleanup list (pasted in the M0-2 instructions) — **not started, awaiting your confirmation**:
  - tag `archive/pre-m0-cleanup`;
  - ADR Status lines;
  - ADRs "Architecture freeze v1.0" and "Repository cleanup";
  - `PARKED.md` for Backstage and Crossplane;
  - `git rm` of `infra/terraform`, `platform/vault` and `ai/`.

  Facts for that decision:
  - there is no `dependabot.yml`; the 21 open Backstage PRs come from repository security updates;
  - no Ansible is tracked;
  - the only tracked OpenCode file is `docs/NEXUS_OpenCode_Master_Brief_v4.md`, a document.
- [ ] Push the branch and open a PR to `main` (needs your approval). Merging is a no-op for the live cluster: no tracked kustomization or chart changes — **done when** `origin/main` contains the branch and `git status` is clean.
- **GATE M0-2**

## M0-3 Git governance

`gh` is installed and authenticated (`read:packages`, `repo`, `workflow`).

1. [ ] Create `dev` from `main` (the integration branch; feature branches merge into it by PR) — **done when** `git ls-remote origin refs/heads/dev` returns a SHA.
2. [ ] Add a **required-check workflow** on every PR to `main` and `dev`: GitLeaks over the repository, `kustomize build` of every kustomization, and `kubeconform`. Path-filtered workflows cannot be required checks — **done when** one PR to each branch shows it green.
3. [ ] The sample-api workflow runs lint and tests on PRs to `dev` as well; build, push and sign run only from `main` — **done when** `ci.yml` triggers match that.
4. [ ] Protect `main` and `dev`: PR required, the required-check workflow required, no force-push, no deletion — **done when** `gh api repos/koussayx8/nexus-platform/branches/{main,dev}/protection` shows those settings.
5. [ ] Create `experiment/dev-state` from `main`. It is **never force-pushed**. The runner restores the baseline tree with a forward commit, `chore(exp): reset to baseline`, skipped when there is no diff. Before the rebuild, `main` is merged into it, and the baseline tag is set only after `verify-state.sh` passes (M0-5). §13 says the branch is created from a tagged baseline; here the tag comes later — **done when** the branch exists on origin and the reset rule is in an ADR.
6. [ ] ADRs for the required-check design and the experiment-branch reset rule.
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
- CI per §19: Trivy scans the pushed digest, not `:latest`; add a digest-bump PR step.
- Find out why GitLeaks let four Secret-bearing files through.
- Dependabot: stop npm updates under `platform/backstage` while it is frozen, and close the 21 open PRs. There is no `dependabot.yml`; the PRs come from repository security updates.
- Decide the fate of `ai/`, `infra/terraform/doks`, `platform/vault/README.md` and the OpenCode-era docs (`docs/NEXUS_OpenCode_Master_Brief_v4.md`, `docs/NEXUS_STATUS.md`, `docs/CUT_LIST.md`). This depends on the cleanup list above.
- The stash `m0-2: dropped dashboard change` can be dropped once M0-4 rebuilds the dashboard. `git stash drop` is denied to agents, so you drop it.
