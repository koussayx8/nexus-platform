# TASKS — NEXUS

**Milestone:** M1 — Dependency DB and `/items` (spec §3, §20, §25). M0 complete: `v0.1.0` on `8f4eaac`.
**Current phase:** M1-1 — `sample-api` `/items` PR. M1-0 done (#68, merge `7cc5811`). Test d blocked on Docker's WSL integration.
**Rules:** `CLAUDE.md`. **Evidence:** `docs/CURRENT_STATE.md` (the from-empty M0-5 rebuild report, 2026-09-26; M0-1 snapshot `docs/state/20260925T064759Z/`).

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

**Standing rule — the AppProject stays as tight as it is now.** Each milestone widens it in the
same pull request that introduces the new kinds, namespaces or repositories it needs.

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

## M0-4 Git convergence — done on `dev`, waiting at its gate

Git only; nothing applied to the live cluster (ADR-014). Branch flow: feature branch → PR → `dev`.
`dev` → `main` happens only at the M0-5 rebuild, because M0-4 changes paths the live cluster
tracks on `main`.

0. [x] Branch flow set up:
   - `main` → `dev` catch-up PR #43;
   - `main` fast-forwarded into `experiment/dev-state` (`159e540`);
   - `repo-checks` new-branch range fixed to scan from merge-base(origin/main) (#44).
1. [x] **Render check** (#45, ADR-012):
   - every Application rendered as ArgoCD would, with `helm template` from Git value files (Helm 3.20.2) and `kustomize build`;
   - checked against its AppProject, then `kubeconform -strict`;
   - negative controls fail as they should. Inline Helm values now fail the check.
2. [x] **`platform`** (#46): namespaces with levels, the AppProject, the `platform` Application, the root Application `platform/argocd/root.yaml` (bootstrap only). The `loki` and `crossplane-infrastructure` Applications are removed. ADR-014, ADR-018.
   - Levels: `nexus-prod` `"1"` and `nexus-data` `"0"` on `main`; `nexus-dev` `"0"` in `overlays/dev` (on `experiment/dev-state` after the rebuild merge), raised to `"3"` in M2.
3. [x] **`kyverno`** (#47): chart 3.8.0 / v1.18.0, values `platform/kyverno/values.yaml`, reports controller and report features disabled. Both ClusterPolicies are removed. ADR-015.
4. [x] **`observability`** (#48), multi-source:
   - chart 86.2.2 + `platform/observability/kube-prometheus-stack-values.yaml` + `platform/observability/config`, no inline values;
   - Loki datasource removed; Grafana admin from Secret `monitoring/grafana-admin`, and the literal password is gone from Git;
   - Prometheus 15d / 9GB on a 10Gi `local-path` PVC;
   - ServiceMonitor, alert and dashboard fixed. ADR-016.
5. [x] **`sample-api-dev`** / **`sample-api-prod`** (#49):
   - `overlays/{dev,prod}`, 2 replicas, PDB `maxUnavailable: 1`, token not mounted, replicas delegated;
   - digest `sha256:45e7a88c…57c` pinned and cosign-verified;
   - AppProject destinations are exactly the §3 namespaces, and only rendered kinds are admitted. ADR-017.
6. [x] Settled (ADR-014):
   - the ghcr package is **public**, so no pull secret;
   - the repository is **public**, so no ArgoCD repository credentials;
   - **no** Ingress, IngressRoute or Gateway route, so k3s gets `--disable traefik --disable servicelb`.
7. [x] Capture-script fix for false booleans; errata in `CURRENT_STATE.md` (this PR).
- **GATE M0-4** — report with the diff by path, the rendered and validated output of every Application, and the removal list.

## M0-5 Bootstrap, rebuild, verify — done

1. [x] `k`, `h`, `g`, `secret_names`, `redact`, `show`, `run`, `need_jq`, `sudo_needed`, `check`
   and a new shared `leak_check` moved into `scripts/lib/readonly.sh` (#51). A `gh_ro` wrapper
   allows only `run list` and GET `api` calls — rejects any short-option cluster carrying
   `f`/`F`/`X` anywhere (not just flags beginning with one) and every long-form
   `--field`/`--raw-field`/`--input`/`--method`; always appends `--method GET` itself.
   `capture-state.sh` sources the library and gains `--backup` (copies a clean, leak-checked
   snapshot to `~/nexus-backup/state-<TS>/`, directory enforced at `0700`). The PEM leak-check is
   narrowed to the actual header/footer line and now also catches PGP private-key blocks.
2. [x] **`scripts/verify-state.sh`** (#52), built on that library. M0 checks: every Application
   (6, including `root`) Synced/Healthy; exactly one default Grafana datasource; no Loki,
   Crossplane or `sample-db`; the sample-api digest — read from each namespace's real tracking
   branch (`origin/experiment/dev-state` for `nexus-dev`, `origin/main` for `nexus-prod`;
   ADR-013/ADR-017), not the local checkout — matches a ready pod, and `/metrics` returns 200 with
   `http_requests_total` and `http_request_duration_seconds`; namespace autonomy levels per
   ADR-018 (a namespace that doesn't exist fails, even where the wanted label is empty); every
   pod's containers ready, `Succeeded` pods skipped, `Failed` pods still fail; a probe-based audit
   check (one `--dry-run=server` create, confirmed in the audit log by name, plus an
   `apiserver_audit_event_total` delta — raw log growth alone proves nothing under the §14 policy);
   the Kill Switch `active`. Writes `--out` (default `docs/CURRENT_STATE.md`), exits non-zero on
   any failure. K1–K6, the Incident CRD/CEL, operator/Reasoner readiness and N1–N6 stay in "Later".
3. [x] **`scripts/bootstrap.sh`** (#53), `--plan` prints every step with `[SUDO]` tags and executes
   nothing (verified: stubbing `k3s`/`kubectl`/`helm`/`git`/`sudo`/`curl`/`systemctl`/`install` to
   log their own invocation and exit 1 produced an empty log and byte-identical `--plan` output).
   Order: k3s (pinned `v1.34.6+k3s1`, refuses to run if already installed, §14 audit policy,
   audit-log pre-created `root:adm 0640` so rotation stays group-readable, `--disable traefik
   --disable servicelb`, optional `~/.nexus/dockerhub.env` → `registries.yaml`) → kubeconfig →
   monitoring namespace + `grafana-admin` (generate-or-reuse, create-or-rotate, never `apply`) →
   ArgoCD `v3.3.8` (server-side apply) + `argocd-admin` (always fresh, temp-file-then-move,
   empty/failed read is fatal) → a merge-order guard (`origin/main` has the convergence,
   `origin/main` is merged into `origin/experiment/dev-state`) then the AppProject and
   `root.yaml`, both read via `git show origin/main:<path>` → wait for `platform` → `nexus-
   killswitch`/`nexus-operator-config` (create-only-if-absent, read the same way, never through
   ArgoCD) → wait for every Application → `verify-state.sh`. Full design and the ACL-vs-lumberjack
   evidence chain: ADR-019.
4. [ ] **Rebuild checklist (ADR-014).** Nothing here runs until separately approved, step by step,
   regardless of who it says runs it.

   1. **Final capture.** Who: Claude (read-only). `scripts/capture-state.sh --backup`. Sudo: no.
      Expected: exit 0; "snapshot copied to `~/nexus-backup/state-<TS>`" printed; leak self-check
      clean. On failure: exit 2 is a script/guard error — investigate before retrying; exit 3 is a
      leak-check hit — read `LEAK-CHECK.txt`'s file list (never the match), decide if it's a real
      leak or a redaction gap, fix, re-run. Do not proceed to the uninstall until this is clean.
      **VERIFIED for the original cluster** (`state-20260926T072146Z`, 07:21Z, copy in
      `~/nexus-backup/`) — this protects the original pre-M0 cluster, and it ran once. **Not
      applicable to the retry clusters:** the two failed from-empty attempts and this final one were
      disposable by design (the cluster holds no persistent data), so retrying needed no fresh
      capture each time.
   2. **k3s uninstall.** Who: you. Done — 2026-09-26, before the bootstrap run below. The standard
      `/usr/local/bin/k3s-uninstall.sh`. Sudo: yes.
      Expected: `k3s` binary/service gone; `/etc/rancher/k3s` and `/var/lib/rancher/k3s` removed.
      `/var/log/nexus-audit/`, `~/.kube/config` and `~/.nexus/*` are outside its scope and persist
      by design (`grafana-admin` is meant to survive a rebuild unless you delete it to rotate;
      delete `~/.nexus/argocd-admin` if you want, `bootstrap.sh` always overwrites it anyway). On
      failure: check `systemctl status k3s`; if the unit or files won't clear, stop and ask —
      don't force anything by hand outside the script. **`/var/lib/kubelet` left over, device
      busy** (seen in practice): it's a mount point, not an ordinary directory — never `rm -rf` a
      mount point (it can silently write into whatever's still mounted underneath, or fail
      partway leaving a worse mess). Unmount first, then remove the now-empty directory:
      ```
      while mountpoint -q /var/lib/kubelet; do sudo umount /var/lib/kubelet; done
      sudo rmdir /var/lib/kubelet
      ```
      Only if a mount won't release (busy, in use by a lingering process): `wsl --shutdown` from
      Windows PowerShell (not inside WSL), then reopen the terminal and retry the loop above — this
      is the fallback, not the first move.
   3. **`dev` → `main` PR.** Done — [#63](https://github.com/koussayx8/nexus-platform/pull/63),
      merged 2026-09-26T13:34:36Z. Who: Claude opens it, you approve the merge (same as every PR this
      session — a first for this specific direction, no precedent, so review the diff even though
      every file already passed `repo-checks` on `dev`). `gh pr create --base main --head dev`.
      Sudo: no. Expected: `repo-checks` passes; merge commit, matching every prior PR's convention.
      On failure: a `repo-checks` failure here would mean something environment-specific to `main`
      that `dev`'s own checks didn't catch — investigate the specific failing step before retrying.
   4. **`main` → `experiment/dev-state`.** Done —
      [#64](https://github.com/koussayx8/nexus-platform/pull/64), merged 2026-09-26T13:37:18Z. Who:
      Claude opens a PR (not a direct push, even though
      ADR-013's ruleset allows one — a PR here is for visibility), you approve the merge. `gh pr
      create --base experiment/dev-state --head main`. Sudo: no. Expected: clean, conflict-free
      merge — `experiment/dev-state` has taken no divergent commits yet (no M1 experiment runs
      have happened), so this should just be a fast-forward-shaped merge. **Never force-push this
      branch** (ADR-013) regardless of what goes wrong. On failure (a real conflict): resolve on a
      working branch, merge commit, still no force-push.
   5. **`bootstrap.sh`.** Done — 2026-09-26, "bootstrap: done", exit 0, log
      `~/nexus-backup/bootstrap-20260926T134351Z.log` (leak-checked clean). Who: you, from a clean
      checkout (any branch — the merge-order guard
      reads `origin/main`/`origin/experiment/dev-state` directly, not the local checkout, by
      design). `./scripts/bootstrap.sh` (no `--plan`). Sudo: yes, for the steps `--plan` already
      tags. Expected: "bootstrap: done", no `FATAL` line. On failure: `bootstrap.sh` names the
      exact failed step. **Known gap:** if failure happens *after* the k3s install step succeeds,
      a bare re-run will refuse at step a ("k3s is already installed") — re-run the uninstall
      first, or handle the specific failed step by hand referencing ADR-019, rather than
      re-running the whole script blindly.
   6. **Post-rebuild-attempt sequence**, revised after two real bugs surfaced on the first two
      real `bootstrap.sh` attempts (the 180s ArgoCD rollout timeout, and `kyverno` stuck
      `OutOfSync` forever — both diagnosed read-only against the live, partially-bootstrapped
      cluster, neither fixed by touching the cluster directly):

      **a) Fixes land on `dev`, then a `dev`→`main` gate PR, then `main`→`experiment/dev-state`.**
      — **done.** Both fixes — the configurable-timeout PR (**merged, #58**) and the `kyverno`
      `ServerSideDiff=true` fix (**merged, #59**) — went through the normal PR flow into `dev`, then
      the second `dev`→`main` gate PR (**#60**, same shape as #56), then forward-merged into
      `experiment/dev-state` (**#61**).

      **b) Confirm the *live* cluster converges, without a rebuild.** — **done**, 2026-09-26. `root`
      tracks `main` with `selfHeal: true`, so once the `ServerSideDiff=true` fix reached `main`,
      ArgoCD picked up the changed `kyverno` Application spec on its own. Checked read-only:
      `kyverno` and `root` both `Synced`/`Healthy`, then `verify-state.sh` passed (exit 0, 8/8)
      against this same live cluster — no `bootstrap.sh` run needed for this step. (This round also
      needed the analogous `root` `ServerSideDiff=true` fix, **#62**, gated through a third
      `dev`→`main` PR **#63** and forward-merged via **#64** — the same pattern as (a), one more
      round, since `root` hit the identical class of bug after `kyverno` was fixed.)

      **c) Final from-empty rebuild.** — **done, 2026-09-26.** `bootstrap.sh` (no `--plan`) ran to
      "bootstrap: done", exit 0; `verify-state.sh` (both the run inside `bootstrap.sh` and an
      independent rerun) passed 8/8; k3s confirmed `v1.34.6+k3s1`; all 6 Applications
      `Synced`/`Healthy`, stable ≥60s. Total bootstrap duration ~27m17s; longest wait was the
      `observability` Application's own sync (~17m47s, see the new "Later" item on its timeout
      margin). The ADR-019 audit-log rotation test was also run against this cluster and passed
      (see ADR-019's "Results" section) — this is the first attempt to reach `verify-state.sh` from
      a genuine empty state (the first two attempts died at the ArgoCD rollout wait and the
      `kyverno` wait respectively).

      **d) M0 exit.** — **done**: #65 → gate PR #66 → forward-merge #67 → tag `v0.1.0`
      (annotated `6564150`, on origin, dereferences to `8f4eaac`). The regenerated `docs/CURRENT_STATE.md`, the
      ADR-019 rotation-test results, and `CHANGELOG.md`'s first section (drafted from every merged
      PR) go to **`dev`** first — same as everything else — then a **fourth** `dev`→`main` gate PR
      (the actual M0-exit PR), then `main`→`experiment/dev-state` again, then the baseline tag:
      `git tag -a v0.1.0 -m "M0 complete" main && git push origin v0.1.0` — needs your explicit
      go-ahead given what it signifies. On failure at any point in a/c/d: `bootstrap.sh` names the
      exact failed step (**known gap**: a bare re-run after k3s already installed refuses at step
      a — re-run the uninstall first); a PR failure is a content issue in whatever it's carrying,
      fix and retry; a tag-push failure (e.g. already exists) is never resolved by force.
5. [x] `verify-state.sh` exits 0 on a genuine from-empty rebuild (item 6c), the M0-exit
   `dev`→`main` merge (#66) and its `experiment/dev-state` forward-merge (#67) are both done
   (item 6d), and the baseline tag `v0.1.0` is on `main` (`8f4eaac`). **M0 complete.**
6. [x] ADR for bootstrap and the audit policy — ADR-019. Kyverno's `ServerSideDiff=true` finding
   recorded as an addendum to ADR-015.
7. [x] `CHANGELOG.md`, Keep a Changelog format, one section per gate, each entry linking its PRs
   and ADRs. First section covers M0, drafted from the merged PRs (this PR, ahead of the tag itself
   — the tag still needs its own separate go-ahead per item 5).
- **GATE M0-5** — passed. From-empty rebuild (6c), ADR-019 rotation test, M0-exit docs (#65), gate
  PR #66, forward-merge #67 and the `v0.1.0` tag, each separately approved.

## M1 — Dependency DB and `/items`

Scope decided at the M1 plan gate: the Dependency DB (PostgreSQL StatefulSet in `nexus-data`,
Application `dependency-db` on `main`) and `sample-api` `/items` reading it, so that S5 (§20:
app role `NOLOGIN`, sessions terminated) is a gray failure: `/items` 5xx, readiness green (NF-23).
Decisions: `emptyDir` storage; a from-empty rebuild at the M1 exit; `/items` is **not** gated by
`NEXUS_FAULTS_ENABLED` (a reading of §3, recorded in ADR-020); path `apps/dependency-db/`; the
other M1-tagged items move to M1b. Branch flow as in M0: feature branch → PR → `dev`, then gated
`dev`→`main` and `main`→`experiment/dev-state`. Every merge needs explicit approval.

**Approved plan changes 1–24** (the phase each lands in is in brackets):

1. The `verify-state.sh` `/items` check lands in M1-5, not M1-3. [M1-5]
2. DB env wiring (`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` from `secretKeyRef`) lands only
   in M1-5, in the same commit as the digest bump. [M1-5]
3. Per-environment roles `app_dev` / `app_prod`. §20 names "the application role" in the
   singular but no namespace, so S5 targets `app_dev` only. [M1-2]
4. `/items` fails within 3 s and logs the server's error message. So: a new connection per request
   (psycopg-pool swallows the connect error and raises `PoolTimeout` instead), `connect_timeout=2`
   (psycopg's minimum), `statement_timeout=500`. [M1-1]
5. `dependency-db-secrets.sh` fails if any `~/.nexus` file is missing while any of the three
   Secrets exists. [M1-3]
6. / 9. The S5 discard rule. `emptyDir` survives container restarts: a restart is an outage
   that does not heal S5. A pod replacement (new `metadata.uid`) re-runs initdb and heals S5. The
   runner records the DB pod's `metadata.uid` and `restartCount` before and after each run and
   discards the run on either change. [ADR-020, M1-2]
7. Audit retention: an ADR-019 addendum accepting about 10 days (the per-run extract is what §14
   keeps); re-measure at M4. [M1-6]
8. The Later items `step_h`, `set -e`, step timestamps and the restart-count line go into M1-3,
   one commit each. The `verify-state.sh` retry window, the second clause of the `step_h` Later
   item that the first pruning pass dropped, becomes its own commit 7, because it lives in a
   different script (added at the #68 follow-up gate). [M1-3]
10. `/items` is a plain `def` (threadpool), not `async def`, with a test asserting it. [M1-1]
11. DB connections stay below `max_connections`. anyio's default threadpool is 40 per pod, which
    gives 560 connections unbounded at 7 pods × 2 envs. A per-pod `BoundedSemaphore(5)` (0.3 s
    acquire, else 503 `db_slots_exhausted`) caps it at 70. `CONNECTION LIMIT 35` per role backs
    that up. 70 ≤ 97 (100 − 3 superuser-reserved). The time budget is 0.3 + 2.0 + 0.5 =
    2.8 s. [M1-1, M1-2, ADR-020]
12. pytest, pytest-asyncio and httpx move from `requirements.txt` (the runtime image) to
    `requirements-dev.txt`; `ci.yml` installs both. [M1-1]
13. Roles are created by an init `.sh` script: SQL on stdin, passwords read with psql
    `\getenv`, never in argv or logs, and `log_min_error_statement = panic` for the session. [M1-2]
14. The 24 h audit measurement is valid only if the boot ID (and the k3s start time) is unchanged
    across the window. [M1-6]
15. Probes use TCP (`pg_isready -h 127.0.0.1`), never the socket, because init runs on a
    socket-only temporary server. [M1-2, ADR-020]
16. / 18. dependency-db limits are provisional until the M1b Locust calibration. It passes only
    with zero `db_slots_exhausted`, CFS throttled ÷ total periods ≤ 1%, and a working-set peak
    ≤ 80% of the memory limit. [ADR-020, M1b]
17. Plan-document fixes (rev 4). No task.
19. The ConfigMap sets `defaultMode: 0555` explicitly. The entrypoint **executes** an executable
    `*.sh` and sources a non-executable one. Test d mounts the script with the same mode and lines
    up the probe timestamps against `init process complete`. [test d, M1-2]
20. A half-initialised DB never goes Ready. The last line of `10-roles.sh` (under `set -e`)
    writes `/var/lib/postgresql/data/.nexus-init-done`. Every probe runs
    `sh -c 'pg_isready -h 127.0.0.1 -p 5432 -q && test -f <marker>'`. Test d covers the negative
    case: failing init, PGDATA on a docker volume, `docker restart`. [test d, M1-2, ADR-020]
21. The container is named `dependency-db`. Queries carry `namespace="nexus-data"`. An empty or
    NaN result is a FAIL. [ADR-020, M1b]
22. Test d positive case: the exact probe command in a timestamped loop, non-zero until
    `init process complete`, then 0. [test d]
23. The test d negative container runs without `--rm`. [test d]
24. startupProbe `failureThreshold` is 150 (5 min at 2 s), since a mid-init kill with the marker is
    a permanent CrashLoop. The init duration is recorded in test d and at the M1 exit rebuild.
    ADR-020 names the recovery: `kubectl delete pod dependency-db-0`, only with the owner's
    approval. The `verify-state.sh` rollout term (360 s, M1-3 commit 7) is derived from this
    startupProbe budget: changing one means re-deriving the other. [test d, M1-2, M1-3, ADR-020]

**Phases**

- **M1-0 — plan.**
  - [x] Read-only live check: 6/6 Applications `Synced`/`Healthy`; `verify-state.sh --out
    <scratch>` exit 0, 8/8, leak check clean.
  - [x] Gate reports a–h delivered with this PR.
  - [x] This PR merged (#68, merge `7cc5811`). **GATE M1-0.**
- **Test d — offline Postgres test, which gates the M1-2 PR.**
  - [ ] Owner enables Docker Desktop's WSL integration.
  - [ ] Run the pinned `postgres@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f`
    (17.11) as UID 999, caps dropped, tmpfs for PGDATA and the socket, covering changes 15 and
    19–24, the `NOLOGIN` error text, and 0 password hits in the logs. If `--read-only` is the only
    cause of a failure, rerun without it.
- **M1-1 — `sample-api` `/items`.**
  - [ ] PR `feat(sample-api)`: changes 4, 10, 11 and 12; `psycopg[binary]`; tests; version 0.2.0.
    No manifest change.
- **M1-2 — dependency-db.**
  - [ ] PR `feat(dependency-db)`: StatefulSet, headless and ClusterIP Service, init ConfigMap,
    Application, AppProject `apps/StatefulSet` (same PR, standing rule), ADR-020. ADR-020 also
    records that the `verify-state.sh` rollout term (360 s) is derived from the startupProbe
    budget (change 24): changing one means re-deriving the other.
  - [ ] AppProject/Application ordering. Confirm, read-only against the live Applications
    (`.status.resources`), which Application owns the AppProject.
    - If `root`: `argocd.argoproj.io/sync-wave: "-1"` on the AppProject, in this PR.
    - If `platform`: no sync-wave (waves do not order across Applications; child Application
      health is not assessed by default, and enabling it is out of scope). Instead, write the
      M1-4 value of `NEXUS_VERIFY_APPS_TIMEOUT` into the M1-4 procedure, derived as reconcile
      delay + retry backoff + rollout + 60 s stable window.
  - [ ] Validation: kustomize, kubeconform, render check, then a server-side dry-run against the
    live cluster (approval first).
- **M1-3 — scripts.**
  - [ ] PR `feat(scripts)`, one commit each:
    1. `step_h` simultaneous-stable. It adds the shared predicate `scripts/lib/apps-stable.jq`, a
       pure jq filter and not a shell wrapper, so `bootstrap.sh` still does not source
       `readonly.sh`.
       - **Input:** one `kubectl get applications -n argocd -o json` snapshot, plus jq args: the
         expected names, `--arg repo https://github.com/koussayx8/nexus-platform.git`,
         `--arg main <origin/main SHA>` and `--arg devstate <origin/experiment/dev-state SHA>`.
         The caller resolves the SHAs with `git rev-parse` after the `git fetch origin main
         experiment/dev-state` that both scripts already run: `bootstrap.sh`'s merge-order
         guard, and `verify-state.sh:122`.
       - **True only if** every expected Application in that same snapshot is `Synced` **and**
         `Healthy` **and** at the expected commit. The expected commit is `devstate` for
         `sample-api-dev` and `main` for every other Application.
       - **Revision check:** a single-source app compares `.status.sync.revision`. A multi-source
         app (live today: `kyverno`, `observability`) compares every
         `.status.sync.revisions[i]` whose `.spec.sources[i].repoURL` equals `repo`, by index.
         Chart sources (`3.8.0`, `86.2.2`) are skipped. An app with no Git source, or a
         `revisions` array shorter than `sources`, is false.
       - **Why:** `Synced` is relative to the last revision ArgoCD fetched. For up to about 180 s
         after a merge (reconcile delay below), every app is `Synced`/`Healthy` at the *old*
         commit, and a status-only predicate would pass on the pre-merge state.
       - **Read-only:** no `argocd.argoproj.io/refresh` annotation and no other write; the scripts
         only wait for ArgoCD's own reconcile.
       - `step_h` polls it every 5 s and passes only after 60 s of consecutive true snapshots; any
         false resets the streak. Per-app timeouts stay the outer bound.
       - Offline fixture tests for `apps-stable.jq`, run in `repo-checks`, same commit. Five
         cases: all apps at the expected SHA → true; one app at the old SHA → false; chart + Git
         multi-source → true; `revisions` shorter than `sources` → false; `repoURL` mismatch (for
         example a missing or extra `.git` suffix) → false.
    2. `set -e`.
    3. Step timestamps.
    4. Restart-count info line.
    5. `dependency-db-secrets.sh` + the bootstrap call.
    6. `dependency-db` in both `EXPECTED_APPS` + a DB-pod-Ready check.
    7. **`verify-state.sh` Application retry window.**
       - Bounded wait: poll every 5 s, total bound `NEXUS_VERIFY_APPS_TIMEOUT`, default **600 s**.
       - Every poll is written to the report: UTC timestamp, each app's sync/health, expected vs
         observed revision(s), predicate result, current streak.
       - Pass only when the **same** `apps-stable.jq` predicate as commit 1 (revision check
         included) has held for 60 s of consecutive snapshots, never on the first success.
       - At the bound: FAIL, with the last snapshot.
       - **Default, derived as additive terms:** reconcile delay 180 s + rollout 360 s + stable
         window 60 s = **600 s**.
         - Reconcile delay, 180 s: ArgoCD v3.3.8 polls Git every `timeout.reconciliation` 120 s
           plus up to `timeout.reconciliation.jitter` 60 s. The live `argocd-cm` overrides
           neither. A GitHub webhook cannot shorten it, because `argocd-server` is `ClusterIP` with no
           Ingress (ADR-014).
         - Rollout, 360 s: the slowest M1 rollout is dependency-db's first start. Its startupProbe
           ceiling is 150 × 2 s = 300 s (change 24), plus a 60 s image-pull allowance for
           `postgres` 17.11, 161.3 MB compressed (implies ≥ 2.7 MB/s; UNVERIFIED, measured at test
           d and M1-4). This dominates sample-api: 2 pods rolled one at a time (`maxSurge` 1,
           `maxUnavailable` 0), each ≤ 52 s startupProbe + 10 s readiness, about 124 s.
         - Stable window, 60 s: the commit-1 streak.
       - Not included: ArgoCD sync-retry backoff after a failed sync attempt (10 s doubling up to
         `maxDuration: 3m`), for example the AppProject/Application ordering race at M1-4. For
         that case, set a larger bound with the env var and record the value used in the report.
  - [ ] Validation: `--plan` and the 6-scenario fault-injection rerun. **GATE M1-3.**
- **M1-4 — DB live.**
  - [ ] The owner runs `dependency-db-secrets.sh` on the live cluster.
  - [ ] `dev`→`main` gate PR (DB via ArgoCD; CI signs the new image), then the
    `experiment/dev-state` forward-merge. **GATE M1-4.**
- **M1-5 — `/items` live.**
  - [ ] One commit: digest bump (`cosign verify` first) + DB env wiring (change 2).
  - [ ] The `verify-state.sh` `/items` check (change 1).
  - [ ] Gate PR, forward-merge, `verify-state.sh` live. **GATE M1-5.**
- **M1-6 — exit.**
  - [ ] S5 smoke test on `app_dev` (approval first): `/items` 503 in under 3 s with the `FATAL`
    text in the log; `/ready` 200; prod unaffected; reset; DB `metadata.uid` and `restartCount`
    unchanged.
  - [ ] Audit-retention addendum (changes 7 and 14).
  - [ ] From-empty rebuild (owner; `NEXUS_WAIT_TIMEOUT_OBSERVABILITY=1800`).
  - [ ] `CURRENT_STATE.md`, `CHANGELOG` `[0.2.0]`, tag `v0.2.0` (separate approval).
    **GATE M1 exit.**

## M1b — the rest of what the spec and TASKS tagged M1 (after M1, before M2)

- The spec §3 Z-score recording rules and the four anomaly alerts, replacing the interim
  `SampleAPIHighErrorRate`.
- The `nexus` Application with the operator (§3, §25).
- sample-api fault hooks `/fault/hang`, `/fault/inject-logs`, `/work/cpu` and `NEXUS_FAULTS_ENABLED`.
- The §27 M1 verifications: the Kopf status-persistence/standalone spike; kube-state-metrics series
  and the Alertmanager v2 API; CEL transition rules; the Locust capacity ramp and baseline
  (this includes the change-16 dependency-db thresholds).

## Later — out of scope for M1

- `scripts/capture-state.sh:508`: `for i in $(seq 1 40); do ... done` (the port-forward readiness
  wait) never references `$i` in the loop body — a shellcheck SC2034-shaped unused-variable pattern
  (`for _ in $(seq 1 40)` reads the intent correctly). Harmless as written, worth a lint pass.
- `observability`'s Application sync took ~17m47s against bootstrap.sh's own 1200s (20 min)
  `NEXUS_WAIT_TIMEOUT_OBSERVABILITY` budget in the 2026-09-26 from-empty rebuild — about 88% of the
  budget, driven by a Prometheus PVC provisioning retry and the kube-prometheus-stack
  admission-webhook hook Jobs running twice (evidence: `kubectl get events`, ADR-019/CHANGELOG).
  Not a failure this time, but close enough to the ceiling to revisit: raise the timeout, or look at
  why the webhook hooks re-run.
  The M1 exit rebuild passes `NEXUS_WAIT_TIMEOUT_OBSERVABILITY=1800` as an interim measure.
- Re-measure audit-log growth and effective retention at M4, once the Experiment Runner exists
  (change 7; the M1 ADR-019 addendum accepts about 10 days).
- The WSL VM rebooted at 2026-09-26 20:57Z (`journalctl --list-boots`). All four sample-api
  containers show last state `Unknown`, exit 255, at 20:59:58Z. The cluster recovered on its own
  and the node IP was unchanged. Informational. Relevant to the run pre-checks and the discard
  rules once experiments start.
- `verify-state.sh` gains checks per milestone: K1–K6 in Enforce, the Incident CRD and CEL, operator and Reasoner Ready, N1–N6.
- M3: WSL2 changes the node IP on restart, so NetworkPolicies template it at bootstrap and never hardcode `172.19.233.100`. N1–N6.
- M3: CODEOWNERS on `platform/policies/`, `platform/rbac/` and the Action Catalogue (§19), once those paths exist.
- M3: Kyverno `verifyImages` (Audit first) for the signing identity recorded in ADR-017 (SHOULD, §19).
- Spec v1.1 also records the Application name `observability` (spec §3 says `monitoring`, ADR-016).
- `platform/argocd/configs/argocd-cm-patch.yaml` still configures Crossplane exclusions; M0-5 decides what `bootstrap.sh` applies.
- CI per §19: Trivy scans the pushed digest, not `:latest`; add a digest-bump PR step.
- CI per §19: "Dependabot opens weekly pull requests into `dev`". That needs a `dependabot.yml` with `target-branch: dev`. Today only security updates run, against `main`.
- Spec v1.1 (ADR plus version bump): the `experiment/dev-state` sequencing and forward-commit reset (§13, ADR-013), the branch ruleset (§19, ADR-013), and the unfiltered required check (§19, ADR-012).
- `repo-checks`: on a push that creates a branch, the range falls back to `-1 <sha>`. For a merge commit that scans 0 commits (seen when `dev` was created); the tree scan still ran. Make that path scan `origin/main..<sha>`, or accept it.
- Docs pass: `README.md` still describes Backstage, Crossplane and the old autonomy ladder. `docs/NEXUS_STATUS.md` and `docs/CUT_LIST.md` are OpenCode-era; decide whether to rewrite or archive them.
- Local only: about 1.9 GB of ignored Backstage build output remains in `platform/backstage/` (`node_modules`, `dist`, Yarn state). Delete it whenever you like.
- The stash `m0-2: dropped dashboard change` can be dropped once M0-4 rebuilds the dashboard. `git stash drop` is denied to agents, so you drop it.
