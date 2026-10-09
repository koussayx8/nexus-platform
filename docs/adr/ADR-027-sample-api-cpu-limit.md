# ADR-027: sample-api's CPU Limit Is One Core

## Status: Proposed (M1b-9; owner decision (a) after clean-window run 2, 2026-10-08; revised at the step-1 and step-1b gate reviews, 2026-10-09; rolled out at `main` `31e8d53` and `experiment/dev-state` `a4b8ef9`; checkpoint A VALID and PASS, 2026-10-09)

## Context
Two M1b-9 findings point at sample-api's 200m CPU limit: R1's throttling knee and run 2's bimodal `/items`.
Evidence: `~/nexus-evidence/m1b-9/r1/`, `clean60-run2/`, `cpu-limit/step1/`, `cpu-limit/step1b/`.

### The mechanism: two nested 200m quotas
Read on the node on 2026-10-09 for all four sample-api pods (Burstable QoS):
- The pod cgroup (`kubepods-burstable-pod<uid>.slice`) has `cpu.max 20000 100000`. Kubernetes sets it to the sum
  of the container limits.
- Inside it, the sample-api container scope also has `cpu.max 20000 100000`. The pause container has `max`.
- **Each level runs its own 100 ms period timer.** A burst can spend one level's 20 ms while the other still has
  room. The stall is counted only at the level that throttled:
  - pod level: `container_cpu_cfs_throttled_periods_total{container=""}`;
  - container level: `{container="sample-api"}`.
- **ADR-026's and R1's throttling figures are container-only, so they are lower bounds.** `ramp.py` reads
  `container="sample-api"` and `container="dependency-db"` only.

A request that meets an exhausted quota at either level waits for the next refill, up to 100 ms. That is the
50–100 ms slow mode of `/items`. `/items` takes the CPU bursts (a new DB connection per call, plus Python
handling), so it is the request that stalls. `/` stays clean.

### R1 (2026-10-07, `nexus-dev`, `DevUser` only), throttled periods at both levels
Container values reproduce `steps.csv`. Both levels were re-read with the same windows.

| Target req/s | sample-api container | sample-api pod cgroup | DB container | DB pod cgroup | p95 |
|---|---|---|---|---|---|
| 10 | 0.00 % | 0.00 % | 7.97 % | 0.21 % | 14 ms |
| 20 | 0.47 % | 0.42 % | 4.71 % | 0.99 % | 16 ms |
| 30 | 6.16 % | 4.28 % | 3.97 % | 0.74 % | 21 ms |
| 40 | 12.73 % | 5.46 % | 4.07 % | 1.47 % | 45 ms |

The knee at 40 (p95 > 2 × 14 ms, container throttling > 10 %) gave C = 30 and B = 12 req/s per namespace. At 40
req/s each pod averaged about 0.04 cores, a fifth of its limit: throttling came from bursts, not from load.

### Per pod in the timed windows
Locust keeps each user on one pod (keep-alive connections), so pods carry uneven load. Users per pod = the
pod's req/s (one request per second per user).

| Window | Pod | Users | Pod cgroup throttled | Container throttled | `/items` 50–100 ms | `/` > 50 ms |
|---|---|---|---|---|---|---|
| run 1 (06:58–07:05Z, 0.3.0) | dev `fmswt` | 10.4 | 2.40 % | 3.21 % | 4.71 % | 0.17 % |
| | dev `fzzsx` | 2.0 | 0.00 % | 0.00 % | 0.60 % | 0.00 % |
| | prod `8nf4x` | 8.3 | 4.25 % | 7.94 % | 25.29 % | 0.04 % |
| | prod `mj9j2` | 4.1 | 0.28 % | 0.00 % | 0.00 % | 0.00 % |
| warm-up 2 (09:50–10:06Z) | dev `2r79t` | 10.2 | 5.10 % | 12.59 % | 27.85 % | 0.00 % |
| | dev `79pdx` | 2.0 | 0.14 % | 0.15 % | 0.00 % | 0.06 % |
| | prod `m2jww` | 7.1 | **9.01 %** | 0.60 % | 24.98 % | 0.11 % |
| | prod `r5pzt` | 5.2 | 2.98 % | 3.02 % | 11.82 % | 0.00 % |
| run 2 (10:09–10:33Z) | dev `2r79t` | 10.2 | 5.82 % | 12.36 % | 21.84 % | 0.10 % |
| | dev `79pdx` | 2.0 | 0.00 % | 0.02 % | 0.17 % | 0.00 % |
| | prod `m2jww` | 7.1 | **8.62 %** | 0.50 % | 23.71 % | 0.09 % |
| | prod `r5pzt` | 5.1 | 2.86 % | 2.92 % | 11.55 % | 0.00 % |

- **Run 2 failed on this slow mode.**
  - At the 4 : 1 mix the mixed p95 is `/items`'s 75th percentile. It sat on the edge of the fast mode
    (≤ 20 ms).
  - A small host blip (canary +13 %, under the void line) moved it across the empty gap, about 30 ms (3 ε), and
    `NexusLatencyAnomaly` went pending in both namespaces. Mixed p95 before the blip: dev 45–58 ms, prod 19–20 ms.
- On every pod the slow share follows that pod's throttling at one level or the other. On each pod, slow
  `/items` ≤ throttled periods (both levels summed). The one exception is a single request on `fzzsx` in run 1.
- `m2jww` throttled at the pod level, not at the container level. Read at the container level only, it looked
  unexplained.
- Dev throttled about 4× prod at the container level because one dev pod carried 10 of 12 users.
- At the same load per pod (10 req/s) and similar CPU, R1's pods throttled 0–1 % and run 2's `2r79t` 12 %.
  Burst alignment within periods (fixed user phases, the user-to-pod split) is the likely cause. UNVERIFIED.
- Run 1's attribution is corrected: the 25 ms bucket edge amplified its jump, but the cliff is in the
  distribution. #111 (finer buckets) was necessary, not sufficient.
- DB throttling over the same windows:

  | Window | DB container | DB pod cgroup |
  |---|---|---|
  | run 1 | 6.43 % | 4.11 % |
  | warm-up 2 | 7.17 % | 2.37 % |
  | run 2 | 7.45 % | 1.63 % |

  It is shared by all pods, and pods without sample-api throttling (`79pdx`, `mj9j2`) show no slow mode.
- Logs on all four current pods (from 2026-10-08 09:00Z): 0 `db_slots_exhausted`, `db_error`, Traceback, retry,
  timeout or 5xx. Each pod: 0 restarts, working set ≤ 48 Mi of 128 Mi, 6–8 threads, 17–25 fds.

**UNEXPLAINED residual:** after the Locust resume (2026-10-08 18:06–18:25Z), `m2jww` (2.8 users) had 117 slow
`/items` against at most 62 throttled periods: 3 at the pod level, 59 at the container level. `/` stayed clean
(0 of 2,556 over 50 ms). The DB was throttled 9.38 % (container) and 3.32 % (pod cgroup) in that window.
**The DB is the first candidate.**

The load is not changed to avoid the cliff (faults and load are not shaped to the detector), and the latency rule
is not changed (that re-runs S5 under the pin rule). The owner chose to remove the cause: the limit.

## Decision

### The principle (decided wording, owner, 2026-10-08)
> Set sample-api's CPU limit by one principle: the most CPU the server process can use at once (check its worker
> and thread count), so normal traffic never throttles; the request stays as is. State how S2's CPU-burn fault can
> still saturate the pod.

**The normal-traffic scope and the S2 requirement are part of the decision** (owner, step-1 gate review). The scope
matters because "the most CPU the process can use at once", unscoped, is about 12 cores:
- `/work/cpu` hashes outside the GIL on a 40-token threadpool, so the process can use as many cores as the node
  has.
- That limit would let S2 load the node instead of saturating the pod.

Read from the image and the running pods (2026-10-08):
- `CMD ["uvicorn", "main:app", …]` with no `--workers`: **one server process**. `container_processes` = 1 in all
  four pods.
- CPython 3.12 (`python:3.12-slim`, a GIL build). `container_threads` = 6–7 per pod: the event loop plus the
  threadpool threads started so far.
  - Starlette runs plain `def` handlers on AnyIO's default limiter of 40 tokens (`CapacityLimiter(40)`).
  - That was read in AnyIO 4.13.0's source locally. The image has 4.15.1, same 4.x default, UNVERIFIED in that
    exact version.
  - `/items` holds one of 5 DB slots.
- `/` (async) and `/items` (threadpool) run Python code, which holds the GIL: at most **one core** at once.
  Their GIL-free parts (libpq calls, socket I/O) are small next to the Python work (UNVERIFIED per call). Check A2
  measures the result.

So the limit is **1000m**, and the request stays 50m.
- The pod cgroup's quota follows the sum of the container limits, so both levels become 1000m. The checkpoint A
  precondition verifies this.
- With one core of quota, a 100 ms period holds 100 ms of CPU. Throttling then needs the process to keep more than
  one core busy for a whole period, which bursty GIL-bound work cannot do.
- R1 measured about 0.023 cores for 2 pods at 10 req/s.

ADR-016 made the same change for Grafana (200m → 1000m) for the same cause.

### S2 still saturates the pod
S2 is "a Locust ramp to twice per-pod capacity on `/work/cpu` for 10 min" (spec, scenario table).
- `/work/cpu` is a plain `def` on the threadpool. It hashes a 64 KiB block 64 times (about 10 ms of CPU), and
  CPython's `hashlib` releases the GIL for updates that large.
  - Measured locally (Python 3.12.3, the same block): 1 thread used 0.95 cores, 4 threads 3.85 cores.
  - Under S2 the process's demand is not bounded by one core.
- The 1000m quota caps it. Every period's quota is spent; the run queue and the threadpool queue grow; latency
  and in-flight rise; CPU sits at the limit. That is the `cpu_saturation` signature S2 needs (`NexusCpuAnomaly`,
  `NexusLatencyAnomaly`).
- **Estimate until R2 (M2/S2):** per-pod capacity is about 1 core ÷ 10 ms ≈ 100 req/s on `/work/cpu`, so S2 is
  about **200 req/s per pod**. R2 measures the real value under this limit.
- **Can one Locust worker drive it?** Probably, UNVERIFIED.
  - At 2 pods × 200 req/s plus B, about 420 req/s.
  - At the canary's 1.2–1.3 ms of worker CPU per request, that is about 0.5 core of the worker's 1000m limit.
  - `HttpUser` with `constant_throughput(1)` needs about 400 users.
  - `/work/cpu` waits longer per call than the baseline paths, so the per-request cost may differ.
  - R2 checks it with the server-rate rule. If one worker cannot drive it, a second worker is an ADR change.
- **Reading to settle at M2:** the spec does not say whether "twice per-pod capacity" is per pod or the
  namespace's total. With scale +1 or +2 from 2 replicas and `scale_v1`'s 70 % criterion (see Consequences):
  - The namespace-total reading (about 200 req/s, about 2 cores) is met at 3 replicas (about 667m per pod) and 4
    (about 500m).
  - The per-pod reading (about 400 req/s, about 4 cores) is not met within the 5-replica bound (800m per pod).

### Checkpoint A: after both rollouts, at the current B (gates the R1 re-run)
**Precondition.** After the rollout, `cpu.max` reads `100000 100000` on all four new pods, at both levels:
- the pod cgroup slice;
- the sample-api container scope.

It is read on the node with read-only `cat`. **Otherwise stop.**

**The window.**
- One 20-min window, both namespaces at B = 12 at once.
- It starts after both rollouts (prod at the `main` merge, dev after the forward-merge) and a settle of ≥ 5 min
  after the later roll's second new pod is Ready.
- **Validity is settled before A1–A4 are read.**

**Void set** (plan §2, long-run rules). A void window is repeated, not failed:
1. `boot_id` changes, or k3s MainPID/NRestarts changes.
2. A new container restart or pod uid on the measured path (plan §2 rule 2).
3. An Application revision changes, or a `verify-state.sh` run overlaps the window.
4. More than 3 missed `nexus-detection` evaluations.
5. Drift-corrected D_c > 90 s, with r read at both ends, each |r| ≤ 0.05 (plan §2).
6. The host-speed canary is sustained above +20 % (rule 5).
7. Locust is outside ±10 % of B in either namespace.
8. Heavy local work (rule 7).
9. Dashboards or UIs open (rule 10).

| # | Check, per namespace or per pod, over the window `W` | Pass | Run 2 (200m) |
|---|---|---|---|
| A1 | `/items` 50–100 ms share per namespace: `(increase(bucket{le="0.1"}) − increase(bucket{le="0.05"})) ÷ increase(count)`, `handler="/items"` | **≤ 3 %** | dev 18 %, prod 18 % |
| A2 | Throttled periods **per pod, at both levels**: `increase(container_cpu_cfs_throttled_periods_total) ÷ increase(container_cpu_cfs_periods_total)` for `container="sample-api"` **and** for `container=""` (pod cgroup) | **every pod ≤ 0.5 % at both** | max 12.36 % (container, `2r79t`); 8.62 % (pod, `m2jww`) |
| A3 | Mixed p95 inside the fast mode: `max_over_time(namespace:nexus_sample_api_latency_p95:2m[W])` | **≤ 20 ms** | dev 45–58 ms, prod 19–20 ms |
| A4 | Margin: `/items` share above 20 ms per namespace, `1 − increase(bucket{le="0.02"}) ÷ increase(count)` | **≤ 10 %** | prod 20 %, dev 35 % |

- The `http_request_duration_seconds` series use `job="sample-api"`.
- Why A4: the mixed p95 crosses the gap when more than 25 % of `/items` exceed 20 ms (5 % of all requests). At
  10 % or less, the slow share must grow 2.5× to cross.
- **Recorded, not gated:** DB throttling at both levels; `/` above 50 ms per pod; users per pod.
- **All four pass in both namespaces: the R1 re-run may be proposed. Any fails: stop, report, no R1.**
- **A1 fails while A2 passes: stop. The DB is the first candidate** (see the residual above).

### Before the R1 re-run: `ramp.py` reads both levels (#117; rules decided at the #117 gate, 2026-10-09)
- **Columns.** `ramp.py` reads throttling at both levels:
  - sample-api: `container="sample-api"` and the pod cgroup;
  - the DB: `container="dependency-db"` and the pod cgroup;
  - the Locust worker: `container="locust"` and the pod cgroup.
- **Knee.** sample-api throttled periods above 10 % at **either** level. DB throttling, at both levels, is recorded,
  not a knee.
- **Locust-bound stop.** The worker's throttled periods above 10 % at either level mean the step is Locust-bound. The
  ramp stops; C ≥ the last good step, a lower bound; B follows by the frozen rule from that bound.
- **Blind stop.** An empty answer on any judged criterion means the step cannot be judged. The judged criteria:
  - the server rate;
  - Locust's requests, failures, `/items` failures and p95, the first step's p95 included;
  - sample-api or worker throttling at either level.

  The ramp stops with `capacity: null` (exit 1). **The run is void and re-run; it never sets C.** An empty answer on a
  record-only series is recorded as empty and stops nothing.
- **Context.** Prometheus is read as `kubectl --context default -n monitoring get --raw …`.

### R1 re-run and the new B (decided at the #117 gate)
- **Locust is a knee candidate.** With sample-api's limit raised, the single worker (1000m limit) may run out
  first. The Locust-bound stop above covers it.
- **Canary during the ramp.** Plan §2 rule 5 (host speed, void) applies only while the worker's CPU is under 0.5
  core. Above that, a canary rise is read as Locust-bound.
- **No knee by step 200:** C ≥ 200 as a lower bound, and B = floor(0.4 × 200) = 80 by the frozen rule. No re-run with
  a higher `--max`.
- **B ceiling: none.** B = floor(0.4 × C), or floor(0.4 × its lower bound).
- **After R1:** `/stop`, and Locust stays idle. No resume at B = 12. The next traffic step is the new B's warm-up at
  checkpoint B.
- **The canary reference at the new B:** each run's reference is the median of its own first 5 minutes at the new
  B. Values from B = 12 (1.184 ms and later) are not used as references.

### R1 attempt 1 (2026-10-09): VALID, no C (the class filter was not applied)
Evidence: `~/nexus-evidence/m1b-9/r1-rerun/` (`start-record.md`, `r1-run.jsonl`, `ramp/`, `result.txt`, `smoke/`).

**The run.**
- `ramp.py` from `dev` `3a096f9` (blob `1d4fbf1d…`). Locust was driven through the agent's 18090.
- Start snapshot at 13:23:47Z. Ramp 13:25:58Z → 13:30:01Z.
- **Validity: VALID.**
  - boot and k3s unchanged; 11 measured-path pods unchanged; Application revisions unchanged.
  - 0 missed evaluations. r_start +0.0231 and r_end +0.0235 agree, so D_c is reliable: −0.2 s.
  - Canary ok; no heavy work, no other port-forward, no UI.
- **Verdict:** `capacity: null`. Knee at step 10, "server rate 4.9 < 95% of 10"; Locust 9.81 req/s, flag +100.5 %.
- **Nothing was derived from it:** no C, no S\*, no B.

**Cause, VERIFIED.**
- `step-10.json` shows Locust ran both classes 5 : 5 (`dev:` 600, `prod:` 600 requests).
- Locust 2.46.7 (`runners.py`): `MasterRunner.start` builds its users dispatcher only when none exists. On a running
  test, `/swarm` re-dispatches the new user count with the old classes. Its answer still echoes the requested
  `user_classes`. Only `stop()` resets the dispatcher.
- L2's `--autostart` keeps the master running both classes, so attempt 1's first `/swarm` landed on a running test.
  On 2026-10-07 the master was idle.
- A local Locust 2.46.7 smoke (master + worker) reproduced it and showed the fix (`smoke/smoke-fix.out`):
  - `/swarm` on the running swarm: `swarm_ok` 0, 6 non-selected requests, `/tasks` DevUser share 0.5;
  - `/stop`, then `/swarm`: `swarm_ok` 1, 0 non-selected requests, share 1.0;
  - the next step stays DevUser only.
- The residual's pod before the swarm was replaced: dev `lddq8`, 3.16 % of `/items` at 50–100 ms, 4.92 users, at B
  over 13:13–13:23Z. That remains the "before" reading for the re-run. There is no resume before it.

### R1: the load shape is judged (owner, R1 attempt 1 gate)
- **Start:** `ramp.py` posts `/stop`, waits for `stopped`, then starts step 1. A master restart re-applies B with
  both classes through `--autostart`, so an idle Locust is not a durable state. This start covers it.
- **Swarm state, every step after the settle:**
  - state `running`;
  - `user_count` = the target;
  - `/tasks` class shares summing to 1 over the selected classes only.

  Not confirmed, or no answer: a blind stop.
- **Per-class stats, every step:** any request under a non-selected class's name (not `<env>:`) is a blind stop.
  Empty selected-class stats are a blind stop.
- **Rate, every step:** Locust's selected-class rate against the target namespace's server rate. Outside ±10 % is a
  blind stop.
- `ramp.py` refuses classes that do not serve the target namespace before sending anything to Locust.

### Checkpoint B: the warm-up at the new B (gates clean-window run 3)
- If the R1 re-run changes B, the 20-min warm-up at the new B is read with the same void set and precondition.
- **Gated: A1 and A2 only** (A2 per pod at both levels), with the same pass values. Any fails: stop, no run 3.
- **Recorded, not gated: A3 and A4.** They were set at B = 12 and may rise with load. **Run 3 judges latency.**

### Clean-window run 3
**Run 3 is reported regardless of its outcome**, pass or fail, with the full record (plan §2 and §3).

### Access to Locust
The agent drives Locust through **its own port-forward on local port 18090**, never Koussay's on 18089 (plan §2
rule 10). Prometheus is read only through the API server's service proxy.

### What does not change
- Unchanged: the request (50m), the memory limit (128Mi), the image (0.3.1, `sha256:3321d6fa…f268`), the buckets,
  and the detection rules.
- The S5 pin holds: no pinned expression reads the CPU limit or throttling (checked at `d351d964`). The CPU signal
  reads `container_cpu_usage_seconds_total`, which is usage.
- The DB limit (500m) and its criterion (ADR-020 addendum).
- **No ResourceQuota or LimitRange** in `nexus-dev` or `nexus-prod` (read live 2026-10-09; none in `nexus-data`
  or `nexus-load`; none in Git). Nothing caps the new limit.

## Checkpoint A result (2026-10-09): VALID, PASS in both namespaces
Evidence: `~/nexus-evidence/m1b-9/checkpoint-a/` (`start-record.md`, `warmup.jsonl`, `window.jsonl`, `result.txt`,
`lddq8-vs-db-per-minute.txt`).

**Before the window:**
- Precondition: all four new pods read `cpu.max 100000 100000` at the pod cgroup and the container scope (prod
  ~08:00Z, dev ~08:46Z).
- Koussay confirmed the laptop regime (typed). Locust resumed at 09:05:57.80Z through the agent's port-forward on
  18090.
- Plan §3's warm-up completed at 09:26:15Z: all Z present with |Z| < 0.02, no Nexus alerts.
- The traffic step raised one Incident per namespace, both exported.

**W = 09:28:37Z → 09:48:37Z (1,200 s), both namespaces at B = 12. Validity, settled first: VALID.**

| Plan §2 rule | Reading |
|---|---|
| 1 | boot_id and k3s (`MainPID=233`, `NRestarts=0`) the same at all four clock reads |
| 2 | 11 measured-path pods: uids and restarts unchanged |
| 3 | 0 missed `nexus-detection` evaluations. r_start +0.0083, r_end +0.0000: they differ by more than 0.005, so D_c is unreliable and "missed evaluations, boot_id and the restart checks decide alone" (plan §2). Raw D +11.5 s, D_c +6.5 s; both \|r\| ≤ 0.05 |
| 4 | Application revisions unchanged; no `verify-state.sh` run |
| 5 | Canary start median 1.021 ms (≤ 1.421 ms). During W: 0.83–1.09 ms; never above +20 % |
| 6 | Locust `running` 24 at every minute; server rate 11.79–12.00 req/s |
| 7, 10 | No heavy local work; no port-forward but the agent's 18090; no dashboard listeners |

**A1–A4:**

| Namespace | A1 `/items` 50–100 ms (≤ 3 %) | A3 max mixed p95 (≤ 20 ms) | A4 `/items` > 20 ms (≤ 10 %) |
|---|---|---|---|
| nexus-dev | 2.03 % | 17.80 ms | 3.31 % |
| nexus-prod | 0.00 % | 17.54 ms | 0.07 % |

| Pod | Users | A2 container | A2 pod cgroup | `/items` 50–100 ms | `/` > 50 ms |
|---|---|---|---|---|---|
| dev `lddq8` | 4.95 | 0 / 5,395 | 0 / 6,385 | 4.87 % | 0 |
| dev `tjqn4` | 6.93 | 0 / 4,615 | 0 / 5,313 | 0.00 % | 0 |
| prod `8652k` | 6.93 | 0 / 5,890 | 0 / 5,896 | 0.00 % | 0 |
| prod `p7m8v` | 4.95 | 0 / 4,444 | 0 / 4,475 | 0.00 % | 0 |

- **No throttled period at either level on any pod.** The slow mode fell from 18–25 % on the affected pods (run 2)
  to 0 on three pods.
- **Recorded, not gated:** DB throttled 8.34 % (container) and 2.27 % (pod cgroup).
- **Residual, UNEXPLAINED (the DB is the first candidate):** dev `lddq8` had 1–5 slow `/items` per minute (58 of
  1,185, 4.89 %) with no sample-api throttling. Meanwhile the DB throttled steadily: 15–21 periods per minute at
  the container level, 2–7 at the pod level.
  - **Phase hypothesis, UNVERIFIED:** each Locust user sends one request per second at a fixed phase. The users on
    `lddq8` may have `/items` phases that meet the DB's throttled periods.
  - A swarm restart re-assigns users to pods. At R1's restart, the pod carrying the residual is recorded before and
    after.
- **R1 may be proposed** (owner, checkpoint A gate review). `ramp.py` now reads throttling at both levels for
  sample-api and the DB.

## Consequences
- **R1 is re-run and B re-derived** (`TASKS.md`, R1 gate), after the `ramp.py` PR (both levels; knee on either sample-api level).
  - The knee may move to the DB, the DB slots, latency, or Locust.
  - L2's `--users` and the frozen-B checks follow the new B. Then the warm-up (checkpoint B) and clean-window
    run 3.
- Node CPU limits rise from 5.4 to about 8.6 of 12 cores (four sample-api pods, +0.8 each), plus 1 core per
  namespace during a rollout surge.
  - At C4's 5-replica bound in both namespaces they would exceed 12.
  - Limits can overcommit. Requests, and so scheduling, do not change.
- **M2: `scale_v1`'s criterion, "CPU per pod below 70 % of limit", now means 700m (was 140m).** The S2 reading
  above decides whether +1 or +2 can meet it.
- The pod-template change rolls both Deployments (maxSurge 1, maxUnavailable 0): prod at the `main` merge, dev
  after the forward-merge into `experiment/dev-state`. A rollout is a traffic event; no timed run spans it.
- The edit is under `apps/sample-api/**`, inside `ci.yml`'s path filter. The `main` merge builds and signs one
  more image from unchanged code. Nothing pins it; its run id and digest are recorded as NOT deployed.
- The M2 Later item "revisit the sample-api CPU limit and the per-request connection cost" is half done. The
  per-request DB connection stays: it is how S5's error reaches the log (ADR-020).
