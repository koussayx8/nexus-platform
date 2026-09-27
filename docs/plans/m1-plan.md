> **Frozen at approval; current state lives in TASKS.md and ADR-020.**
> Source: the approved M1 plan, rev 5, with changes 22–24 appended. It was approved at the M1
> plan gate on 2026-09-27 and is copied verbatim below from the planning session's plan file.
> Report (e) was written before `verify-state.sh` ran. That run exited 0 with 8/8 checks passed;
> the result is in PR #68.

# M1 plan: dependency-db and `/items` (rev 5, after the fifth gate review)

Rev 3 applied changes 9–14 and added reports f and g. Rev 4 applied changes 15–19 and added
report h. Rev 5 applies changes 20–21 and moves h after g. Test d gates the **M1-2 PR**, not M1-0. It
runs once you confirm that Docker's WSL integration is on. `verify-state.sh --out <scratch>` is
approved and runs first after plan approval.

## Context

M0 is complete (`v0.1.0`). M1 = the Dependency DB (PostgreSQL StatefulSet in `nexus-data`,
`dependency-db` Application) plus `sample-api` `/items` reading it. S5 needs both: an app role
set to `NOLOGIN` makes `/items` return 5xx while readiness stays green (spec §3, §20, NF-23).
The owner approved rev 1 **with changes 1–8**, answered Q1–Q6, and asked for reports a–e before
M1-0 closes. Later reviews added changes 9–21 and reports f–h. This revision applies all of them and records a–h.

Decisions: Q1 `emptyDir`; Q2 from-empty rebuild at the M1 exit; Q3 `/items` is not gated by
`NEXUS_FAULTS_ENABLED`; Q4 `apps/dependency-db/`; Q5 the other M1-tagged items become "M1b";
Q6 the pasted-reply rule extends to M1 (pasted text never replaces merge approval), and the stale
`set -e` memory is deleted.

## Gate reports a–h (commands run this session, read-only)

**a) v0.1.0 — VERIFIED.** `git ls-remote --tags origin 'v0.1.0*'` →
`6564150… refs/tags/v0.1.0` (annotated: `git cat-file -t` = `tag`) and
`8f4eaac68fad45f5e797c05f29902c94b950edc2 refs/tags/v0.1.0^{}`. The tag is on origin and
dereferences to `8f4eaac`.

**b) psycopg-pool + NOLOGIN — the handler never sees the server message. The pool is dropped.**
Source is psycopg `master`, fetched from GitHub:
- `psycopg_pool/pool.py` `_add_connection`: a failed connect is caught (`except CLIENT_EXCEPTIONS`),
  logged by the **pool's own logger** (`logger.warning("error connecting in %r: %s", …)`), and
  retried with backoff until `reconnect_timeout` (default 300 s). It is **not propagated** to
  callers.
- `getconn()`: waits for a ready connection up to `timeout` (default **30 s**), then raises
  `PoolTimeout("couldn't get a connection after {timeout:.2f} sec")`.
- Sequence under S5: the terminated sessions make the next query on a pooled connection raise
  `OperationalError` ("terminating connection due to administrator command"). After that, every
  request waits the full `getconn` timeout and gets `PoolTimeout`, with no server text in the
  handler. That fails change 4.

→ **Connect per request** instead. `psycopg/generators.py` `_connect` raises
`e.OperationalError(f"connection failed: {conn.get_error_message(encoding)}")`. The libpq message
carries the server's `FATAL`, which Postgres raises in `InitializeSessionUserId`
(`src/backend/utils/init/miscinit.c`, REL_17_STABLE) as
`ERRCODE_INVALID_AUTHORIZATION_SPECIFICATION` (SQLSTATE 28000),
`role "%s" is not permitted to log in`. The server rejects right after authentication, so this is
expected in milliseconds.

Timing bound:
- `psycopg/conninfo.py` `timeout_from_conninfo` applies `connect_timeout` **per attempt**. It has
  a 2 s minimum, and unset means 130 s. The `connection.py` `connect()` loop wraps a timeout as
  `ConnectionTimeout("connection timeout expired")`.
- So `connect_timeout=2` with a single host, plus `options=-c statement_timeout=500`, bounds
  `/items` below 3 s.
- The ms timing is UNVERIFIED until the M1-6 smoke test, which measures it with `curl -w %{time_total}`.

**c) §20 — it does not scope S5 to one environment explicitly.** It says "the application role is
set to `NOLOGIN`" and, under Reset, "restore the database role". Both are singular, and neither
names a namespace. The levels L0–L3 are block settings on `nexus-dev` (§12, via
`experiment/dev-state`). Nothing in §20 contradicts per-env roles, so change 3 applies: roles
`app_dev` / `app_prod`, and S5 targets `app_dev` only. ADR-020 records this reading.

**d) Offline docker test — BLOCKED, not run.**
- Docker CLI: `docker` resolves to `/mnt/c/Program Files/Docker/…` and prints "could not be
  found in this WSL 2 distro … activate the WSL integration". There is no podman, nerdctl, skopeo,
  crane or buildah. `ctr` is k3s's symlink: root only, and it is the live node's runtime, so it is
  not offline.
- What is done: the pin is resolved read-only through an anonymous registry token. `postgres:17`
  index = `sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f`
  (linux/amd64 `sha256:e31e3d53…5ee0`). The config shows `PG_VERSION=17.11-1.pgdg13+2`,
  `PGDATA=/var/lib/postgresql/data`, `User` empty (root). The UID 999 claim is unverified.
- To settle: **you** enable Docker Desktop → Settings → Resources → WSL integration → Ubuntu-24.04.
  Then, once you confirm, I run the following. Its result gates the M1-2 PR.
  `docker run --rm -d --name pgtest --user 999:999 --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /var/lib/postgresql/data:uid=999,gid=999 --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp -e POSTGRES_PASSWORD=<generated, not printed> postgres@sha256:d74eeac9…`
  followed by these checks:
  - During startup, a timestamped loop of `pg_isready` over the socket and over `127.0.0.1`,
    confirming the socket answers first and TCP only after init completes (change 15).
  - **Change 19:** `10-roles.sh` is bind-mounted
    (`-v <scratch>/10-roles.sh:/docker-entrypoint-initdb.d/10-roles.sh:ro`, host file `chmod 0555`
    = the ConfigMap `defaultMode: 0555`), not run by hand. First print the pinned image's own
    entrypoint (`docker run --rm --entrypoint sed … -n '/^docker_process_init_files/,/^}/p'
    /usr/local/bin/docker-entrypoint.sh`) and confirm the execute-vs-source branch. The
    `pg_isready` loop timestamps are lined up against the entrypoint's
    `PostgreSQL init process complete; ready for start up.` line in `docker logs -t`.
  - A `psql` login as `app_dev`.
  - `ALTER ROLE app_dev NOLOGIN` → capture the exact error text (covers b). Confirm
    `pg_isready -h 127.0.0.1` still returns 0 while `app_dev` is `NOLOGIN`.
  - Grep `docker logs` for the password: expect 0 hits.
  - `docker rm -f`.
  - **Change 20 negative case**, a second container:
    - a deliberately failing init script (`exit 1` after `initdb`, before the marker), with PGDATA
      on a **docker volume** (`-v pgneg:/var/lib/postgresql/data`, because tmpfs does not survive
      `docker restart`);
    - `docker restart`, then run the exact probe command
      (`sh -c 'pg_isready -h 127.0.0.1 -p 5432 -q && test -f …/.nexus-init-done'`) in a timestamped
      loop;
    - confirm it keeps returning non-zero, and that `docker logs` after the restart shows init
      skipped (the entrypoint's `PostgreSQL Database directory appears to contain a database;
      Skipping initialization` line, `docker-entrypoint.sh:376` on master, with no
      `docker_process_init_files` output);
    - then `docker rm -f` and `docker volume rm pgneg`.

  `--read-only` is stricter than the pod spec. If it is the only cause of a failure, rerun
  without it before concluding.

**e) F12 live check.**
- `kubectl get applications -n argocd`: all 6 `Synced`/`Healthy`. `root`, `platform` and
  `sample-api-prod` are at `8f4eaac`; `sample-api-dev` is at `5d01ee3`. `kyverno`/`observability`
  show `<none>` in `.status.sync.revision` (Helm/multi-source apps report `revisions` instead).
- Node `koussay` is Ready, `v1.34.6+k3s1`, InternalIP `172.19.233.100`. All pods are Running/Ready
  (the `kyverno-migrate-resources` Job is Completed). The namespace levels are as declared
  (`nexus-dev` 0, `nexus-prod` 1, `nexus-data` 0).
- Finding: `journalctl --list-boots` shows the WSL VM **rebooted** (boot −1 ended 20:56:56Z, boot 0
  started 20:59:53Z, 2026-09-26). The four `sample-api` containers show last state `Unknown` / exit
  255 at 20:59:58Z. Earlier terminations (18× Completed, 4× Error at 15:12Z) match the ADR-019
  restart-triggered rotation test. The cluster recovered without intervention, and the node IP is
  unchanged after the reboot.
- **Not yet run:** `scripts/verify-state.sh --out <scratch>/verify.md`. It performs a
  `--dry-run=server` probe and writes a file, so it waits for plan approval.

**f) Connection math.** All figures below are VERIFIED unless marked.

| Factor | Value | Source |
|---|---|---|
| Replicas per env | Git baseline 2 (`k8s/base/deployment.yaml:13`); runtime delegated (`ignoreDifferences` `/spec/replicas`); spec bounds 1–5 (§25), not enforced until K1–K6 (M3) | files, spec |
| Rollout surge | No `strategy` in base, so the default RollingUpdate `maxSurge` 25% applies, rounded up: +1 at 2 replicas, +2 at 5 | `grep strategy` returned nothing |
| Processes per pod | 1 (`CMD uvicorn …`, no `--workers`) | `Dockerfile:20` |
| Threads for sync `def` | Starlette `run_in_threadpool` → `anyio.to_thread.run_sync(func)`, no limiter; the anyio default is `CapacityLimiter(40)` | starlette `concurrency.py`; anyio `_asyncio.py:3187`, `docs/threads.rst:284` |
| Postgres slots | `max_connections=100`, `superuser_reserved_connections=3`, `reserved_connections=0` → 97 for app roles | PG 17 `postgresql.conf.sample:65-67` |

Worst case **unbounded**: baseline 2 envs × 2 pods × 40 = **160**; at the bound with surge,
2 × 7 × 40 = **560**. Both exceed 97.

**Change 11 → per-pod `threading.BoundedSemaphore(5)`.** Acquire timeout 0.3 s; on timeout → 503,
logged as `db_slots_exhausted` (distinct from S5). Worst case 2 × 7 × 5 = **70 ≤ 97**, with 27
spare. DB-side backstop: `CONNECTION LIMIT 35` on each of `app_dev`/`app_prod` (35 = 7 × 5).
`max_connections` stays at 100. Time budget: 0.3 (acquire) + 2.0 (connect) + 0.5 (statement)
= 2.8 s < 3 s. All numbers go into ADR-020. M1b's Locust calibration must show zero
`db_slots_exhausted` at baseline.

**g) Requirements on the StatefulSet's resources and probes — none. The values below are proposals.**
- Live: `kubectl get clusterpolicies,policies -A` → none; `validatingpolicies`/VAPs → none;
  `limitrange,resourcequota -A` → none.
- In Git: `platform/kyverno/values.yaml` has no policies (ADR-015 removed both ClusterPolicies).
- `CLAUDE.md` hard rules and PSS `restricted` don't mention resources or probes; spec §25 gives
  none for dependency-db.

Proposed values follow the sample-api base convention of setting both resources and probes:

| Setting | Proposal | Why |
|---|---|---|
| resources | requests `cpu 100m`, `memory 256Mi`; limits `cpu 500m`, `memory 512Mi` | `shared_buffers` 128MB default plus ≤70 small sessions |
| `emptyDir` sizeLimit | data `1Gi`; socket dir `16Mi` | The seeded table is tiny; this caps runaway use |
| startupProbe | exec `sh -c 'pg_isready -h 127.0.0.1 -p 5432 -q && test -f /var/lib/postgresql/data/.nexus-init-done'`, period 2 s, failureThreshold 30 | initdb + init script ≤ 60 s |
| readinessProbe | same command, period 5 s, timeout 2 s, failureThreshold 3 | — |
| livenessProbe | same command, period 10 s, timeout 2 s, failureThreshold 6 | — |

**Change 20: a half-initialised DB never goes Ready.**
- The **last line** of `10-roles.sh`, reached only under `set -Eeuo pipefail`, runs
  `touch /var/lib/postgresql/data/.nexus-init-done`.
- All three probes require both TCP `pg_isready` and the marker.
- Reason: after a failed init, a container restart keeps the `emptyDir`. The entrypoint then
  skips init (`PG_VERSION` already exists), and TCP `pg_isready` alone would pass on a DB with
  no roles.
- With the marker, such a pod fails startup and liveness until it is replaced, and replacement
  gives a fresh `emptyDir` and a clean init. This is a visible outage, never a silent Ready.
- ADR-020 records this.
- Test d exercises the negative case.

**Change 15:** every probe uses TCP (`-h 127.0.0.1`), never the socket. The image runs init on a
socket-only temporary server (`listen_addresses=''`), so a socket probe would go Ready before
`10-roles.sh` finishes. ADR-020 records this. Probes don't authenticate, so **S5 (`NOLOGIN`)
cannot make the DB unready or restart it**. The injection stays invisible to Kubernetes, and no
restart blurs the discard rule. The claim that `pg_isready` reports "accepting connections"
without valid credentials is to be confirmed in test d.

**Change 16 (thresholds from change 18):** the limits above are **provisional** until the M1b
Locust calibration, and ADR-020 says so and records the thresholds. Over the calibration run,
M1b passes only if all three hold:
- zero `db_slots_exhausted` log lines;
- dependency-db CPU throttling
  `sum(increase(container_cpu_cfs_throttled_periods_total{namespace="nexus-data",container="dependency-db"}[run])) /
  sum(increase(container_cpu_cfs_periods_total{namespace="nexus-data",container="dependency-db"}[run]))`
  **≤ 1%**;
- `max(max_over_time(container_memory_working_set_bytes{namespace="nexus-data",container="dependency-db"}[run]))`
  **≤ 80% of the memory limit** (≤ 409.6 Mi of 512 Mi).

**Change 21:**
- The StatefulSet's container is named `dependency-db` explicitly.
- Every query carries `namespace="nexus-data"`.
- **An empty result or NaN is a FAIL**, not a pass. For example, if no CFS series exists,
  `0/0` gives NaN.
- ADR-020 records all three.

**h) cAdvisor series in the live Prometheus — PRESENT, nothing missing.** The query went through
the API server's service proxy (GET, read-only):
`kubectl get --raw /api/v1/namespaces/monitoring/services/http:observability-kube-prometh-prometheus:9090/proxy/api/v1/query?query=count by (job,metrics_path,container) (<metric>{namespace="nexus-prod",pod=~"sample-api.*"})`

| Metric | Result |
|---|---|
| `container_cpu_cfs_throttled_periods_total` | `job=kubelet, metrics_path=/metrics/cadvisor`: 2 series with `container="sample-api"`, plus 2 without `container` |
| `container_cpu_cfs_periods_total` | same: 2 + 2 |
| `container_memory_working_set_bytes` | 2 with `container="sample-api"`, plus 4 without `container` |

The series without a `container` label are cgroups at pod or sandbox level, hence the
`container=` filter in the change-16 queries.
UNVERIFIED: cAdvisor may emit CFS series only for containers that have a CPU quota (sample-api
has a 200m limit). If so, dependency-db's `cpu 500m` limit is required for the throttling
threshold to be measurable. The empty/NaN = FAIL rule (change 21) catches it if not. Once the DB
runs, the same query with `container="dependency-db"` settles it.

**Changes 22–24** (approved with rev 5; they go into the M1-0 `TASKS.md` as changes 1–24 and
are applied in test d and M1-2):
- **22:** test d positive case. Run the exact probe command
  (`pg_isready -h 127.0.0.1 -p 5432 -q && test -f <marker>`) in the timestamped loop. Expect
  non-zero until `init process complete`, then 0.
- **23:** the negative-case container runs **without `--rm`**, so `docker restart` has something
  to restart.
- **24:** startupProbe `failureThreshold` goes 30 → **150** (5 min at 2 s). A startup kill
  mid-init with the marker is a permanent CrashLoop. The init duration is recorded in test d and
  in the M1 exit rebuild (step timestamps). ADR-020 names the recovery:
  `kubectl delete pod dependency-db-0`, only with your approval.

## Design (changes applied)

**dependency-db** (`apps/dependency-db/`, `namespace: nexus-data`)
- StatefulSet with 1 replica, image `postgres@sha256:d74eeac9…` (17.11).
- Pod and container securityContext: `runAsUser/runAsGroup/fsGroup` = the UID that test d
  confirms (expected 999), `runAsNonRoot`, drop ALL, `allowPrivilegeEscalation: false`, seccomp
  `RuntimeDefault`.
- `emptyDir` for `PGDATA` and `/var/run/postgresql`.
- Headless and ClusterIP Service `dependency-db:5432`.
- **Change 13 — init is a `.sh` script** in a ConfigMap mounted at
  `/docker-entrypoint-initdb.d/10-roles.sh` with **`defaultMode: 0555`** set explicitly
  (change 19).
- Per `docker_process_init_files` in docker-library `17/trixie/docker-entrypoint.sh:172-184`
  (master), a `*.sh` file with `-x` is **executed** as its own process, and one without it is
  sourced. So 0555 → executed, the script starts with `set -Eeuo pipefail`, and a non-zero exit
  aborts init. The pinned image's copy is confirmed in test d. It feeds SQL to `psql -v ON_ERROR_STOP=1` on
  **stdin (heredoc)**, and psql reads the passwords with `\getenv pw_dev APP_DEV_PASSWORD` (psql
  15+). Passwords are therefore never in argv, and the script uses no `set -x`.
- The session first runs `SET log_statement = 'none'; SET log_min_error_statement = 'panic';`, so a
  failed `CREATE ROLE … PASSWORD :'pw_dev'` cannot echo the password into the server log.
- It creates table `items` (about 20 seeded rows), and roles `app_dev`/`app_prod` with `LOGIN`,
  `CONNECTION LIMIT 35` and `SELECT ON items` only. Its last line writes the
  `.nexus-init-done` marker (change 20).
- The superuser is reachable only through the local socket (`kubectl exec`).
- `platform/argocd/applications/dependency-db.yaml` tracks `main`; the AppProject gains
  `apps/StatefulSet`.

**Secrets** (`scripts/dependency-db-secrets.sh`, called by `bootstrap.sh` after
`step_f_wait_platform` and also runnable standalone, no sudo)
- Files: `~/.nexus/dependency-db-{super,app-dev,app-prod}`, following the ADR-019 generate-or-reuse
  pattern (`umask 077`, no newline).
- Secrets: `nexus-data/dependency-db` (all three values), `nexus-dev/dependency-db-app`
  (`app_dev`), `nexus-prod/dependency-db-app` (`app_prod`). Each is created only if absent, never
  `apply`.
- **Change 5:** if any `~/.nexus` file is missing while **any** of the three Secrets exists →
  `fatal` ("delete all three Secrets and the files to rotate"). Silently regenerating would split
  the DB and app credentials.
- It waits for each namespace to exist (`nexus-dev` is created by `sample-api-dev`).

**`/items`** (`apps/sample-api/main.py`; `psycopg[binary]` only, no pool)
- **Change 10:** `def items()`, a plain `def`, so Starlette runs it in the threadpool. An
  `async def` would run the blocking psycopg call on the event loop, stall `/ready` and turn S5
  into a readiness failure, which breaks NF-23.
- It acquires the semaphore (0.3 s; f), then calls `psycopg.connect(host, dbname, user, password,
  connect_timeout=2, options="-c statement_timeout=500")` and runs
  `SELECT id, name FROM items ORDER BY id` → 200 JSON.
- `except psycopg.Error as exc` → **503**, and it logs `type(exc).__name__`, `exc.sqlstate`
  and `str(exc)` at ERROR (change 4: the server message reaches the log).
- The password is never logged. `DB_*` values are read at request time, so the app starts and
  stays Ready with no DB and no env set.
- `/ready` and `/health` are untouched (NF-23).
- Tests (`test_main.py`, `psycopg.connect` monkeypatched):
  - 200 with rows;
  - 503 when `OperationalError("…role \"app_dev\" is not permitted to log in")` is raised, with the
    message present in `caplog`;
  - `/ready` returns 200 while `connect` raises;
  - the conninfo passed contains `connect_timeout=2` and `statement_timeout`;
  - `not inspect.iscoroutinefunction(main.items)` (change 10);
  - semaphore exhausted → 503 with `db_slots_exhausted` and no connect attempted.
- **Change 12:** `pytest`, `pytest-asyncio` and `httpx` move from `requirements.txt` (the runtime
  image) to `requirements-dev.txt`. The `ci.yml` test job then installs both files. That job
  already triggers on `apps/sample-api/**` and `ci.yml`.

**Change 2:** the base and overlays get **no** DB env until M1-5. There, one commit carries the
digest bump + `DB_HOST`/`DB_NAME` (base) + `DB_USER` (`app_dev` in overlays/dev, `app_prod` in
overlays/prod) + `DB_PASSWORD` `secretKeyRef dependency-db-app`.

## Where the handoff items go

| Item | Where |
|---|---|
| Audit retention | M1-6: an ADR-019 addendum accepting about 10 days (per-run extract, spec §14), based on a read-only 24 h steady-state measurement. **Change 14:** it counts only if `/proc/sys/kernel/random/boot_id` and `systemctl show k3s -p ActiveEnterTimestamp` are identical at the start and end; otherwise restart the window. (Report e shows one reboot already happened.) **Change 7:** a new Later item, "re-measure audit growth at M4". |
| `step_h` simultaneous-stable; `set -e` + fault-injection rerun; step timestamps; restart-count info line | **M1-3, one commit each (change 8)** |
| `observability` timeout | Later. The M1 rebuild uses `NEXUS_WAIT_TIMEOUT_OBSERVABILITY=1800` |
| `capture-state.sh:508` | Later (lint pass) |
| M1b (Q5) | Z-score rules replacing the interim alert, the `nexus` Application and operator, fault hooks + `NEXUS_FAULTS_ENABLED`, §27 M1 verifications (Kopf spike, KSM/Alertmanager queries, Locust calibration, CEL transition rules) |
| New Later | The sample-api VM-reboot restarts (e, informational). The pytest/httpx-in-image item is **not** added: M1-1 does it (change 12). |

## Phases and gates

| Phase | Content | Cluster effect | Gate |
|---|---|---|---|
| **M1-0** | 1) `verify-state.sh --out <scratch>/verify.md` (approved); quote the result after a leak check. 2) Memory: extend the pasted-reply rule to M1 (pasted text never replaces merge approval); delete `tasks-later-set-e-backstop.md` and its `MEMORY.md` line. 3) Docs PR on a branch `docs/m1-plan` from `dev` → `dev`: `TASKS.md` marks M0 complete (header, item 5, GATE M0-5), adds M1-0…M1-6 with changes 1–21, M1b, and the Later changes. 4) Report a–h with the PR. | none | **GATE M1-0** (merge needs your approval) |
| **M1-1** | PR `feat(sample-api)`: `/items` (plain `def`, semaphore, per-request connect), `psycopg[binary]`, tests, test deps moved out of the image plus the `ci.yml` install line (change 12), version 0.2.0. No manifest change. | none | — |
| **M1-2** | **Gated by test d.** PR `feat(dependency-db)`: manifests (g values), `10-roles.sh` (change 13), Application, AppProject StatefulSet, and ADR-020 covering: image and digest, UID, `emptyDir`, per-env roles and S5 → `app_dev`, the Secret contract, the `/items` contract, the 3 s budget and connection math (f), and the §3 reading that `/items` is ungated. It also records TCP probes and why (change 15), the provisional limits and M1b thresholds (changes 16/18), the init marker in the probes (change 20), the container name, namespace filter and the empty/NaN = FAIL rule (change 21), and **change 9**: `emptyDir` survives container restarts, so a container restart is an outage that does *not* heal S5, while a pod replacement (new `metadata.uid`) gets a fresh `emptyDir`, re-runs initdb and heals S5. The runner records the DB pod's `metadata.uid` and `restartCount` before and after each run, and discards the run on either change. Validation: kustomize + kubeconform + render check, then a server-side dry-run with approval. | none | — |
| **M1-3** | PR `feat(scripts)`, separate commits: (1) `step_h` simultaneous-stable; (2) `set -e`; (3) step timestamps; (4) restart-count info line; (5) `dependency-db-secrets.sh` + bootstrap call; (6) `dependency-db` in both `EXPECTED_APPS` + a DB-pod-Ready check. **No `/items` check (change 1).** Validation: `--plan` + the 6-scenario fault-injection rerun in a throwaway worktree. | none | **GATE M1-3** |
| **M1-4** | You run `dependency-db-secrets.sh` → `dev`→`main` gate PR (DB goes live; CI signs the new image) → forward-merge to `experiment/dev-state`. | DB via ArgoCD | **GATE M1-4** |
| **M1-5** | One commit: digest bump (after `cosign verify`) + DB env wiring (change 2). Plus a second commit adding the `verify-state.sh` `/items` 200 check in both namespaces (change 1). Then the gate PR and forward-merge, then `verify-state.sh` live. | `/items` live | **GATE M1-5** |
| **M1-6** | S5 smoke test on `app_dev`, with approval: `/items` 503 under 3 s with the `FATAL` text in the log, `/ready` 200, prod unaffected, reset, DB pod `metadata.uid` + `restartCount` unchanged before and after (change 9). Then the audit addendum, the from-empty rebuild (owner), `CURRENT_STATE.md`, `CHANGELOG [0.2.0]`, tag `v0.2.0` (separate approval). | owner rebuild | **GATE M1 exit** |

## Verification

- Offline: `ruff`, `pytest`, `kustomize build`, `kubeconform -strict`, `.github/scripts/repo-checks.sh`, test d.
- Live: server-side dry-run (M1-2). After each gate: 7 Applications `Synced`/`Healthy`, stable for ≥60 s, and `verify-state.sh` exits 0.
- Behaviour: the M1-6 smoke test with timing. M1 exit: `verify-state.sh` exits 0 from empty.

## Assumptions (open ones only)

- A4: the image runs under PSS `restricted` as UID 999 → test d (offline) and the M1-2 dry-run (admission).
- A5: until M3 there are no NetworkPolicies, so any pod can reach 5432.
- The DNS lookup inside `psycopg.connect` has no timeout of its own. Inside the cluster CoreDNS
  answers in milliseconds (a missing Service gives NXDOMAIN), so it is not bounded by
  `connect_timeout`. Accepted, and noted in ADR-020.
- A new connection per request under the Locust baseline adds a few ms of SCRAM auth per
  `/items` call. Acceptable at about 40% of capacity, and it is measured at the M1b Locust
  calibration.

## Risks

- The live Secrets must exist before the M1-4 merge.
- The AppProject and the Application land in the same sync; ArgoCD retry covers it.
- Docker Hub rate limits apply to the new image.
- The image flow needs two gates.
- The rebuild may reach the `observability` timeout, hence the env override.
