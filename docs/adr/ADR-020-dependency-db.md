# ADR-020: The Dependency DB, the `/items` Contract and S5

## Status: Accepted

## Context
Scenario S5 (spec §20) sets the application role to `NOLOGIN` and terminates its sessions. It must
be a gray failure (NF-23): `sample-api` `/items` returns 5xx while readiness stays green. M1 adds
the Dependency DB (spec §3) and `/items` to make that possible. The M1 plan (`docs/plans/m1-plan.md`,
rev 5, changes 1–24) fixed the design; this ADR records it with the evidence from test d
(2026-09-27, the pinned image under Docker, UID 999, all capabilities dropped, `--read-only`).

## Decision

### Image, identity, storage
- `postgres@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f`
  (`postgres:17` index, `PG_VERSION=17.11-1.pgdg13+2`, 161.3 MB compressed). Pulled in 55.9 s
  through Docker Desktop at test d, so the image-pull allowance below is 120 s, not the planned 60 s.
- UID/GID **999** (`id postgres` in the image: `uid=999(postgres) gid=999(postgres)`), with
  `runAsNonRoot`, `fsGroup: 999`, all capabilities dropped, no privilege escalation, seccomp
  `RuntimeDefault`: PSS `restricted`, which `nexus-data` enforces.
- **`emptyDir`** for the data and the socket directory (M1 Q1). The DB holds only seeded rows.
- **`PGDATA=/var/lib/postgresql/data/pgdata`**, a subdirectory of the data `emptyDir`.
  - Why: the kubelet creates the `emptyDir` root and owns it (root, mode 2777, group 999 through
    `fsGroup`). initdb must `chmod` PGDATA to 0700, and UID 999 cannot `chmod` a directory it
    does not own. A subdirectory that the entrypoint creates is owned by 999, so initdb can.
  - Evidence, test d with a root-owned 2777 mount: PGDATA at the mount root fails
    (`initdb: error: could not change permissions of directory "/var/lib/postgresql/data":
    Operation not permitted`); the subdirectory initialises and goes Ready.
  - The init marker sits at the volume root (`/var/lib/postgresql/data/.nexus-init-done`), not
    inside PGDATA. It is on the same `emptyDir`, so it has the lifecycle change 20 intends: it
    survives a container restart together with PGDATA, and a pod replacement removes both.
- One replica, StatefulSet `dependency-db` in `nexus-data`, container named `dependency-db`;
  a headless Service (`dependency-db-headless`) and a ClusterIP Service `dependency-db:5432`.

### Init (changes 13, 19, 20)
- `10-roles.sh` ships as a generated ConfigMap mounted at `/docker-entrypoint-initdb.d` with
  `defaultMode: 365` (0555). The pinned image's `docker_process_init_files` **executes** a `*.sh`
  that has `-x` and sources one without it; test d logged
  `docker-entrypoint.sh: running /docker-entrypoint-initdb.d/10-roles.sh`. Executed under
  `set -Eeuo pipefail`, a failure exits non-zero and aborts init.
- SQL goes to `psql -v ON_ERROR_STOP=1` on stdin. Passwords are read with `\getenv`, never in
  argv, and the session first sets `log_statement = 'none'` and `log_min_error_statement = 'panic'`.
  Test d: 0 hits for any of the three passwords in the container log or the `/items` log.
- It creates `items` (20 rows), roles `app_dev` and `app_prod` with `LOGIN`,
  `CONNECTION LIMIT 35` and `SELECT ON items` only (test d: `INSERT` → `permission denied`).
- Its last line writes `/var/lib/postgresql/data/.nexus-init-done`, outside PGDATA on the same
  `emptyDir`.
- The superuser is reachable only through the local socket (`kubectl exec`).

### Probes (changes 15, 20, 22, 24)
- Every probe runs `sh -c 'pg_isready -h 127.0.0.1 -p 5432 -q && test -f <marker>'`: TCP, never
  the socket, because init runs on a socket-only temporary server (`listen_addresses=''`).
- Test d, positive case: the socket answered at 45.70–45.97 s while TCP and the exact probe
  returned 2; the probe first returned 0 at 46.37 s, after `PostgreSQL init process complete`
  (46.30 s), and never failed again. Init took about 2.7 s from container start.
- Test d, negative case (changes 20, 23): init failing after the roles and before the marker,
  PGDATA on a docker volume, `docker restart`. The entrypoint logged `PostgreSQL Database directory
  appears to contain a database; Skipping initialization`; TCP `pg_isready` returned 0, and the
  exact probe returned 1 in all 69 samples over 20 s. A half-initialised DB never goes Ready.
- startupProbe: period 2 s, `failureThreshold: 150` (5 min). A startup kill mid-init leaves
  PGDATA without the marker, which is a permanent CrashLoop. **Recovery:** `kubectl delete pod
  dependency-db-0` (a fresh `emptyDir` and a clean init), only with the owner's approval.
- **Coupling:** the `verify-state.sh` rollout term (420 s = 150 × 2 s + a 120 s pull allowance, M1-3
  commit 7) is derived from this budget. It sets the verify default (180 + 420 + 60 = 660 s) and
  the M1-4 bound (180 + 160 + 420 + 60 = 820 s). Changing the startupProbe budget or the pull
  allowance means re-deriving all three.
- Probes do not authenticate, so S5 cannot fail them. Test d: with `app_dev` `NOLOGIN`, the exact
  probe returned 0.

### Resources (changes 16, 18, 21) — provisional
- Requests `cpu 100m`, `memory 256Mi`; limits `cpu 500m`, `memory 512Mi`; `emptyDir` size limits
  1 Gi (data) and 16 Mi (socket).
- Provisional until the M1b Locust calibration, which passes only with zero `db_slots_exhausted`
  lines, CFS throttled ÷ total periods ≤ 1 %, and a working-set peak ≤ 80 % of the memory limit.
- Every query filters `namespace="nexus-data", container="dependency-db"`. An empty result or NaN
  is a FAIL, not a pass.

### Roles and S5 (change 3)
- Per-environment roles: `app_dev` for `nexus-dev`, `app_prod` for `nexus-prod`. §20 names "the
  application role" in the singular and no namespace; S5 targets **`app_dev`** only, since the
  autonomy levels are block settings on `nexus-dev`.

### Secret contract
Created by `scripts/dependency-db-secrets.sh` (M1-3), each only if absent, from
`~/.nexus/dependency-db-{super,app-dev,app-prod}`:

| Secret | Keys | Consumer |
|---|---|---|
| `nexus-data/dependency-db` | `postgres-password`, `app-dev-password`, `app-prod-password` | this StatefulSet |
| `nexus-dev/dependency-db-app` | `password` (`app_dev`) | `sample-api` in `nexus-dev`, from M1-5 |
| `nexus-prod/dependency-db-app` | `password` (`app_prod`) | `sample-api` in `nexus-prod`, from M1-5 |

If any `~/.nexus` file is missing while any of the three Secrets exists, the script fails
(change 5). Silently regenerating would split the DB and app credentials. The passwords are also
in the DB container's environment for its lifetime; that is the image's contract.

### The `/items` contract (changes 4, 10, 11)
- Database `nexus`, Service `dependency-db.nexus-data:5432`. The DB env reaches `sample-api` only
  in M1-5 (change 2).
- `/items` is a plain `def` (threadpool), so a blocking connect never stalls `/ready`.
- A new connection per request: psycopg-pool swallows the connect error and raises `PoolTimeout`
  after 30 s, with no server text. `connect_timeout=2` (psycopg's minimum, per attempt),
  `options=-c statement_timeout=500`.
- A per-pod `BoundedSemaphore(5)` with a 0.3 s acquire, else 503 `db_slots_exhausted`.
  Time budget 0.3 + 2.0 + 0.5 = 2.8 s < 3 s.
- Connection math: anyio's default `CapacityLimiter(40)` per process gives 160 connections
  unbounded at baseline (2 envs × 2 pods) and 560 at 5 replicas plus surge (2 × 7 × 40). With the
  semaphore, 2 × 7 × 5 = 70 ≤ 97 (`max_connections` 100 − 3 superuser-reserved).
  `CONNECTION LIMIT 35` (7 × 5) per role is the DB-side backstop.
- On `psycopg.Error`: 503 `db_unavailable`, logged at ERROR with type, sqlstate and message on one
  line. **Connect-time errors carry `sqlstate=None`**: psycopg builds that `OperationalError`
  client-side. The S5 evidence is the message text; the app has no message classifier. Test d,
  `sample-api` against the test DB: 200 with 20 rows; after `ALTER ROLE app_dev NOLOGIN`, 503 in
  16 ms with `db_error type=OperationalError sqlstate=None message=connection failed: … FATAL:
  role "app_dev" is not permitted to log in`, `/ready` 200; after reset, 200.
- The DNS lookup inside `psycopg.connect` is not bounded by `connect_timeout`. CoreDNS answers in
  milliseconds in-cluster, so this is accepted.
- `/items` is **not** gated by `NEXUS_FAULTS_ENABLED` (M1 Q3). §3 gates the *fault hooks*;
  `/items` is ordinary application behaviour, and S5 is injected in the DB, not in the app.

### The S5 discard rule (change 9)
An `emptyDir` survives container restarts, not pod replacement. A container restart is an outage
that does **not** heal S5; a pod replacement (new `metadata.uid`) re-runs initdb and heals it. The
experiment runner records the DB pod's `metadata.uid` and `restartCount` before and after each
run, and discards the run on either change.

### ArgoCD
Application `dependency-db` (tracks `main`, `apps/dependency-db`, automated sync with prune and
selfHeal, the same retry policy as every Application). The AppProject gains `apps/StatefulSet` in
the same PR (standing rule). The AppProject is managed by `platform`, not `root`, so no sync-wave
can order it first; M1-4 uses a derived `NEXUS_VERIFY_APPS_TIMEOUT=820` instead (`TASKS.md`).

## Consequences
- S5 is invisible to Kubernetes: no probe fails, nothing restarts, and only `/items` and its log
  show it.
- Any pod replacement loses the data and re-seeds it; that is intended.
- A mid-init kill needs a manual, approved pod delete.
- The resource figures and the pull allowance must be re-checked at M1-4 and at the M1b
  calibration.
