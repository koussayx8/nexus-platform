> **M2 live plan, rev 1 (2026-11-03), drafted from `dev` `d404849`. It orders every step from `dev` to the M2 exit on
> WSL2. Scope and exit criterion: [`m2-outline.md`](m2-outline.md) (frozen). Current state lives in `TASKS.md`. One
> revision round at most.**

# M2 live plan: from `dev` to the M2 exit on WSL2, by 2026-11-20

## Owners

| Owner | Meaning |
|---|---|
| **owner** | Koussay runs it: `sudo`, every `kubectl` write (including `exec` and `--dry-run=server` requests), `helm`, `terraform`, Secrets and `~/.nexus`, GitHub secrets, fault injection |
| **agent** | A Claude session: offline work, PRs to `dev`, read-only cluster reads (`get`, `get --raw` GETs, `auth can-i`) |
| **agent, after typed approval** | Merges, forward-merges and tags, run by the agent only after the owner's typed approval naming the full SHA (CLAUDE.md rule 11) |

Every promotion `dev` → `main` is a PR from `dev`, merged with `gh pr merge <N> --merge --match-head-commit <SHA>`.
ArgoCD picks up a new `main` in 108–382 s (ADR-020 addenda). No Grafana dashboard or port-forward is open during
an injected run (standing rule). The rollback for any Git step is a revert PR to `dev`, then a promotion.

## State at 2026-11-03T12:57Z (read-only, this session)

| Item | Found |
|---|---|
| Node | `koussay` Ready, k3s v1.34.6+k3s1; WSL2 booted at 12:42Z |
| Applications | 8 of 8 `Synced`/`Healthy`; `main` ones at `86cea844`, `sample-api-dev` at `0ceb290` (`experiment/dev-state`) |
| Pod restarts | Every pod's last termination is `Unknown`/255 at 12:42:36Z, the host boot. Counts (11–38) track host reboots since 2026-09-28 |
| Locust | `running`, 160 users (80 per namespace), ~157 req/s total, `--autostart` |
| Incidents | 4, all terminal (2 `Recorded`/`level_observe` in `nexus-dev`, 2 `Escalated`/`evidence_error` in `nexus-prod`, 2026-10-16); 0 open |
| Kill Switch, levels | `active`; `nexus-dev` 0, `nexus-prod` 1, `nexus-data` 0 |
| `nexus-operator-config` | M1b keys only; no `prometheusURL`, `recorder*` or `metricsPort` |
| FR DB | Not deployed (no StatefulSet or PVC in `nexus-system`) |
| `DIGEST_BUMP_TOKEN` | Not set (repository secret list empty) |
| Branches | `dev` = `d404849`, 128 commits ahead of `main` = `86cea844`, 0 behind; `experiment/dev-state` tree = `main` tree (`e538b50`) |
| CI on `dev` `d404849` | `repo-checks` and `harness` green |
| Audit log | `/var/log/nexus-audit/`, readable through group `adm`; 100 MB × 10 backups ≈ 30–35 h of cluster uptime |

## What `dev` holds that `main` doesn't

304 files, +29,579/−354, PRs #128–#150.

| Deploys on promotion (Application on `main`) | Change |
|---|---|
| `platform` | `nexus-operator-dev.yaml`: Role and binding for `deployments/scale`, `pods/eviction` in `nexus-dev`; ClusterRole `nexus-operator-evidence` (§11 reads); AppProject admits `ClusterPolicy`, `NetworkPolicy` |
| `nexus` | FR DB StatefulSet, Service, schema, roles; Service `nexus-operator-metrics`; operator Deployment gets port 9102, `fsGroup`, the optional Secret mount, still on image `ef3c6955` |
| `observability` | FR datasource and dashboard; operator ServiceMonitor; `NexusRolloutStuck`, `NexusCrashLooping`; the Alertmanager `config` override; Grafana env `FR_READER_PASSWORD` |
| CI on the `main` push | `ci.yml`, `operator.yml`, `reasoner.yml` build amd64 + arm64, Trivy, sign, verify, open digest-bump PRs into `dev` |

Not deployed by a promotion: `platform/m3-live` (K1–K6, N1–N6; nothing references it), `reasoner/k8s`, `nexusctl/`,
`experiments/`, `runbooks/`. `overlays/` and `platform/namespaces`, `platform/load` are unchanged.

## Steps

### A — Offline, before any promotion

| # | Step | Owner | Check | Rollback |
|---|---|---|---|---|
| A1 | Merge this plan | agent, after typed approval | `repo-checks` green | Revert |
| A2 | F5 decision (Q7); a PR to `dev` only for option A or B, so promotion 1's sample-api build carries it | owner decides; agent PR | CI green; `ci.yml` builds on promotion 1 | Revert |
| A3 | G7 PR: `verify-state.sh` M2 checks. FR DB StatefulSet Ready; staged RBAC by `auth can-i --list --as=system:serviceaccount:nexus-system:nexus-operator` (scale and eviction in `nexus-dev`; none in `nexus-prod`, `nexus-data`); live rollout strategy equals Git; `nexus-dev` level read from `origin/experiment/dev-state` `overlays/dev/namespace.yaml`, not hard-coded (`scripts/verify-state.sh:243`) | agent (local session) | Offline tests with stub `kubectl`; CI green | Revert |
| A4 | envtest RBAC run (#139): `SubjectAccessReview` denies scale and eviction in `nexus-prod` | agent | Passes with `NEXUS_ENVTEST_DIR=~/nexus-envtest` | — |
| A5 | Confirm G2 (Q13) on #144's evidence: `operator/tests/unit/test_act.py:454-576`, `test_act_postgres.py:189`, `test_transitions.py` | owner | Owner accepts or names a live check | — |
| A6 | `pods/log` scope against spec §11 (Q14). VERIFIED: `final-spec.md:834` grants cluster-wide get/list/watch; `platform/rbac/nexus-operator-dev.yaml:64-82` grants exactly that; the collector reads logs only in the target namespace (`operator/nexus_operator/evidence.py:187-199`) | owner decides | Live: step C3 | — |

### B — Owner prerequisites, before any promotion

| # | Step | Owner | Check | Rollback |
|---|---|---|---|---|
| B1 | `DIGEST_BUMP_TOKEN` (Q8): fine-grained PAT, this repository only, contents and pull requests write. `gh secret set DIGEST_BUMP_TOKEN --repo koussayx8/nexus-platform` (prompted; never in argv) | owner | `gh secret list` shows the name | `gh secret delete`; revoke the PAT |
| B2 | `scripts/flight-recorder-secrets.sh` (from a `dev` checkout). Required first: the FR DB `secretKeyRef`s are not optional (`statefulset.yaml:50-61`) | owner | Output names 3 Secrets, `created` or `exists`; no value printed | Delete the 3 Secrets and the 4 `~/.nexus/flight-recorder-*` files (script header) |
| B3 | Add the 7 new keys of `platform/bootstrap-templates/nexus-operator-config.yaml` (`dev`) to the live ConfigMap (`kubectl patch --type merge`; not ArgoCD-managed). The running M1b operator ignores them; the new image reads them at its next start | owner | `kubectl get configmap` lists the keys | Patch the keys out, or set `recorderHost` empty (fail closed: `recorder_unavailable`) |
| B4 | First live run of `scripts/capture-state.sh --backup` (shellcheck-touched, `b05643c`, `543c30a`); doubles as the pre-promotion snapshot. It opens a temporary port-forward | owner, or agent with approval | Exit 0; leak check clean | — (read-only) |
| B5 | `verify-state.sh` baseline | owner, or agent with approval | Exit 0, except the new A3 checks, which fail before C1 | — |

The other shellcheck-touched scripts: `.github/scripts/render-apps.sh` already runs in every `repo-checks` (green on
`d404849`); `infra/k3s/install-k3s.sh` needs `sudo`, is referenced only by `infra/k3s/README.md:8` and is not run in
M2 (Q16).

### C — Promotion 1

| # | Step | Owner | Check | Rollback |
|---|---|---|---|---|
| C1 | PR `dev` → `main`; merge | agent, after typed approval | PR head equals the approved SHA | Revert PR, promote. `nexus` prunes the StatefulSet; its PVC stays (deletion: owner) |
| C2 | Sync | agent (read) | 8 Applications `Synced`/`Healthy` at the merge SHA, stable ≥ 60 s; FR DB 1/1 Ready; Grafana and operator pods Ready (operator still `ef3c6955`) | C1 rollback |
| C3 | RBAC reads (#144) and `pods/log` (A6): `auth can-i --list --as=…nexus-operator` in `nexus-dev`, `nexus-prod`, `nexus-data`, `kube-system` | agent (read) | Scale and eviction only in `nexus-dev`; §11 reads cluster-wide | C1 rollback |
| C4 | Alertmanager and Prometheus (#143): `get --raw` service-proxy GETs of `/api/v2/status` and `/api/v1/rules` | agent (read) | Route `nexus_target =~ ".+"`, `group_by: [nexus_target]`, receiver `null`; both rules loaded | Revert #143's files |
| C5 | Grafana FR datasource (#140): "Save & test" | owner | Datasource OK | — |
| C6 | Kyverno in M2: no NEXUS `ClusterPolicy` exists (K1–K6 are M3) | agent (read) | `get clusterpolicies` lists none of K1–K6 | — |
| C7 | CI on `main` (#141): multi-arch push, Trivy on the pushed digest, `cosign sign` and `verify`, digest-bump PRs (operator, sample-api, reasoner) | agent (read, `gh run view`) | All green; bump PRs open, or the skip notice if B1 was skipped | Fix forward before the next merge (`main` is never left red) |
| C8 | `Audit-Id` dry-run (#140, #139, #144): `kubectl --context default -n nexus-dev scale deployment sample-api --replicas=2 --dry-run=server --as=system:serviceaccount:nexus-system:nexus-operator -v=8`, then the same in `nexus-prod` | owner | `nexus-dev`: success with an `Audit-Id` response header; `nexus-prod`: 403 with an `Audit-Id`; both ids found in `/var/log/nexus-audit/audit.log` | — (dry-run) |

### D — Images, bumps, promotions 2 and 3

| # | Step | Owner | Check | Rollback |
|---|---|---|---|---|
| D1 | Bump PRs into `dev`: operator (`operator/k8s/deployment.yaml`), sample-api `overlays/dev` and `overlays/prod` (Q9). By hand with `.github/scripts/bump_digest.py` if B1 was skipped. The reasoner bump is not deployed (Q15) | agent; merge after typed approval | `cosign verify` of each digest; one image line changed per file | Revert |
| D2 | Promotion 2 | agent, after typed approval | Operator logs `recorder=flight-recorder-db.nexus-system.svc:5432 metricsPort=9102`; `nexus_operator_recorder_up` = 1 (DB, Secret mount, password file, #144); ServiceMonitor target up (#144); `nexus-prod` sample-api on the new digest | Revert the bumps; promote |
| D3 | Forward-merge `main` → `experiment/dev-state` (rule 11) | agent, after typed approval | Merge tree = `main` tree; `sample-api-dev` Synced at it; pods on the new digest; p95 at B = 80 compared with the 15 min before the bump (Prometheus reads) | Revert on `dev`, promote, forward-merge |
| D4 | PR: remove the operator `USER` env (`operator/k8s/deployment.yaml:64-66`) and invert `operator/tests/unit/test_runtime_user.py` (#141 follow-up, Q10); then promotion 3 | agent; merges after typed approval | Operator Ready, no `KeyError` in its log, Kopf identity `nexus-operator` | Revert: the env returns |

D4's test change is under `operator/**`, so `operator.yml` rebuilds on promotion 3 and opens one more bump PR. Its
code is identical to D1's image: close it unmerged.

### E — Rehearsal at L0, no mutation

| # | Step | Owner | Check | Rollback |
|---|---|---|---|---|
| E1 | First live use of the runner: `runner.py plan inject S1 --context default`, then `run … --execute`. The runner's S1 targets `nexus-dev` only (`experiments/harness/runner.py:121-133`); the same hang in `nexus-prod` is a manual `kubectl exec` | owner | `nexus-dev` ends `Recorded`/`level_observe`; `nexus-prod` passes `Diagnosing` → `Proposed` → `Recommended` (#144); no operator scale or eviction in the audit log other than dry-runs | Pods recover on their own; else Kill Switch `halted` (owner) |
| E2 | Optional (Q15): S3 on `nexus-dev` at L0 for the G6 alerts (#143) | owner | `NexusRolloutStuck` fires, grouped by `nexus_target`; Incident `Recorded` | Revert the S3 commit on `experiment/dev-state` |

### F — Level 3 and the exit (U7)

| # | Step | Owner | Check | Rollback |
|---|---|---|---|---|
| F1 | Level `"3"` on `nexus-dev` (Q11): PR to `dev` editing `overlays/dev/namespace.yaml`, promotion 4, forward-merge | agent PR; merges after typed approval | Label `3` live; tree check holds | PR back to `"0"`; Kill Switch `halted` at once (owner) |
| F2 | `verify-state.sh` with G7 (G7 M2 part) | owner, or agent with approval | Exit 0; Applications stable ≥ 60 s | — |
| F3 | Exit demo under B = 80 (Q12): S1 on `nexus-dev` at L3, then the same fault in `nexus-prod`. Afterwards: FR `actions` export; copy the audit files at once (rotation); `scripts/audit-join.py` (G5); `auth can-i --list` in `nexus-prod`; the FR dashboard | owner runs; agent reads | Outline exit criteria 1–5: `Resolved`/`remediation_verified` through `restart_v1`, one eviction at a time within the PDB; intent row before the request and its `audit_id` matched; `nexus-prod` `Recommended`; no operator mutation in `nexus-prod`; the Incident on the dashboard | Kill Switch `halted`; level back to `"0"` (F1) |
| F4 | Exit docs PR: `CURRENT_STATE.md` (`verify-state.sh --out`), `CHANGELOG` `[0.4.0]`, `TASKS.md` M2 boxes (G1–G5, G7), README M2 row; promotion 5; forward-merge | agent; merges after typed approval | CI green; tree check holds | Revert |
| F5 | Tag `v0.4.0` on promotion 5's merge SHA: `git tag -a v0.4.0 <SHA>`, `git push origin v0.4.0` | agent, after typed approval naming the tag and SHA | Tag on `origin` points to the SHA | — (a published tag is not moved) |

## Live-check items from the task PRs

| PR | Item | Step |
|---|---|---|
| #140 | FR DB live | C2 |
| #140 | Secrets script run | B2 |
| #140 | Grafana FR datasource | C5 |
| #140, #139, #144 | `Audit-Id` on a live API server | C8; F3 |
| #139 | Real API server | E1, F3 |
| #139, #144 | Kyverno | C6 in M2; K1–K6 at the M3 exit |
| #139, #144 | PDB | F3 (eviction) |
| #139 | envtest RBAC | A4 |
| #144 | DB, Secret mount, password file on a live pod | D2 |
| #144 | ServiceMonitor target | D2 |
| #144 | NetworkPolicy on 9102 | M3: `platform/network` is under `m3-live`, not deployed (Q15) |
| #144 | New RBAC reads | C3 |
| #144 | An Incident reaching `Proposed` | E1 |
| #141 | Multi-arch push, Trivy by digest, `cosign verify`, bump PR | C7, D1 |
| #141 | arm64 image running | January, A1 build (no arm64 host in M2) (Q15) |
| #141 | Remove the operator `USER` env | D4 |
| #143 | G5 on a real audit log and `actions` export | F3 |
| #143 | Both alerts firing on S3 | E2 (Q15) |
| #143 | Alertmanager config and `nexus_target` grouping; Prometheus rule reload | C4 |
| #145 | None listed | — |

## Schedule

| Dates | Steps |
|---|---|
| 11-03 → 11-04 | A1–A6 |
| 11-04 → 11-05 | B1–B5 |
| 11-05 → 11-06 | C1–C8 |
| 11-06 → 11-10 | D1–D4 |
| 11-10 → 11-12 | E1, E2 |
| 11-13 → 11-16 | F1, F2; F3 dry rehearsal of the export and join |
| 11-17 → 11-19 | F3, F4, F5 |
| 11-20 | Deadline; buffer |

## Decisions for the owner

| # | Question | Recommendation |
|---|---|---|
| Q7 | **F5**, from #143: "Options for the live plan: uvicorn `--timeout-graceful-shutdown`, `terminationGracePeriodSeconds`, or leave as is." Here **A** = uvicorn `--timeout-graceful-shutdown` (image `CMD`, `apps/sample-api/Dockerfile:22`), **B** = `terminationGracePeriodSeconds` (base Deployment, both namespaces), **C** = leave as is. Evidence: ADR-022, ~30 s per pod to SIGKILL on an S1 restart or eviction | **C** for M2: no change to the system under test before the exit; the 30 s stays a stated property; reopen at G16 if MTTR needs it |
| Q8 | `DIGEST_BUMP_TOKEN` or manual bumps | Token (B1) |
| Q9 | Bump sample-api (both overlays) to the rebuilt image, or hold the calibrated `3321d6f`. The rebuild changes the base to the pinned `python:3.12-slim@dddfd7e0` | Bump both in promotion 2, with D3's p95 check |
| Q10 | `USER` env removal: its own promotion (as #141 says), or bundled with the bump | Own promotion (3) |
| Q11 | Level route. CLAUDE.md rule 11 requires a forward-merge's tree to equal `main`'s. A direct level commit on `experiment/dev-state` breaks that for every later forward-merge (M4's per-run level commits, #149, will too) | Level via PR to `dev` → `main` → forward-merge (F1); an ADR-021 addendum for M4's runner commits before G16 |
| Q12 | Exit fault: S1 or S2. S2 is "a Locust ramp to twice per-pod capacity on `/work/cpu`" (ADR-027:127), but the deployed locustfile never calls `/work/cpu` (`platform/load/locustfile.py:11`), and R2 (its capacity) was never measured (`TASKS.md:785`, cut with M2-9) | **S1** |
| Q13 | G2: close on #144's offline evidence, or require a live compensation (no step here forces a failed verification) | Close on the offline evidence |
| Q14 | `pods/log`: keep §11's cluster-wide grant (frozen spec), or note a narrowing for spec v1.1 (G17) | Keep; optional G17 note |
| Q15 | Deferrals out of M2: NetworkPolicy on 9102 and K1–K6 (M3), arm64 running (A1, January), S3 alerts (E2 optional, else before G16), the reasoner bump PR (not deployed: merge or close) | Accept; run E2 if E1 finishes by 11-12 |
| Q16 | `infra/k3s/install-k3s.sh`: not run in M2 | Leave unrun; Later item |
| Q17 | Where the findings below go: rule 9 says `TASKS.md` Later, but only task 0 edits `TASKS.md` | In F4's `TASKS.md` edit |

## Findings, out of scope here

- `TASKS.md` still shows T0 open; #136 merged it.
- `scripts/verify-state.sh:303`: the audit probe's `kubectl` call has no `--context`.
- Audit log retention (100 MB × 10) is ~30–35 h of uptime: G5 and M4's reconciliation must copy the files soon after each run.
- The runner's S1 injects in `nexus-dev` only; `nexus-prod` needs a manual `exec` (E1, F3).
- #149's open question: the API-server proxy to the Reasoner under N1–N6 (M3/M4).

## Risks

- Until M3, `nexus-dev`'s floor, bounds and Kill Switch are advisory operator checks (no K1–K6); RBAC and the PDB are
  the authoritative layers. The Kill Switch `halted` stops the operator at stage 5.
- B2 must precede C1: without the Secrets, the FR DB pod cannot start and `nexus` is `Degraded`.
- Between C1 and D2, the ServiceMonitor target is down (the old image serves no 9102).
- WSL2 reboots restart every pod (restart counts above); a reboot during F3 voids that run.
- 17 days for five promotions and three forward-merges, each with a typed approval.
