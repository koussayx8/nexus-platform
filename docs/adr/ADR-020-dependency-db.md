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
  dependency-db-0` (a fresh `emptyDir` and a clean init), only with the owner's approval, and
  **run by the owner**: the agent's guard (`.claude/settings.json`) denies `kubectl delete`.
- **Coupling:** the `verify-state.sh` rollout term (420 s = 150 × 2 s + a 120 s pull allowance, M1-3
  commit 7) is derived from this budget. It sets the verify default (180 + 420 + 60 = 660 s) and
  the M1-4 bound (180 + 160 + 420 + 60 = 820 s). Changing the startupProbe budget or the pull
  allowance means re-deriving all three. Superseded at M1-5 by the four-value rule below.
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
- A mid-init kill needs a manual pod delete, approved and run by the owner.
- Until M3 there are no NetworkPolicies, so any pod in the cluster can reach
  `dependency-db:5432` (plan assumption A5). Accepted: the roles still need passwords, and the
  NetworkPolicies of spec §18 arrive in M3.
- The resource figures and the pull allowance must be re-checked at M1-4 and at the M1b
  calibration.

## Addendum (2026-09-27, GATE M1-4): pull allowance 300 s and the four-value coupling rule

M1-4 measured the node's pull of the pinned `postgres` image at **143.2 s** (161,346,986 bytes,
~1.13 MB/s), over the 120 s allowance. Verify run 1 still passed because its bound (820 s) had
slack. The owner raised the allowance to **300 s**. Derived values:

| Value | Derivation | Where |
|---|---|---|
| Rollout | 300 startupProbe (150 × 2 s) + 300 pull = **600 s** | the term inside the three below |
| `verify-state.sh` default | 180 reconcile + 600 + 60 stable = **840 s** | `NEXUS_VERIFY_APPS_TIMEOUT` |
| M1-4-style bound | 180 + 160 retry backoff + 600 + 60 = **1000 s** | env override when a project widening races |
| `bootstrap.sh` DB wait | 160 + 600 + 60 = 820, rounded up for rebuild contention = **900 s** | `NEXUS_WAIT_TIMEOUT_DEPENDENCY_DB` default |

**Coupling rule:** all four values are derived from the startupProbe budget and the pull
allowance. Changing either means re-deriving all four. The pull is re-measured at the M1 exit
rebuild.

The idle DB's CFS throttling (~23 % of active periods, from probe bursts) is recorded in
`TASKS.md` M1b; it changes no limit here.

## Addendum (2026-09-27, GATE M1-5): the reconcile term is 480 s, not 180 s

**Measured pickup delay** (merge or push → the Application's `.status.sync.revision` at the new
commit, from the `verify-state.sh` poll logs, 5 s resolution):

| Event | Pickups |
|---|---|
| M1-4, #74 merge | `platform` 242 s, `root` 356 s |
| M1-5, #76 merge | `root` and `sample-api-prod` 262 s, `dependency-db` 269 s, `kyverno` and `observability` 352 s, `platform` **382 s** |
| M1-5, forward-merge push | `sample-api-dev` 315 s |

Every one exceeds the 180 s term.

**Cause (read-only, ArgoCD v3.3.8 source and live config).** A second cache sits in front of the
controller's poll:
- The controller refreshes each Application every `timeout.reconciliation` 120 s plus up to
  `timeout.reconciliation.jitter` 60 s (`defaultAppResyncPeriod = 120`, `defaultAppResyncPeriodJitter
  = 60`).
- The repo-server caches each repository's resolved Git references (branch → SHA) for
  `--revision-cache-expiration`. Its default is `ARGOCD_RECONCILIATION_TIMEOUT`, else 3 min
  (`reposerver/cache/cache.go`).
- Live: `argocd-cm` and `argocd-cmd-params-cm` set none of these keys. The repo-server's
  `ARGOCD_RECONCILIATION_TIMEOUT` is an optional reference to the absent `timeout.reconciliation`,
  so the 3 min default applies.
- Worst case: a reference cache filled just before the push serves the old SHA for up to 180 s.
  The next controller refresh then comes up to 180 s later: 360 s, plus comparison time. The
  measured 382 s is within 5 s polling plus that time.

**Re-derived** (reconcile term 480 s = the measured worst case 382 s plus about 25 % margin,
equivalently the 360 s model plus 120 s):

| Value | Before | Now | Derivation |
|---|---|---|---|
| Rollout | 600 s | 600 s | unchanged |
| `verify-state.sh` default | 840 s | **1140 s** | 480 + 600 + 60 |
| M1-4-style bound | 1000 s | **1300 s** | 480 + 160 + 600 + 60 |
| `bootstrap.sh` DB wait | 900 s | 900 s | unchanged: no Git-pickup term, since bootstrap creates the Applications from an empty cache |

The 160 s retry term is unchanged. `root` and `platform` track the same repository, so they share
one reference cache entry and see the new SHA at the same moment. Their pickups can then differ
only by the controller's 180 s refresh window, which is what the 160 s term assumed.

**Coupling rule, extended:** the four values derive from the startupProbe budget, the pull
allowance and the reconcile term. Changing any of the three means re-deriving all four. Setting
`timeout.reconciliation` or `reposerver.revision.cache.expiration` would change the reconcile term,
and is out of scope here.

## Addendum (2026-09-28, GATE M1-6 b5): which budget covers which pull

The M1 exit rebuild re-measured the `postgres` pull (161,346,986 bytes): **377.3 s**
(04:13:35 → 04:20:06Z), over the 300 s allowance. It ran alongside 12 other image pulls
(`sample-api` ×4 at about 70 s each, `kyverno` 2 min 24 s to 5 min 13 s, `prometheus` 7 min 55 s,
`grafana` 10 min 23 s), so the node's bandwidth was shared. Init took about 2 s (container start
04:20:06, Ready 04:20:08Z).

**Decision (owner, GATE M1-6 b5): keep the 300 s allowance, scoped explicitly.** No value in the
coupling rule changes.

| Situation | Measured | Budget that covers it |
|---|---|---|
| A single, uncontended pull in a running cluster: `dependency-db`'s first start after a merge | 143.2 s (M1-4) | the 300 s pull allowance, inside the 600 s rollout term of the `verify-state.sh` default (1140 s) and the M1-4-style bound (1300 s) |
| A rebuild: every image pulled at once from an empty node | 377.3 s (M1-6) | `bootstrap.sh`'s own 900 s DB wait, counted from step h; 390 s used |

A gate that makes the node pull several images at once (for example a digest bump of more than
one workload, or a new Application with its own images) is closer to the rebuild case than to the
single-pull case. For that run, raise `NEXUS_VERIFY_APPS_TIMEOUT` and record the value used in the
report, as for the M1-4 project-widening race.

## Addendum (2026-09-29, M1b-0): the M1 exit pickups widen the range to 162–382 s

Same method as the GATE M1-5 addendum (first poll with the new revision, 5 s resolution), from the
M1 exit verify runs (`~/nexus-evidence/m1-6/verify-a11.md`, `verify-a13.md`):

| Event | Pickups |
|---|---|
| M1 exit, #81 merge (07:32:27Z) | `platform` 196 s, `kyverno` 201 s, `root` 207 s, `sample-api-prod` and `dependency-db` 250 s, `observability` 376 s |
| M1 exit, forward-merge push (~07:43:35Z) | `sample-api-dev` 162 s |

The measured range is now **162–382 s** (about 2.7 to 6.4 min). The worst case is unchanged, so
the 480 s reconcile term and the four derived values stand. 162 s is below the 180 s term: a
pickup can be fast when the reference cache happens to expire just before the controller's next
refresh.

## Addendum (2026-10-02, M1b gates 2 and 3): the measured pickup range is now 108–382 s

Same method (ArgoCD `deployedAt` of the new revision, or first poll at it), from the M1b gate runs
(`~/nexus-evidence/m1b-gate2/`, `m1b-gate3/`):

| Event | Pickup |
|---|---|
| Gate 2 (#92 merge, 2026-10-01T05:56:53Z) | `sample-api-prod` 180 s |
| Gate 2 forward-merge push (06:21:39Z) | `sample-api-dev` **108 s** |
| Observability gate (#94 merge, 13:36:16Z) | Grafana's new pod Ready 297 s after the merge |

The measured range is now **108–382 s**. The worst case, and so the 480 s reconcile term and the
four derived values, are unchanged.

## Addendum (2026-10-07, M1b-9 R1 gate): the change-16 DB criterion is restated

**The criterion changed after the data.** We set this down plainly: the original criterion was fixed before
any load was measured, and it is replaced now because the R1 data show it measures the wrong thing.

- **Original (change 16, above):** passes only with zero `db_slots_exhausted`, CFS throttled ÷ total
  periods ≤ 1 %, and a working-set peak ≤ 80 % of the memory limit.
- **What the data show:**
  - At idle (2026-10-06, 30 min): throttled ÷ periods 30.3 %, at 0.018 cores.
  - Under R1 load (2026-10-07; `~/nexus-evidence/m1b-9/r1/`): 8.0 %, 4.7 %, 4.0 %, 4.1 % at 10, 20, 30 and 40 req/s on
    `nexus-dev`. DB CPU was 0.027–0.060 cores of the 0.5 limit (at most 12 %); the working set was 42.5–42.8 MiB (about
    8 % of 512Mi).
  - The ratio *falls* as load rises. CFS counts only periods in which the container ran, and near idle those are mostly
    short probe and backend-fork bursts, so a few throttled bursts dominate the ratio. It measures burst shape, not
    whether the DB keeps up.
- **Restated criterion (owner, R1 gate):** the DB is not the bottleneck at B. All three of these, over the
  60-min clean baseline:
  - zero `db_slots_exhausted`;
  - DB CPU under 50 % of its limit (< 0.25 cores);
  - working set under 80 % of the memory limit (< 410 MiB).

  As before, every query filters `namespace="nexus-data", container="dependency-db"`, and an empty or NaN
  result is a FAIL.
- **The throttled-periods ratio stays reported, not gated.**
  `container_cpu_cfs_throttled_seconds_total` is absent for this container, so throttled seconds are not
  available as an alternative.
- **No DB change before S5:** limits stay requests `cpu 100m` / `memory 256Mi`, limits `cpu 500m` / `memory 512Mi`.
  The ADR-020 coupling rule is untouched: it depends on the startupProbe budget and the pull allowance, not on these.
