# ADR-028: The Dependency DB's CPU Limit Follows ADR-027's Principle

## Status: Proposed (M1b-9, branch `feat/m1b-9-db-cpu-limit`; owner decision (1) after checkpoint B failed on A1, 2026-10-13; time-boxed: one fix, then one checkpoint B and run 3, and a second failure is reported, not fixed; rolled out at `main` `01838a4`; checkpoint B VALID, FAIL on A1 (dev), A2, D1 and D2, 2026-10-13: the second failure, reported, not fixed)

## Context
Checkpoint B (2026-10-13, 11:37:45–11:57:45Z, B = 80) was **VALID and FAILED on A1**:
- `/items` at 50–100 ms: dev 13.72 %, prod 14.03 % (limit 3 %).
- A2 passed: sample-api throttled ≤ 0.07 % at both levels.
- Every pod showed the slow tail (11.6–15.2 %); `/` stayed clean.

Evidence: `~/nexus-evidence/m1b-9/cb-run3-repeat-20261013T1133Z/` (`run.jsonl`, `NOTE-2026-10-13-db-cost.md`).

**The DB, at the same load** (all three windows: about 32 `/items`/s, 160 req/s):

| Window | DB CPU | CPU per `/items` | DB throttled (container / pod) | DB processes max (backends) |
|---|---|---|---|---|
| R1 attempt 2, step 160 (2026-10-09) | 0.195 cores | 6.26 ms | 5.1 % / 7.4 % | 8 (2) |
| 2026-10-13 08:20–08:40Z | 0.220 | 6.88 ms | 10.3 % / 11.4 % | 12 (6) |
| Checkpoint B 11:37:45–11:57:45Z | 0.241 | 7.52 ms | **50.0 % / 43.8 %** | **26 (20)** |

- At idle the DB runs 6 processes (the postmaster and five background processes; read 2026-10-13). Backends =
  processes − 6.
- **In checkpoint B, 20 backends ran at once: exactly the app's 20 DB slots** (4 sample-api pods × 5).
- CPU per request rose about 20 %; throttling rose about 10×.
- **What `/items` costs:**
  - one new connection per request (a new backend process plus authentication), then `SELECT id, name FROM items
    ORDER BY id` on a 20-row table created once at init;
  - the app roles have SELECT only, and the B mix sends GETs only, so nothing writes;
  - the cost is connection setup, not the query.
- **Liveness restarts as evidence:**
  - The DB's restart count rose from 8 (2026-10-09) to 12.
  - On 2026-10-13 the container got `received fast shutdown request` (SIGTERM) at 08:13:41Z and ended "Completed",
    exit 0.
  - The liveness probe (pg_isready, 10 s period, 2 s timeout, 6 failures) is the likely sender. The events have
    expired: UNVERIFIED.
  - At the 11:05Z resume after a VM pause of about 2 h the DB logged 25 `canceling authentication due to timeout`,
    and at 11:37:17Z one `too many connections for role` (CONNECTION LIMIT 35).
- **Mechanism, UNVERIFIED:** a feedback loop.
  1. Connection bursts exhaust the 0.5-core quota in a 100 ms period.
  2. The stalled backends hold their connections longer, so more of them overlap.
  3. Larger bursts follow, and more throttling.
  4. The loop ends when the app's slots are full (20).

  Average CPU (0.24 cores, under half the limit) does not show it. Throttling and the concurrent-process count do.

## Decision

### The principle (ADR-027, applied to the DB)
The CPU limit is the most CPU the DB can use at once on normal traffic, so normal traffic never throttles. The request
stays 100m.

**Derived from Postgres's process model and the app's DB slots:**
- Postgres runs one single-threaded backend process per connection, plus a fixed set of background processes.
- On normal traffic the number of concurrent backends is bounded by the app's DB slots: `DB_SLOTS =
  BoundedSemaphore(5)` per sample-api pod, 2 replicas per namespace, so **20 slots** at B.
- Each `/items` connection costs a bounded amount of CPU: **6.3–7.5 ms** measured (the higher value under
  throttling).
- The most CPU normal traffic can demand in one 100 ms CFS period is every slot running one connection:
  **20 × 7.5 ms = 150 ms per 100 ms = 1.5 cores**. The background processes add about 0.02 cores at idle.
- **Limit: 2000m.** That is 1.5 cores plus headroom for the background processes and for a per-connection cost
  measured under throttling, rounded up to a whole core.
  - A burst with every slot busy then fits inside one period's quota, so normal traffic never throttles.
  - The unscoped bound (one core per backend, 20 + 6 processes) would exceed the node's 12 cores. As in ADR-027, the
    scope is normal traffic.
- Memory (512Mi), the request (100m / 256Mi), the probes and the ADR-020 coupling rule are unchanged.

### ADR-020's criterion, restated in absolute cores
The ADR-020 addendum (2026-10-07) reads "DB CPU under 50 % of its limit (< 0.25 cores)". With a 2-core limit, 50 % would
be 1 core, which changes its meaning. **Restated: DB CPU < 0.25 cores (absolute).** The working-set criterion
(< 410 MiB) and zero `db_slots_exhausted` are unchanged.

### Why B stays 80
- **C ≥ 200 stays a lower bound.** R1 attempt 2 measured it with the DB at 500m and reached the top step with no
  knee. A higher DB limit can only add capacity.
- **S\* = 200 stays.** It is defined by absolute DB CPU (< 0.25 cores), working set and slots. Less throttling cannot
  raise CPU per request (6.26 ms at step 160 when barely throttled).
- So B = floor(min(0.4 C, S\*/2)) = 80, with 0.4 C binding. **R1 is not re-run.**

### Pre-registered checks (before any rollout)
**Precondition after the DB rollout:** the `dependency-db-0` pod reads `cpu.max 200000 100000` at both levels (pod
cgroup and container). Otherwise stop.

At B = 80, over checkpoint B's 20-min window from a fresh `/swarm` (the 2026-10-13 repeat procedure: revision guard,
`/stop`, `/swarm` 160, split verified):

| # | Check | Pass |
|---|---|---|
| D1 | DB throttled periods, container (`container="dependency-db"`) and pod cgroup (`container=""`, `pod="dependency-db-0"`) | **≤ 0.5 % at both levels** |
| D2 | DB processes, `max_over_time(container_processes{container="dependency-db"})` | **≤ 12** (R1 step 160: 8; 6 background plus a few backends) |
| A1, A2 | As registered (ADR-027) | gated |

- **D1, D2, A1 and A2 are gated.** A3, A4, per-pod shares and users per pod are recorded.
- Then run 3 as registered: zero Nexus alerts in 60 min; the ADR-020 DB criterion (absolute); reported regardless.
- **Time-box (owner):** this is the one fix. If checkpoint B or run 3 fails again, it is reported, not fixed.

## Consequences
- **The DB rollout replaces its pod.** The StatefulSet changes its pod template, so `dependency-db-0` is recreated and
  its `emptyDir` data is re-initialised from `10-roles.sh` (the 20 rows; roles from the Secrets).
  - The dependency-db Application tracks `main`, so this happens at the `dev` → `main` gate merge.
  - Locust stays stopped through it. `/items` fails while the DB restarts.
- Node CPU limits rise by 1.5 cores, from about 8.6 to about 10.1 of 12. Limits can overcommit; requests are unchanged.
- **Scaling:** at the 5-replica bound in one namespace (S2, dev), the slots become 35 (7 pods × 5), about 2.6 cores of
  connection bursts. S2 drives `/work/cpu`, not `/items`, so `/items` stays at B, but the bound is recorded.
- The mechanism stays UNVERIFIED until D1 and D2 are read after the rollout.

## Result (2026-10-13): checkpoint B VALID, FAIL on A1 (dev), A2, D1 and D2
Evidence: `~/nexus-evidence/m1b-9/cb-run3-adr028-20261013T1323Z/` (`run.jsonl`, `premerge.json`, clock samples,
scripts with `cbrun.adr028.diff`).

**Rollout and precondition.**
- #121 → `dev` `183c5667`; #122 → `main` `01838a4e` (13:24:05Z). `dependency-db-0` replaced (Ready 13:27:07Z).
- Precondition PASS: the DB reads `cpu.max 200000 100000` at both levels; the four sample-api pods kept their uids and
  `100000 100000`.
- Revision guard PASS (about 13:28:25Z). `/stop`, then a fresh `/swarm` of 160; split 0.5 / 0.5 verified at
  13:31:09Z; the 18090 forward closed.

**Window W = 13:31:09Z → 13:51:09Z (1,200 s), B = 80 per namespace. Validity: VALID.**
- 0 missed `nexus-detection` evaluations; D_c +0.03 s (reliable).
- Canary reference 1.057 ms, never above +20 %.
- Locust `running` 160 (both classes); server rate 80.0 req/s per namespace; no heavy local work, no other forward,
  no dashboard listener.

**The fix against the failed window, same load** (11:37:45Z at 500m; 13:31:09Z at 2000m):

| # | Check | Pass | DB 500m | DB 2000m | Verdict |
|---|---|---|---|---|---|
| A1 | `/items` 50–100 ms, dev / prod | ≤ 3 % | 13.72 % / 14.03 % | **4.50 %** / 0.94 % | FAIL (dev) |
| A2 | sample-api throttled, worst pod, container / pod cgroup | ≤ 0.5 % | 0.07 % / 0.07 % | **8.34 % / 9.46 %** | FAIL |
| D1 | DB throttled, container / pod cgroup | ≤ 0.5 % | 50.0 % / 43.8 % | **1.00 %** / 0.26 % | FAIL (container) |
| D2 | DB processes, max | ≤ 12 | 26 | **16** | FAIL |
| A3 | max mixed p95, dev / prod | recorded | 289.6 / 276.4 ms | 33.8 / 33.0 ms | — |
| A4 | `/items` > 20 ms, dev / prod | recorded | 64 % / 65 % | 48 % / 50 % | — |
| — | DB CPU; per `/items` | recorded | 0.241 cores; 7.52 ms | 0.249 cores; 7.77 ms | — |

**Per pod at 2000m:**

| Pod | Users | A2 container | A2 pod cgroup | `/items` 50–100 ms | `/` > 50 ms |
|---|---|---|---|---|---|
| dev `tjqn4` | 40 | 8.34 % | 9.46 % | 8.90 % | 1 |
| dev `lddq8` | 40 | 0.25 % | 0.01 % | 0.11 % | 0 |
| prod `p7m8v` | 36 | 4.24 % | 2.83 % | 1.75 % | 1 |
| prod `8652k` | 44 | 2.03 % | 3.43 % | 0.28 % | 0 |

**What it shows.**
- **The improvement is large.** DB throttling fell 50 → 1 % (container) and 44 → 0.3 % (pod); DB processes 26 → 16
  (20 → 10 backends). A1 fell 13.7 → 4.5 % (dev) and 14.0 → 0.9 % (prod); p95 fell from about 280 ms to about 34 ms.
- **The ADR's prediction missed on both DB checks.** D1 (1.0 % at the container) and D2 (16 against R1's 8) did not
  reach their pre-registered values. CPU per `/items` stayed at 7.77 ms, not R1's 6.26 ms.
- **The slow tail moved to sample-api.** sample-api throttled at its one-core limit on three of four pods (A2 passed at
  500m). Dev's A1 fail is one pod: `tjqn4` carries 8.9 % of slow `/items` and the most throttling; `lddq8`, at the
  same 40 users, is clean. ADR-027 records the A2 failure. Cause UNVERIFIED.
- **The ADR-020 criterion (absolute):** DB CPU 0.249 cores, under 0.25. Working set and `db_slots_exhausted` were not
  read in this window.
- The feedback-loop mechanism (Context) stays UNVERIFIED: the DB's share fell with more quota, but D2 shows more
  overlap than R1 still.

**Time-box (owner):** this is the second failure. It is reported, not fixed. No run 3, no repeat, no tuning. The
open items go to `TASKS.md` Later (the client-side connection cost; replication on a server host).
