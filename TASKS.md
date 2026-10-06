# TASKS — NEXUS

**Milestone:** M1b — the rest of what the spec tagged M1 (see M1b below). M0 complete: `v0.1.0` on `8f4eaac`. M1 complete: `v0.2.0` on `ce2b174`.
**Current phase:** M1b-0 — guard ADR, settings and the M1b task list. M1-0 done (#68, `7cc5811`); M1-1 done (#69, `fb047e1`); test d passed 2026-09-27; M1-2 done (#70, `05e859a`); M1-3 done (#72, `5d5120e`); M1-4 done (#74, `87e3ab3`); M1-5 done (#76, `ee39cef`); M1-6 done (#80, #81, `ce2b174`).
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
    ADR-020 names the recovery: `kubectl delete pod dependency-db-0`, run by the owner (the
    agent's guard denies `kubectl delete`), only with the owner's approval. The `verify-state.sh` rollout term (420 s, M1-3 commit 7) is derived from this
    startupProbe budget: changing one means re-deriving the other. From M1-5 the rollout term is
    600 s and the coupling rule names four values (M1-5 below, ADR-020). [test d, M1-2, M1-3, ADR-020]

**Phases**

- **M1-0 — plan.**
  - [x] Read-only live check: 6/6 Applications `Synced`/`Healthy`; `verify-state.sh --out
    <scratch>` exit 0, 8/8, leak check clean.
  - [x] Gate reports a–h delivered with this PR.
  - [x] This PR merged (#68, merge `7cc5811`). **GATE M1-0.**
- **Test d — offline Postgres test, which gates the M1-2 PR.**
  - [x] Owner enables Docker Desktop's WSL integration.
  - [x] Run the pinned `postgres@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f`
    (17.11) as UID 999, caps dropped, tmpfs for PGDATA and the socket, covering changes 15 and
    19–24, the `NOLOGIN` error text, and 0 password hits in the logs. If `--read-only` is the only
    cause of a failure, rerun without it.
    - **Passed 2026-09-27**, against the M1-2 drafts, all runs with `--read-only`. Evidence and
      figures are in ADR-020: UID 999; the execute branch; socket before TCP, and the probe 0 only
      after `init process complete`; the negative case non-zero in 69/69 samples after
      `Skipping initialization`; the `NOLOGIN` text; 0 password hits; the PGDATA subdirectory
      required on a root-owned mount; pull 55.9 s (hence the 120 s allowance).
- **M1-1 — `sample-api` `/items`.**
  - [x] PR `feat(sample-api)` (#69, merge `fb047e1`): changes 4, 10, 11 and 12; `psycopg[binary]`; tests; version 0.2.0.
    No manifest change.
- **M1-2 — dependency-db.**
  - [x] PR `feat(dependency-db)` (#70, merge `05e859a`): StatefulSet, headless and ClusterIP Service, init ConfigMap,
    Application, AppProject `apps/StatefulSet` (same PR, standing rule), ADR-020. ADR-020 also
    records that the `verify-state.sh` rollout term (420 s) is derived from the startupProbe
    budget (change 24): changing one means re-deriving the other. It also records that
    connect-time errors carry `sqlstate=None` (psycopg builds the `OperationalError` client-side),
    so the S5 evidence is the message text (`FATAL: role "app_dev" is not permitted to log in`),
    and the app has no message classifier.
  - [x] AppProject/Application ordering. Confirm, read-only against the live Applications
    (`.status.resources`), which Application owns the AppProject.
    - If `root`: `argocd.argoproj.io/sync-wave: "-1"` on the AppProject, in this PR.
    - If `platform`: no sync-wave (waves do not order across Applications; child Application
      health is not assessed by default, and enabling it is out of scope). Instead, write the
      M1-4 value of `NEXUS_VERIFY_APPS_TIMEOUT` into the M1-4 procedure, derived as reconcile
      delay + retry backoff + rollout + 60 s stable window.
    - **Result (2026-09-27, read-only): `platform`.** Its `.status.resources` lists
      `argoproj.io/AppProject argocd/nexus`, and the live AppProject's tracking-id is
      `platform:argoproj.io/AppProject:argocd/nexus`. `root`'s `.status.resources` lists only the
      five Applications. So no sync-wave; the timeout goes into M1-4 below.
  - [x] Validation: kustomize, kubeconform, render check, then a server-side dry-run against the
    live cluster (approval first).
    - kustomize + kubeconform `-strict` locally and in `repo-checks`; the render check in
      `repo-checks` (`dependency-db: project=nexus objects=4`).
    - Server-side dry-run, run by the owner on 2026-09-27: all 7 objects `(server dry run)`, no
      PodSecurity warning (`nexus-data` is `enforce`/`warn`/`audit` `restricted`), and a Pod built
      from the StatefulSet template admitted under `enforce`. The only warning was the API
      server's generic one on the ArgoCD finalizer name.
- **M1-3 — scripts.**
  - [x] PR `feat(scripts)` (#72, merge `5d5120e`), one commit each:
    1. `step_h` simultaneous-stable. It adds the shared predicate `scripts/lib/apps-stable.jq`, a
       pure jq filter and not a shell wrapper, so `bootstrap.sh` still does not source
       `readonly.sh`.
       - **Input:** one `kubectl get applications -n argocd -o json` snapshot, plus jq args: the
         expected names, `--arg repo https://github.com/koussayx8/nexus-platform.git`,
         `--arg main <origin/main SHA>` and `--arg devstate <origin/experiment/dev-state SHA>`.
         The caller resolves the SHAs with `git rev-parse` after the `git fetch origin main
         experiment/dev-state` that both scripts already run: `bootstrap.sh`'s merge-order
         guard, and the fetch at the start of `verify-state.sh` (moved there by commit 7 so M1 and M4
         read the same refs; M1 fails if it did not succeed).
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
         false resets the streak. Per-app timeouts stay the outer bound: each app must first be
         seen stable within its own timeout, and the step ends at the largest one plus 60 s.
       - Offline fixture tests for `apps-stable.jq`, run in `repo-checks`, same commit. Five
         cases: all apps at the expected SHA → true; one app at the old SHA → false; chart + Git
         multi-source → true; `revisions` shorter than `sources` → false; `repoURL` mismatch (for
         example a missing or extra `.git` suffix) → false.
    2. `set -e`.
    3. Step timestamps.
    4. Restart-count info line.
    5. `dependency-db-secrets.sh` + the bootstrap call. It prints `exists` or `created` per
       Secret, by name only, never values. That output is the M1-4 evidence that the three
       Secrets exist, since the agent's guard denies `kubectl get secret*`.
    6. `dependency-db` in both `EXPECTED_APPS` + a DB-pod-Ready check.
    7. **`verify-state.sh` Application retry window.**
       - Bounded wait: poll every 5 s, total bound `NEXUS_VERIFY_APPS_TIMEOUT`, default **660 s**.
       - Every poll is written to the report: UTC timestamp, each app's sync/health, expected vs
         observed revision(s), predicate result, current streak.
       - Pass only when the **same** `apps-stable.jq` predicate as commit 1 (revision check
         included) has held for 60 s of consecutive snapshots, never on the first success.
       - At the bound: FAIL, with the last snapshot.
       - **Default, derived as additive terms:** reconcile delay 180 s + rollout 420 s + stable
         window 60 s = **660 s**.
         - Reconcile delay, 180 s: ArgoCD v3.3.8 polls Git every `timeout.reconciliation` 120 s
           plus up to `timeout.reconciliation.jitter` 60 s. The live `argocd-cm` overrides
           neither. A GitHub webhook cannot shorten it, because `argocd-server` is `ClusterIP` with no
           Ingress (ADR-014).
         - Rollout, 420 s: the slowest M1 rollout is dependency-db's first start. Its startupProbe
           ceiling is 150 × 2 s = 300 s (change 24), plus a 120 s image-pull allowance for
           `postgres` 17.11, 161.3 MB compressed (implies ≥ 1.35 MB/s). Test d pulled it in 55.9 s
           through Docker Desktop, so 60 s was too tight; the node's pull is measured at M1-4. This dominates sample-api: 2 pods rolled one at a time (`maxSurge` 1,
           `maxUnavailable` 0), each ≤ 52 s startupProbe + 10 s readiness, about 124 s.
         - Stable window, 60 s: the commit-1 streak.
       - Not included: ArgoCD sync-retry backoff after a failed sync attempt (10 s doubling up to
         `maxDuration: 3m`), for example the AppProject/Application ordering race at M1-4. For
         that case, set a larger bound with the env var and record the value used in the report.
    - Also in this PR: ADR-020 states that the owner runs the change 24 recovery delete.
  - [x] Validation: `--plan` and the 6-scenario fault-injection rerun (2026-09-27, a throwaway
    worktree, removed with `git worktree remove`; only its copy had the k3s.service path override).
    - `--plan` with every stub, `k3s` included, exiting 1: exit 0, 25 steps printed, 0 stub calls.
    - The six #55 scenarios (`k3s_install`, `argocd_apply`, `grafana_secret`, `root_apply`,
      `killswitch_create`, `verify_state`) and two new ones (`dependency_db_secrets`,
      `apps_unstable`): each stops at its step with a named FATAL, exit 1, never "done".
    - Baseline, no fault: steps a–h complete (step h stable for 60 s after 61 s); the one FATAL is
      the real `verify-state.sh`, whose M1 passed after 61 s (13 polls) against the stub cluster.
    - `apps-stable.jq`: the five fixture cases pass, and the live cluster evaluates true.
  - [x] PR merged (#72, merge `5d5120e`, 2026-09-27). **GATE M1-3.**
- **M1-4, M1-5 and M1-6 — permission mode.** The owner selects Manual mode. Each session starts
  with the prompt test: the agent runs `gh api repos/koussayx8/nexus-platform --jq .full_name`,
  which matches the `ask` rule `Bash(gh api *)`, and the owner confirms whether they were
  prompted before it ran. The owner seeing the prompt is the check, not the agent's
  self-report. If there was no prompt, the session stops. (At the M1-4 start, the first test in
  Manual mode ran without a prompt because of the untracked local allow list; see Later. After
  its removal, the rerun prompted.) **Ended 2026-09-28 (owner):** the local allow list regrew
  three times, and the owner chose prompts at their discretion over added tooling. Since then:
  prompts (Allow once / Always allow at the owner's discretion), bypass mode for tasks the owner
  picks, the deny list as the floor, typed approval for every merge; each session reports
  `.claude/settings.local.json` after the prompt test, and neither stops the session.
- **M1-4 — DB live.**
  - [x] The owner runs `dependency-db-secrets.sh` on the live cluster: `generated:` ×3 and
    `created:` ×3 (`nexus-data/dependency-db`, `nexus-dev/dependency-db-app`,
    `nexus-prod/dependency-db-app`).
  - `verify-state.sh` after the gate merge runs with **`NEXUS_VERIFY_APPS_TIMEOUT=820`**, recorded
    in the report. Derivation (additive terms):
    - Reconcile delay, 180 s: as in M1-3 commit 7.
    - Retry backoff, 160 s: `root` (creates the `dependency-db` Application) and `platform` (adds
      `apps/StatefulSet` to the AppProject) poll Git independently, so `dependency-db` can try to
      sync up to 180 s before the AppProject allows a StatefulSet. With the live retry policy on
      every Application (`limit: 10`, backoff 10 s × 2, `maxDuration: 3m`), sync attempts start at
      +0, 10, 30, 70, 150 and 310 s after the first. The latest first attempt after the widening
      is when `root` picks up the merge 30 s after it and `platform` 180 s after it:
      30 + 310 = 340 s = 180 + 160.
    - Rollout, 420 s, and stable window, 60 s: as in M1-3 commit 7.
    - UNVERIFIED: that ArgoCD treats the project denial as a failed sync operation retried by this
      policy, rather than re-evaluating on the AppProject change (faster). The M1-4 report's poll
      log shows which.
  - `verify-state.sh` runs twice (decided at the M1-4 start):
    - Run 1, after the `main` merge, with `NEXUS_VERIFY_APPS_TIMEOUT=820`. If it fails, no
      forward-merge.
    - Run 2, after the forward-merge, with the default bound (660 s).
  - [x] `dev`→`main` gate PR #74 (merge `87e3ab3`, 2026-09-27T13:08:29Z). CI run 36321366504 signed
    `sample-api@sha256:8ea896c267d0842732e564e7c45b1606bc5347764bab7c7741e4d356c0e0e9af` (cosign
    v2.5.2, Rekor tlog index 2975125709).
  - [x] Verify run 1 passes: `NEXUS_VERIFY_APPS_TIMEOUT=820`, 13:08:47 → 13:18:11Z, exit 0, 9/9;
    M1 stable 64 s after 551 s (100 polls).
  - [x] `experiment/dev-state` forward-merge: local `--no-ff` merge pushed as `5d01ee3..9bfa5ff`.
  - [x] Verify run 2 passes: default bound 660 s, 13:30:41 → 13:35:21Z, exit 0, 9/9; M1 stable
    62 s after 263 s (49 polls). **GATE M1-4** — passed (owner, 2026-09-27).
  - Findings:
    - AppProject race did not occur: `platform` at `87e3ab3` 13:12:31Z, `root` 13:14:25Z;
      `dependency-db` synced once. Whether ArgoCD retries a project denial stays UNVERIFIED.
    - DB image pull on the node: **143.2 s** (161,346,986 bytes, ~1.13 MB/s), over the 120 s
      allowance. Init ~2.2 s; pod created → Ready 148 s.
    - Leak checks: both verify reports and the DB log, 0 hits.
- **M1-5 — `/items` live.**
  - Decisions at GATE M1-4 (owner, 2026-09-27):
    - **Pull allowance 120 → 300 s.** Derived values:
      - rollout = 300 startupProbe + 300 pull = **600 s**;
      - `verify-state.sh` default = 180 reconcile + 600 + 60 stable = **840 s**;
      - M1-4-style bound (+ 160 s retry backoff) = **1000 s**;
      - `bootstrap.sh` `dependency-db` wait = 160 + 600 + 60 = 820, rounded up for rebuild
        contention = **900 s** (today it falls back to `NEXUS_WAIT_TIMEOUT_DEFAULT` 600 s).
    - **Coupling rule (ADR-020):** rollout 600, verify default 840, M1-4-style bound 1000 and
      bootstrap DB wait 900 are all derived from the startupProbe budget and the pull allowance.
      Changing either means re-deriving all four.
    - Re-measure the pull at the M1 exit rebuild.
    - k3s Secrets encryption at rest: `Disabled` on the live cluster (owner's
      `sudo k3s secrets-encrypt status`); `bootstrap.sh` enables it from the M1 exit rebuild
      (ADR-019 addendum).
  - [x] Docs commit (`514a9e9`): this section, M1-4 ticked, the ADR-020 coupling rule, the
    ADR-019 addendum (owner's verbatim evidence added in `d90678d`).
  - [x] Scripts commit (`1797bb6`): `verify-state.sh` default 840 s; `bootstrap.sh` DB wait 900 s;
    `secrets-encryption: true` in `bootstrap.sh`'s k3s config. shellcheck v0.11.0: no warnings.
  - [x] One commit (`5ffe85f`): digest bump + DB env wiring (change 2). `cosign verify` (v2.5.2, as the
    sign job) with the exact identity `…/ci.yml@refs/heads/main` and issuer
    `https://token.actions.githubusercontent.com`: exit 0, workflow SHA `87e3ab3`.
  - [x] The `verify-state.sh` `/items` check (`63f66e2`, change 1), `NEXUS_VERIFY_ITEMS_NAMESPACES`;
    offline tests run by `repo-checks` (`999918e`). Deployment version label 0.2.0 (`34e4eac`).
  - [x] PR #75 → `dev` (merge `b652725`); gate PR #76 → `main` (merge `ee39cef`,
    2026-09-27T15:50:14Z); forward-merge `23d0b84` (local `--no-ff`, tree equal to `ee39cef`, pushed
    `9bfa5ff..23d0b84`). **GATE M1-5** — passed (owner, 2026-09-27).
    - Run 1 (`nexus-prod` only, 840 s): **failed**, 8/10, not caused by M1-5 (incident below).
      Rerun 16:23:18 → 16:24:39Z: exit 0, 10/10; M1 stable 60 s after 61 s. prod rolled one pod at
      a time to `8ea896c2` (15:54:37 → 15:55:56Z), restarts 0, `DB_USER=app_prod`; `/items` 200,
      rows=20; dev skipped.
    - Run 2 (both namespaces, 840 s): 16:27:32 → 16:33:43Z, exit 0, 10/10; M1 stable 61 s after
      356 s. dev rolled one pod at a time (16:32:03 → 16:32:33Z), restarts 0, `DB_USER=app_dev`;
      `/items` 200, rows=20 in both namespaces.
    - Every run: `dependency-db-0` uid `7fff600d…`, restartCount 0; 0 `db_error` in the new pods;
      leak checks 0; wall-clock vs `/proc/uptime` within 2.58 s (no VM pause).
  - Incident, run 1 (owner accepted it as not caused by M1-5): the owner's open Grafana dashboards,
    through a port-forward, drove the `grafana` container to its 200m CPU limit from 15:38–15:42Z
    (before the merge), throttled in 99 % of periods. Liveness kill 15:45:42Z; the readiness probe
    (1 s timeout) then failed 123×; working set 270 → 483 MiB (limit 512 MiB). `observability`
    flapped `Healthy`/`Progressing`, so M1 and M6 failed. The owner closed the tab and the
    port-forward; the rerun waited for 5 min of Grafana Ready + `observability` Healthy + CPU < 50m
    (306 s held) and a working set ≤ 450 MiB (207 MiB).
  - Incident, step 3: an unprompted live `verify-state.sh` run (guard-model Later item).
- **M1-6 — exit.**
  - **Finding at GATE M1-5 — ArgoCD pickup delay.** Measured merge/push → new revision: 242 and
    356 s (M1-4); 262–382 s and 315 s (M1-5); all above the 180 s reconcile term. Cause: the
    repo-server's Git reference cache (`--revision-cache-expiration`, default 3 min, not set live)
    in front of the controller's 120 + 60 s refresh, a worst case of about 360 s. Reconcile term
    re-derived to **480 s**: `verify-state.sh` default **1140 s**, M1-4-style bound **1300 s**,
    bootstrap DB wait unchanged at 900 s (ADR-020 addendum, GATE M1-5).
  - **WSL2 VM pause rule** (added at M1-4): a host sleep changes neither `boot_id` nor k3s's start
    time. The 24 h audit window (change 14) and every S5 run record wall-clock time and
    `/proc/uptime` at start and end; if the two deltas differ by more than 60 s, the VM was paused
    and the window or run is discarded. The owner disables Windows sleep during both.
    **M1-6 window:** the sleep setting was off and the VM paused anyway (2,921.9 s); the owner
    kept the window under D1, normalized per uptime second (ADR-019 addendum). The rule stands
    for S5 and verify runs; see the pause-cause Later item.
  - **Mount check** (added at GATE M1-6 b5): the S5 preconditions and the audit window's start and
    end records include `awk 'NF!=6' /proc/mounts`, which must print nothing. A 7-field line (Docker
    Desktop's `/Docker/host`) makes the kubelet exit on any k3s start.
  - [x] Minimal read-only allow list in `.claude/settings.json` (PR 1, GATE M1-6 plan review).
    Principle: nothing auto-allowed may read arbitrary files, reach arbitrary hosts, or write
    files. So only Git reads of repository objects: `git --no-optional-locks status` (plain
    `git status` may rewrite `.git/index`), `git log`, `git show`, `git rev-parse`,
    `git ls-remote origin`, `git worktree list`. New deny rules `Bash(*--output*)` (`git log` and
    `git show` write files with `--output`), `Bash(*--upload-pack*)` and `Bash(*--exec*)`
    (`--exec` is `ls-remote`'s alias for `--upload-pack`; with a local or SSH remote, either runs
    a command). No entry overlaps an ask rule; none allows `cat`,
    `python3`, `git merge` or `gh pr`. Left out: `git diff` (`--no-index` reads any file),
    `kubectl` (`--kubeconfig` reads any file, `--server` reaches any host), `bash -n` and
    `shellcheck` (both read any path and echo its lines). Each session reports
    `.claude/settings.local.json` after the prompt test and stops on a risky entry.
  - [x] Before the rebuild: the M1-4/M1-5 verify reports and poll logs archived to
    `~/nexus-evidence/m1-4` and `m1-5` (checksums match; the originals lived only in `/tmp`,
    which is emptied at boot); a pre-rebuild record; `/var/log/nexus-audit` archived to
    `~/nexus-evidence/m1-6/audit-pre-rebuild/` (8 source files byte-identical), because
    `bootstrap.sh` empties the active audit log (ADR-019 M1-6 addendum).
  - [x] From-empty rebuild (owner; `NEXUS_WAIT_TIMEOUT_OBSERVABILITY=1800`), scripts from a
    detached worktree at `dev` `0cfe6c7` (identical `bootstrap.sh` behaviour to `main`, and #77's
    1140 s verify default; its `docs/CURRENT_STATE.md` write stays out of the main checkout).
    - First attempt: FATAL at 03:57:38Z, "creating namespace monitoring failed". k3s was
      crash-looping: the kubelet exited with `system validation failed - wrong number of fields
      (expected 6, got 7)`, from Docker Desktop's `/Docker/host` 9p mount (enabled for test d,
      after the M0-5 rebuild). The owner uninstalled, unmounted it, and reran (Later: bootstrap
      preflight).
    - Second attempt: 04:05:40 → 04:34:05Z (28 min 25 s), `bootstrap: done`, exit 0. ArgoCD rollout
      5 min 13 s; step h stable after 1146 s; `observability` Healthy after about 1108 s (62 % of
      1800 s); `verify-state.sh` 10/10, M1 stable 65 s after 65 s. DB Secrets `created:` ×3 from
      the existing `~/.nexus` files. k3s `v1.34.6+k3s1`, `boot_id` unchanged.
    - DB image pull **377.3 s**, alongside 12 other pulls; init about 2 s; new uid
      `c8436079…`. Owner decision (a): keep the 300 s allowance, scoped to an uncontended pull;
      rebuild pulls fall under `bootstrap.sh`'s 900 s DB wait (390 s used). ADR-020 addendum.
  - [x] After the rebuild, the owner's `sudo k3s secrets-encrypt status`: `Encryption Status:
    Enabled`, all hashes match, AES-CBC (ADR-019 addendum; Later: secretbox).
  - [x] S5 smoke test on `app_dev` (2026-09-28, 05:45–05:47Z): dev `/items` 503
    `db_unavailable` in 12–17 ms (×5) with `FATAL:  role "app_dev" is not permitted to log in` in
    the log (`sqlstate=None`); dev `/ready` 200; prod `/items` 200, 20 rows, 0 `db_error`; all 7
    Applications Healthy and 0 restarts during the fault; reset → 200, 20 rows; DB uid and
    restartCount unchanged; \|Δwall − Δuptime\| 3.71 s. Evidence `~/nexus-evidence/m1-6/s5/`.
  - [x] 24 h audit window: start 2026-09-28T06:11:24.709Z, end 2026-09-29T06:11:54.837Z
    (Δwall 86,430.1 s). `boot_id`, k3s start and mounts unchanged; DB unchanged; 7/7 Healthy.
    VM pause 2,921.9 s (rule: 60 s), `boot_id` unchanged: kept by owner override D1,
    uptime-normalized (Δuptime 83,508.2 s). Owner, check 7: "no dashboards or port-forwards were
    open during the window. The sleep setting was off, but the VM paused anyway (about 2,922 s,
    still growing about 34 s/h); covered by D1." Growth 509,060,759 B: 502.3 MiB/day per uptime
    (485.3 per wall clock); **retention 2.19 days** (2.27), CONTRADICTING change 7's ~10 and
    agreeing with the 2.2–2.8 estimate. ADR-019 addendum.
  - [x] `CURRENT_STATE.md` from a `verify-state.sh` run after d2 (approval A8): 2026-09-29T06:57:46Z,
    10/10, exit 0, 77 s, wall vs uptime within 1.1 s. `CHANGELOG` `[0.2.0]` filled but for the tag
    date.
  - [x] Tag `v0.2.0` (A14, the owner's typed approval, 2026-09-29): annotated tag object `1be13f1`
    → `ce2b174` (merge of #81). Verify runs after the gate merge: A11 10/10 (pause gap 5.09 s),
    A13 10/10 after the forward-merge `801043e` (3.80 s); no pod rolled. **GATE M1 exit passed.**

## M1b — the rest of what the spec and TASKS tagged M1 (after M1, before M2)

- The spec §3 Z-score recording rules and the four anomaly alerts, replacing the interim
  `SampleAPIHighErrorRate`.
- The `nexus` Application with the operator (§3, §25).
- sample-api fault hooks `/fault/hang`, `/fault/inject-logs`, `/work/cpu` and `NEXUS_FAULTS_ENABLED`.
- The §27 M1 verifications: the Kopf status-persistence/standalone spike; kube-state-metrics series
  and the Alertmanager v2 API; CEL transition rules; the Locust capacity ramp and baseline
  (this includes the change-16 dependency-db thresholds).
- dependency-db CPU throttling, measured at M1-4 (2026-09-27 13:29Z, no change now): the three
  cAdvisor series exist for `{namespace="nexus-data",container="dependency-db"}` (job `kubelet`,
  one series each). Idle DB (limits `cpu: 500m`, `memory: 512Mi`): throttled 139.5 / 608.0 periods
  (~23 %) over 10 min after init; working-set peak 48.7 MB over 15 min. CFS counts only active
  periods, and an idle DB is active mostly for probe bursts (`sh` + `pg_isready` + a forked
  backend; readiness every 5 s, liveness every 10 s). **M1b measures throttling under load, or
  uses throttled seconds, before any decision on limits.**

**Plan:** `~/.claude/plans/m1b-planning-plan-only-abundant-globe.md` (owner's local file), approved
with the owner's gate changes on 2026-09-28. Rules for every phase: its §2. Branches reach `dev` by
PR with typed approval, bringing `dev` in by a merge commit, never a rebase.
**Merge order into `dev` (gate position, 2026-09-29; confirmed by Koussay, typed, 2026-09-30):** #82 →
`test/m1b-6-envtest` → `test/m1b-6c-detection-inputs` → `feat/m1b-6a-incident-crd` (the
`Prune=false,Delete=false` annotation committed on its branch first) → `feat/m1b-6b-kopf-spike` →
`feat/m1b-5-fault-hooks` → the M1b-3 branch and #79 together, for the observability gate. #79
conflicts with 6c in `.github/scripts/repo-checks.sh` (both add a step 7); fixed on #79 by
merging `dev` in.
**Exit criterion (owner):** an end-to-end demo under baseline load: S5 injected on `app_dev`,
`NexusErrorRateAnomaly` fires for `nexus-dev`, and an Incident reaches `Recorded`
(`level_observe`, L0); then the reset, DB uid and restartCount unchanged. The demo runs the
detection rules pinned at #79's head `d351d964f2a50ffd07916e69f30e6c067e567cb9`: the 5
rule-defining files, `platform/observability/alerts/kustomization.yaml`, `nexus-detection.yaml` and
`sample-api-error-rate.yaml` (deleted there), `platform/observability/tests/nexus-detection.test.yaml`
and `scripts/tests/promtool-rules.sh`. The S5 record names the full graded SHA (the `main` commit
the demo ran on) and the values in effect, read live: the sample-api scrape interval
(ServiceMonitor, 15 s in Git), Prometheus's rule evaluation interval and Alertmanager's
`group_wait` (neither is set in Git: chart defaults). A change to those files
(`git diff d351d964f2a50ffd07916e69f30e6c067e567cb9 <graded SHA> -- <the 5 paths>` non-empty) or
to the recorded values means a rerun;
`CURRENT_STATE.md`,
`CHANGELOG` `[0.3.0]`, tag (separate approval). **GATE M1b exit.**
- [ ] After S5 is graded: remove or re-pin the S5 pin guard (`repo-checks.sh` step 9) through an
  ADR (plan M1b-8 rev 3, ADR-025).
- [ ] S5 pre-check (ADR-025): `nexus-dev`'s level is "0" in `overlays/dev/namespace.yaml` at the
  dev-state SHA and live, and `sample-api-dev` is Synced at that SHA; all three in the S5 record.

- **M1b-0 — guard ADR, settings, task list** (branch `docs/m1b-0-guard-model`).
  - [x] ADR-021: the guard findings, the heavier design rejected for proportionality, the model
    we run, and that only a typed message renews the pasted-reply rule.
  - [x] `.claude/settings.json`: `git commit`, `git switch` and `git branch` move from ask to
    allow; `git checkout` is denied; the destructive forms of push, branch, switch and commit are
    denied (best-effort, ADR-021). `git push`, `git tag` and `gh pr merge` stay in ask.
  - [x] Merge and tag procedure (ADR-021, CLAUDE.md rule 11):
    `gh pr merge <N> --merge --match-head-commit <approved full SHA>`;
    `git tag <name> <approved full SHA>`, then push only that tag.
  - [x] ADR-020 addendum: the M1 exit pickups widen the ArgoCD range to 162–382 s.
  - [x] This M1b list and the queued Later items.
  - **#79 merge condition 2 (owner):** #79 deletes
    `platform/observability/alerts/sample-api-error-rate.yaml` (the interim
    `SampleAPIHighErrorRate` rule, commit `81ead2f` on `feat/m1b-7-detection`), replaced by
    `nexus-detection.yaml`. Listed here before #79 merges.
  - **The permissions list is frozen** (owner, round 5): from here, only probe failures change it.
  - [x] Pattern probe (owner, before the merge): a fresh default-mode session in the M1b-0
    worktree, throwaway branches only, pushes with `--dry-run`; commands and results table in
    `~/nexus-handoff-m1b.md`. Result (reported 2026-09-30, Manual mode): 12 of 13 rows pass; row 6
    fails, `git push` ran without a prompt (cause unknown), so ask prompts are no longer counted
    as a backstop (ADR-021). #82 merged by the owner at head `2c72e0e` (merge `e59e535`).
    **GATE M1b-0.**
- **M1b-1 (Guard A) and M1b-2 (Guard B) — removed** (owner, 2026-09-28; ADR-021).
- [x] **M1b-3 — Grafana limits** (one observability gate with M1b-7): resources and readiness timeout
  in `kube-prometheus-stack-values.yaml`, after a node headroom read; ADR-016 addendum; acceptance
  with one dashboard open for 15 min, then a verify run with none.
  **Done** (#93 `f923b95`; gate #94, M3 `c05d91b`, 2026-10-01; forward-merge `38d37bd`,
  2026-10-02): CPU 1000m limit / 200m request, memory 1Gi limit, readiness timeout 5 s; only the
  Grafana Deployment rendered differently, so Prometheus did not restart. Acceptance (rerun
  2026-10-02 06:50–07:05Z, one dashboard open, 420 panel queries): throttled 0.11 %, peak working
  set 580 MiB (57 % of 1Gi), 0 restarts, 0 failed probes, `observability` Healthy at every poll,
  `nexus-detection` 0 missed evaluations (≤ 3.5 ms); then verify 10/10 with no dashboard.
  Evidence `~/nexus-evidence/m1b-gate3/`.
- **M1b-4 — ArgoCD refresh after runner commits:** the "applied at" definition and, only if
  cheap, the offline jq filter with fixtures. The live tests (refresh annotation vs selfHeal,
  commit → applied timing) move to the runner milestone.
- **M1b-5 — fault hooks and rollout strategy** (branch `feat/m1b-5-fault-hooks` `39eefec`, no PR):
  sample-api 0.3.0 behind `NEXUS_FAULTS_ENABLED`, `/fault/hang` (a real deadlock, not bounded),
  `/work/cpu`, `/fault/inject-logs`; async `/metrics`, in-flight gauge, finer buckets; blinding
  (D2); hash-pinned runtime set (D4); `RollingUpdate` `maxSurge: 1`, `maxUnavailable: 0` (D3);
  ADR-022. Two-merge pattern: code → `main` builds and signs, then the digest bump on path L; a
  fault smoke on `nexus-dev` only.
- **M1b-6 — §27 spikes on envtest:** harness `test/m1b-6-envtest` `b9f62f0`; 6a Incident CRD with
  C1–C4 (`feat/m1b-6a-incident-crd` `8706587`, stacked on the harness); 6b Kopf spike
  (`feat/m1b-6b-kopf-spike` `4091baa`, stacked on 6a, ADR-023); 6c offline checker for the KSM and
  Alertmanager v2 reads (`test/m1b-6c-detection-inputs` `75b5743`). Live after merge: the CRD
  through `platform`, one rejected spec patch, the 6c reads through the service proxy.
  **6a exit criterion (owner, M1b-0 gate):** the CRD carries
  `argocd.argoproj.io/sync-options: Prune=false,Delete=false`, committed on
  `feat/m1b-6a-incident-crd` before that branch merges. Deleting a CRD deletes every Incident,
  and `platform` syncs with `prune: true`. `8706587` does not have it yet.
- [x] **M1b-7 — Z-score rules and the four anomaly alerts** (#79, `feat/m1b-7-detection`
  `d351d96`): lagged baseline (`[15m] offset 3m`, 27-sample guard), promtool tests in
  `repo-checks`, ADR-024. Live: rule health `ok`, series present, no alert at idle.
  **Done** (#79 `9607344`, `dev` brought in by merge with the step-7 conflict resolved: 6c step 7,
  promtool step 8; the five pinned rule files equal `d351d964`; gate #94, M3 `c05d91b`): rules
  loaded 2026-10-01T13:41:39Z without a Prometheus restart, 24/24 `health: ok`,
  `SampleAPIHighErrorRate` pruned; after the ~16.5 min warm-up no Nexus alert at idle (CPU
  Z ≈ 0.007). **At idle only the CPU signal has data**: `/` and `/items` have no series without
  business traffic, so requests, error ratio, in-flight and p95 (and their baselines) stay empty
  until M1b-9's baseline load, which the S5 exit demo therefore needs first.
- **M1b-8 — the `nexus` Application and the operator skeleton:** staged §11 RBAC (Incidents create
  and status, the M1b reads; no `deployments/scale` or `pods/eviction` until M2), Alert Poller and
  a 5 s status-only loop (single writer), L0 → `Recorded`, L1–L3 → `Escalated` after 20 s. The loop
  never moves an Incident to a terminal phase while Kopf progress is pending, and every Kopf
  handler has a timeout that bounds that wait (ADR-023). `nexus-operator-config` gets its real
  schema (`advisoryChecks: on`, `approvalTTL: 15m`); ADR-025.
  **Base (gate position, 2026-09-29; confirmed by Koussay, typed, 2026-09-30):** the 6b spike. M1b-8 moves its loop,
  `decide()` and `reconcile()` into `operator/` with unit tests, and turns `run-spike.sh` into the
  operator's envtest integration test.
  **M1b-8 exit criterion (owner):** `operator/spikes/kopf-status/` is deleted; `_race_hold` and
  the envtest token login exist only in tests.
  RBAC checks use `kubectl auth can-i --list` (the `kubectl * create *` deny would catch a
  per-verb `auth can-i create …`).
  Plan: `~/nexus-m1b8-plan.md` revision 3 (approved by Koussay, typed, 2026-10-02).
  - [x] PR A (code, #96 → `dev` `13a2bd0`, gate #97 → `main` `3ea41f8`; image
    `sha256:ef3c6955…083b53` signed and `cosign verify`-ed; verify run 10/10): `operator/nexus_operator/` (Alert Poller with
    episode dedupe, reconcile loop, intake, liveness), unit tests, the envtest integration test
    `operator/tests/envtest/` (P1–P6, episodes, smoke, absorb), `platform/rbac/` (not yet listed
    in `platform/kustomization.yaml`), `operator.yml`, the S5 pin guard, ADR-025; the spike
    deleted.
  - [x] PR B (deploy; #99 → `dev`, gate #102 → `main` `bba0646`; RBAC wait proven by
    `operator/tests/envtest/run-rbac-late.sh`): `operator/k8s/`, `rbac` in the
    `platform` kustomization, the `nexus` Application, verify/bootstrap checks, the
    `nexus-operator-config` template (owner applied it live). The first deploy crash-looped
    (#100: image UID 10001 has no passwd entry); fixed by #101 (`USER` env).
  - [x] Live acceptance (plan §3), 2026-10-06; evidence `~/nexus-evidence/m1b-8/` (0600, not in Git):
    `verify-state.sh` 12/12 (`verify-state-3.md`), operator pod 0 restarts, `boot_id` unchanged,
    0 Incidents at idle; smoke (`smoke.txt`, marked as smoke, not detections): 5 episodes, 10
    Incidents, dev L0 `Recorded`/`level_observe`, prod L1 `Escalated`/`evidence_error` in 20–22 s
    (window 20–28 s); audit (`operator-audit-smoke-2.jsonl`): 10 `create` and 30 `incidents/status`
    patches, 0 403s, 0 writes elsewhere; the owner deleted the smoke Incidents, count 0, none recreated.
    - **Not performed:** the in-window repeat post (same episode, before `endsAt`); both attempts
      landed after `endsAt` and made new episodes. Dedupe is evidenced indirectly: each episode
      polled about 12 times with one Incident, Prometheus re-sends keep `startsAt`, envtest and unit tests.
    - **Moved to M1b-9:** the `startsAt` versus Prometheus `activeAt` check on a real firing alert
      (ADR-025 UNVERIFIED stays open until then).
  - **Problems catalogue:** envtest ran Kopf under a real local user, so it missed that image UID
    10001 has no passwd entry (the #100 crash loop). Test the identity the image actually runs as.
- **M1b-9 — Locust calibration:** R1 baseline mix on 2 replicas covers `/` and `/items`;
  `/work/cpu` stays out or minimal and constant (ADR-026); R2 `/work/cpu` on 1 pod; baseline 0.4 ×
  R1 capacity; a 60-min clean baseline with zero Nexus alerts; the change-16 DB criteria; S5
  first-fire time re-measured (+105 s in promtool at a 20 % error share).

## Later — out of scope for M1b

- **High priority, before any MTTR measurement in M1b** (owner, #77 review, 2026-09-27): ArgoCD
  pickup takes about 1.8 to 6.4 minutes (the repo-server's revision cache plus the controller's
  refresh; measured 108–382 s, ADR-020 addenda). If NEXUS repairs through Git commits, this dominates
  measured recovery time. Decide how NEXUS triggers ArgoCD: an operator refresh after committing,
  or shorter cache and refresh timeouts.
  - **Correction** (M1b plan gate, 2026-09-28): the premise contradicts the spec. NEXUS never
    writes Git: the operator mutates only through the Scale and Eviction subresources, and
    desired-state fixes escalate to a human (§13, AD-12). Git commits come from the Experiment
    Runner (S3 inject, `experiment/dev-state` reset) and from humans (escalated fixes, C5), so the
    pickup delay affects injection timing, reset time and human-fix recovery, not NEXUS's own
    act phase. **Decision (owner):** the runner hard-refreshes the Application after each commit
    it makes, and a run's injection time is when ArgoCD applied the revision, not the commit time.
    `verify-state.sh` stays read-only. Still open: whether selfHeal touches the refresh annotation
    (one approved live test, moved from M1b-4 to the runner milestone).
- **Rejected (owner, 2026-09-28; ADR-021): no agent kubeconfig, now or later.** Was: high
  priority, before M1b (owner, #78 review, 2026-09-27): pattern rules cannot protect
  Secrets. `kubectl get --raw .../secrets/...` and `kubectl get -n x secrets` both bypass the
  `Bash(kubectl get secret*)` deny. Fix it at the identity layer: a dedicated agent kubeconfig
  with RBAC read access to everything except Secrets and no write verbs. Once it exists,
  `kubectl` reads can be auto-allowed safely.
- Pin `sigstore/cosign-installer` by commit SHA in `ci.yml`, with an explicit `cosign-release`.
  Today `@v3` is a moving tag; the last sign job (run 36227651665) got `398d4b0` and cosign
  v2.5.2. Until it is pinned, at M1-5 check which cosign version the sign job used before running
  `cosign verify`.
- **Base images: digest pins and a slimmer base** (owner, #96 gate review, 2026-10-03). The
  operator image pins `python:3.12-slim` by index digest (`operator/Dockerfile`, `sha256:dddfd7e0…`);
  sample-api's does not (`apps/sample-api/Dockerfile`: `python:3.12-slim`, a moving tag). Trivy on
  the #96 PR image: 45 HIGH, 0 CRITICAL, all Debian 13.7 base packages, 0 in the Python packages.
  Pin sample-api's base by digest, and move both images to a slimmer base (e.g. distroless or a
  minimal Python runtime), then compare the Trivy counts. `operator.yml` copies `ci.yml`'s
  `cosign-installer@v3`, so the item above applies to it too.
- **Operator image: a passwd entry for UID 10001, then drop the USER workaround** (owner, #100 stop,
  2026-10-06). Image `ef3c6955` runs as UID 10001 with no `/etc/passwd` entry; Kopf's
  `getpass.getuser()` raised `KeyError` and the pod crash-looped at the #100 live gate.
  `operator/k8s/deployment.yaml` now sets `USER=nexus-operator` (guarded by
  `operator/tests/unit/test_runtime_user.py`). At the next image rebuild (with the base-image item
  above): `useradd --uid 10001` in the Dockerfile, new `cosign verify` and digest pin, then remove
  the env and turn the manifest test into a check that the image resolves UID 10001.
- **Gate rule until the Grafana limits are fixed (M1b-3):** no Grafana dashboards open (no Grafana
  port-forward) during `verify-state.sh` runs. Added at GATE M1-5 after the run 1 incident.
  **Retired for verify runs only** (owner, observability gate, 2026-10-02, after the M1b-3
  acceptance pass). **Dashboards stay closed during timed runs: S5 and the M1b-9 calibration.**
- Grafana's sidecar containers `grafana-sc-dashboard` and `grafana-sc-datasources` render with no
  `resources` (chart 86.2.2, seen in the M1b-3 render). Set requests and limits under
  `grafana.sidecar.resources` once their usage is measured (ADR-016 M1b-3 addendum).
- Grafana starves under an open dashboard: `grafana` container limits `cpu: 200m`,
  `memory: 512Mi`, readiness probe `timeoutSeconds: 1` (M1-5 run 1: 99 % throttled, 483 MiB, 123
  readiness failures, one liveness kill). Fix in `platform/observability/kube-prometheus-stack-values.yaml`
  **before the M1b calibration**. **Done: M1b-3** (#93, live at M3 `c05d91b`; acceptance passed
  2026-10-02).
- Set the sample-api rollout strategy explicitly before any scaling (M1b-5, `fc2255b` on its
  branch: `maxSurge: 1`, `maxUnavailable: 0`). Today it is the default
  `maxSurge: 25%` / `maxUnavailable: 25%`, which rounds to 1 / 0 only at 2 replicas; at 4 or more
  replicas `maxUnavailable` becomes ≥ 1.
- **Done: ADR-021** (M1b-0) records the guard model; the heavier design is rejected there. Was:
  write up the agent guard model: the `.claude/settings.json` deny and ask rules, how they
  behave in bypass and default permission modes, and the script gap (the rules match only the
  command typed, not what a script calls; `verify-state.sh` runs `kubectl port-forward`,
  `kubectl create --dry-run=server` and `rm -rf` internally). Second instance, M1-5 step 3
  (2026-09-27, Manual mode): an argument-validation test ran
  `NEXUS_VERIFY_ITEMS_NAMESPACES=nexus-prod ./scripts/verify-state.sh --out /dev/null`; the value was
  valid, so the whole script ran against the live cluster, and its `kubectl port-forward` (an
  `ask` rule when typed) ran without a prompt, as did the fetch and the dry-run create. Exit 1,
  output discarded; nothing persisted. Owner accepted it as harmless; rule since: offline tests
  set `KUBECONFIG=/nonexistent` and put a stub `kubectl` first on `PATH`. Include the branches:
  `experiment/dev-state` accepts direct pushes (its ruleset 23998158 blocks only force-push and
  deletion; no required check, no pull request), so from M1-4 on the default-mode rule is its only
  guard against an agent push. `main` and `dev` require a pull request and `repo-checks`.
  Include the Secrets finding (#78 review): pattern rules cannot protect Secrets
  (`kubectl get --raw .../secrets/...` and `kubectl get -n x secrets` bypass
  `Bash(kubectl get secret*)`); the fix is the agent kubeconfig at the identity layer (Later,
  high priority before M1b). **Finding 4, CONTRADICTED (M1b, 2026-09-28):** the Claude Code docs
  (permissions, settings precedence) say an `ask` rule outranks a local `allow`; observed the
  opposite: with the project `ask` `Bash(kubectl exec *)` and a local allow, no prompt appeared.
  Cause UNKNOWN; no test planned.
- The M1-2 permission audit read only deny/ask. The untracked local allow list (54 entries,
  including `gh api *`, `gh pr *` and `python3 -`) silently overrode the ask rules. Evidence:
  `~/nexus-evidence/settings.local.json.bak` (removed from the repo at the M1-4 start,
  2026-09-27). It regrew by the M1-6 start (`python3 -`, `cat >> *`, `gh pr *`, `git merge *`,
  `kubectl get *`); the owner moved it to `~/nexus-evidence/settings.local.json.m1-6.bak`
  (2026-09-27). **Done in M1-6:** a minimal read-only allow list is tracked in
  `.claude/settings.json` (see M1-6). **Third regrowth (M1b, 2026-09-28):** the owner clicked
  "don't ask again" on purpose, to let the agent work while away; the file was written at
  05:45:38Z, 13 s after the first S5 `kubectl exec`, and the S5 inject and reset then ran without
  prompts (the S5 result stands). Moved to `~/nexus-evidence/settings.local.json.m1b.bak`.
- No CI job runs shellcheck: neither `repo-checks` nor `ci.yml` checks
  `apps/dependency-db/10-roles.sh` or `scripts/*.sh`. It was run by hand (clean) for #70. Add a
  shellcheck step to `repo-checks`.
- sample-api runtime requirements use `>=`, so a signed image's contents depend on the build day
  (the psycopg tested in M1-1, 3.3.6, may differ from what M1-4 builds). Consider a lock file with
  hashes. **Covered by `036e7aa`** on `feat/m1b-5-fault-hooks` (hash-pinned runtime closure,
  `--require-hashes`, ADR-022); closed when M1b-5 merges.
- ruff's first-party detection depends on the working directory: `ruff check .` inside
  `apps/sample-api` and `ruff check apps/sample-api/` from the root (as CI runs it) disagree on
  import order. Set `src` / `known-first-party` so local runs match CI.
- `apps/sample-api/requirements-dev.txt` (from #88, `036e7aa`): lines 9–12 keep the old
  `httpx>=0.27.0`, `pytest>=8.3.0`, `pytest-asyncio>=0.24.0` lines beside the `==` pins above them.
  Harmless (the `==` pins win; dev only, never in the image). Remove the leftover lines (owner,
  #88 gate review, 2026-09-30).
- sample-api tests: Starlette warns `StarletteDeprecationWarning: Using httpx with
  starlette.testclient is deprecated; install httpx2 instead` (seen in the M1-1 pytest run, #69).
  Move the test client off `httpx` before Starlette drops support for it. Still seen with
  Starlette 1.7 on `feat/m1b-5-fault-hooks`.
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
  (change 7 assumed about 10 days; the M1-6 window measured 2.19 at idle, ADR-019 addendum).
- Raise the audit-log `maxbackup` (G2, owner decision at the M1-6 plan gate). The old cluster
  filled a 100 MiB file every 4.8–6.0 h, about 2.2–2.8 days of retention with `maxbackup=10`,
  not the ~10 days of change 7. **Measured in the M1-6 window:** about 500 MiB per idle day
  (502.3 MiB/day per uptime second), retention 2.19 days (ADR-019 addendum). 7 days needs
  `maxbackup` of about 35, more under load. Or narrow the §14 policy. Changing it needs a k3s
  restart: do it at a planned k3s restart (a rebuild or between measurement windows).
- ~~**Before M1b-9: find the WSL2 VM pause cause**~~ **Closed (owner, 2026-10-02).** The M1-6 window
  paused 2,921.9 s with Windows sleep off, growing about 25–34 s/h, `boot_id` unchanged. **Cause
  (owner):** Koussay put the PC to sleep and shut it down several times; that accounts for the
  pauses and the five reboots between 2026-09-29 and 2026-10-02 (the observability gate's 113.3 s
  pause stopped its first run). With the PC kept awake, the gate rerun stayed at 19.0 s over about
  23 min. **Rule from now on:** every timed run (S5, M1b-9) records its own pause gap (wall clock
  vs `/proc/uptime`, and `boot_id`) at start and end; M1b-9's plan sets when a run is discarded.
- `AlertmanagerClusterCrashlooping` (kube-prometheus-stack rule) has fired since the 2026-10-01
  12:52:46Z reboot and kept firing across later reboots, while Alertmanager stayed Ready with
  clean (`Completed`) last terminations. **Hypothesis (owner):** clock jumps after VM pauses shift
  `process_start_time_seconds`, so `changes(process_start_time_seconds{job="alertmanager"}[10m])`
  counts restarts that did not happen. Settle read-only with that query and the series' raw
  samples around a pause; then decide whether the rule needs a guard or is accepted noise on this
  host. Not a Nexus alert; not a gate stop condition.
- k3s Secrets encryption uses the AES-CBC default (M1-6 b6). The Kubernetes documentation
  prefers secretbox or a KMS provider: consider k3s's secretbox provider in `bootstrap.sh`'s k3s
  config.
- `bootstrap.sh` hardening, from the first M1-6 bootstrap (FATAL 03:57:38Z): (1) a preflight that
  fails before the k3s install if any `/proc/mounts` line has other than 6 fields, naming the
  mount — Docker Desktop's WSL-integration mount `/Docker/host` has an unescaped space in its
  `path=` option, and the kubelet then exits with `system validation failed - wrong number of
  fields (expected 6, got 7)`; (2) after the install, wait for `/readyz` and check that `k3s` is
  still active about 30 s later, so the FATAL names k3s instead of "creating namespace monitoring
  failed". Until then, keep Docker Desktop closed (or its WSL integration off) whenever k3s may
  start.
- The WSL VM rebooted at 2026-09-26 20:57Z (`journalctl --list-boots`). All four sample-api
  containers show last state `Unknown`, exit 255, at 20:59:58Z. The cluster recovered on its own
  and the node IP was unchanged. Informational. Relevant to the run pre-checks and the discard
  rules once experiments start.
- `verify-state.sh` gains checks per milestone: K1–K6 in Enforce, the Incident CRD and CEL, operator and Reasoner Ready, N1–N6.
- M3: WSL2 changes the node IP on restart, so NetworkPolicies template it at bootstrap and never hardcode `172.19.233.100`. N1–N6.
- M3: CODEOWNERS on `platform/policies/`, `platform/rbac/` and the Action Catalogue (§19), once those paths exist.
- M3: Kyverno `verifyImages` (Audit first) for the signing identity recorded in ADR-017 (SHOULD, §19).
- A deliberate CI job that runs `scripts/tests/incident-crd.sh` on the envtest harness (owner, 6a
  gate, 2026-09-28).
- The envtest harness PKI (7-day certificates) in `~/nexus-envtest` expires 2026-10-05T21:12Z.
  `test/m1b-6-envtest` `b9f62f0` renews it at `up`; 6a and 6b still carry the older harness. Merge
  the harness forward into 6a/6b only if a rerun after that date is needed (owner).
- The §3 kube-state-metrics alerts `NexusRolloutStuck` and `NexusCrashLooping`, and Alertmanager
  `group_by: [nexus_target]` (M1b-7 plan; `TASKS.md` M1b lists only the four anomaly alerts).
- M2: the operator validates approval content itself until Kyverno K5 lands in M3 (owner, 6a
  gate).
- M2: an alert that clears is not a recovery; repair verification compares the raw signal with
  its pre-fault baseline (M1b-7 gate; ADR-024 on `feat/m1b-7-detection` (#79), not yet on `dev`).
- M2: F5, SIGTERM with hung requests needs SIGKILL after the 30 s grace on eviction (ADR-022 on `feat/m1b-5-fault-hooks`, not yet on `dev`).
- M4: the runner's pre-check uses `baseline_stddev15m`; with the 3 min lag a clean baseline needs
  15 + 3 + 2 = 20 min after a fault ends, the whole §25 budget (ADR-024 on `feat/m1b-7-detection` (#79), not yet on `dev`). Measure it in M4.
- Spec v1.1 notes (M1b-5, M1b-7): the Z-score baseline excludes the most recent 3 minutes; the
  latency signal includes the in-flight gauge (p95 Z > 3 OR in-flight Z > 3). ADR-022 and ADR-024 are on `feat/m1b-5-fault-hooks` and
  `feat/m1b-7-detection` (#79), not yet on `dev`.
- Spec v1.1 also records the Application name `observability` (spec §3 says `monitoring`, ADR-016).
- `platform/argocd/configs/argocd-cm-patch.yaml` still configures Crossplane exclusions; M0-5 decides what `bootstrap.sh` applies.
- CI per §19: Trivy scans the pushed digest, not `:latest`; add a digest-bump PR step.
- CI per §19: "Dependabot opens weekly pull requests into `dev`". That needs a `dependabot.yml` with `target-branch: dev`. Today only security updates run, against `main`.
- Spec v1.1 (ADR plus version bump): the `experiment/dev-state` sequencing and forward-commit reset (§13, ADR-013), the branch ruleset (§19, ADR-013), and the unfiltered required check (§19, ADR-012).
- `repo-checks`: on a push that creates a branch, the range falls back to `-1 <sha>`. For a merge commit that scans 0 commits (seen when `dev` was created); the tree scan still ran. Make that path scan `origin/main..<sha>`, or accept it.
- Docs pass: `README.md` still describes Backstage, Crossplane and the old autonomy ladder. `docs/NEXUS_STATUS.md` and `docs/CUT_LIST.md` are OpenCode-era; decide whether to rewrite or archive them.
- Local only: about 1.9 GB of ignored Backstage build output remains in `platform/backstage/` (`node_modules`, `dist`, Yarn state). Delete it whenever you like.
- **Fresh bootstrap: operator config ordering** (M1b-8 PR B, ADR-025 addendum). `bootstrap.sh` step g
  creates `nexus-operator-config` after `root` has created the `nexus` Application; image
  `ef3c6955` treats a missing ConfigMap as permanent, so on a fresh bootstrap the operator pod can
  restart until step g runs, then recovers. Fix in either way: retry a 404 like a 403 in the next
  operator image, or create the two ConfigMaps before `root` (needs `nexus-system` first). Not a
  live-gate issue: the ConfigMap exists before PR B merges.
