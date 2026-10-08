# ADR-027: sample-api's CPU Limit Is One Core

## Status: Proposed (M1b-9, branch `feat/m1b-9-cpu-limit`; owner decision (a) after clean-window run 2, 2026-10-08)

## Context
Two M1b-9 findings point at sample-api's 200m CPU limit (20 ms of CPU per 100 ms CFS period per pod).

1. **R1's knee is a throttling knee** (2026-10-07, ADR-026, `~/nexus-evidence/m1b-9/r1/`). At 40 req/s each
   pod averaged about 0.04 cores, a fifth of its limit, yet CFS throttled 12.8 % of its periods and p95 tripled
   (14 → 45 ms). Throttling rose before CPU did: 0.0 % at 10 req/s, 0.5 % at 20, 6.2 % at 30, 12.8 % at 40.
   Short bursts (one DB connection per `/items` call, Python request handling) fill the 20 ms budget.
   C = 30 and B = 12 req/s per namespace came from that knee.
2. **Clean-window run 2 failed on a bimodal `/items`** (2026-10-08, `~/nexus-evidence/m1b-9/clean60-run2/`).
   Over 10:09–10:33Z at B, `/items` had a fast mode at or under 20 ms and a slow mode at 50–100 ms:

   | Namespace | `/items` ≤ 20 ms | 20–50 ms | 50–100 ms | sample-api throttled periods | mixed p95 |
   |---|---|---|---|---|---|
   | nexus-prod | 80 % | 1 % | 18 % | about 2 % | 19–20 ms |
   | nexus-dev | 65 % | 12 % | 18 % | about 8 % | 45–58 ms |

   - `/` stayed under 5 ms. At the 4 : 1 mix the mixed p95 is `/items`'s 75th percentile, which sat on the edge
     of the fast mode. A small host blip (canary +13 %, under the void line) moved it across the empty gap, about
     30 ms (3 ε), and `NexusLatencyAnomaly` went pending in both namespaces.
   - The slow mode matches throttling stalls: a request that meets an exhausted quota waits for the next period
     (up to 100 ms). The namespace with more throttling has the larger slow tail.
   - This corrects run 1's attribution: the 25 ms bucket edge amplified that jump, but the cliff is in the
     distribution. #111 (finer buckets) was necessary, not sufficient.

The load is not changed to avoid the cliff (faults and load are not shaped to the detector), and the latency
rule is not changed (that re-runs S5 under the pin rule). The owner chose to remove the cause: the limit.

## Decision

### The principle
**The CPU limit is the most CPU the server process can use at once on normal traffic**, so normal traffic
never throttles. The request stays 50m.

Read from the image and the running pods (2026-10-08):
- `CMD ["uvicorn", "main:app", …]` with no `--workers`: **one server process**. `container_processes` = 1 in all
  four pods.
- CPython 3.12 (`python:3.12-slim`, a GIL build). `container_threads` = 6–7 per pod: the event loop plus the
  threadpool threads started so far. Starlette runs plain `def` handlers on AnyIO's default limiter of 40
  tokens (`CapacityLimiter(40)`, read in AnyIO 4.13.0's source locally; the image has 4.15.1, same 4.x
  default, UNVERIFIED in that exact version). `/items` holds one of 5 DB slots.
- `/` (async) and `/items` (threadpool) run Python code, which holds the GIL: at most **one core** at once.
  Their GIL-free parts (libpq calls, socket I/O) are small next to the Python work (UNVERIFIED per call;
  check A2 below measures the result).

So the limit is **1000m**. With one core of quota, a 100 ms period holds 100 ms of CPU: throttling now needs
the process to keep more than one core busy for a whole period, which bursty GIL-bound work cannot do. A pod
throttles only when it is saturated, not when two requests overlap. R1 measured about 0.023 cores for 2 pods
at 10 req/s, so normal traffic is far from one core per pod.

ADR-016 made the same change for Grafana (200m → 1000m) for the same cause.

### S2 still saturates the pod
S2 is "a Locust ramp to twice per-pod capacity on `/work/cpu` for 10 min" (spec, scenario table). Per-pod
capacity is defined against this limit, so S2 saturates by construction:
- `/work/cpu` is a plain `def` on the 40-token threadpool. It hashes a 64 KiB block 64 times (about 10 ms of
  CPU), and CPython's `hashlib` releases the GIL for updates that large. Measured locally (Python 3.12.3, the
  same block): 1 thread used 0.95 cores, 4 threads 3.85 cores. Under S2, the process's demand is not bounded
  by one core.
- The 1000m quota caps it: every period's quota is spent, the run queue and the threadpool queue grow,
  latency and in-flight rise, and CPU sits at the limit. That is CPU saturation of the pod, the
  `cpu_saturation` signature S2 needs (`NexusCpuAnomaly`, `NexusLatencyAnomaly`).
- Per-pod capacity is about 1 core ÷ 10 ms ≈ 100 req/s on `/work/cpu`, so S2 drives about 200 req/s per pod.
  R2 (deferred to M2/S2, ADR-026) measures the real value under this limit.
- Without a limit, S2 could take most of the node's 12 cores and starve its neighbours instead of
  saturating one pod. The limit is what keeps S2 a pod-level fault.

### Pre-registered checks (before any rollout; if a check fails after the rollout, stop)
**Checkpoint A** gates the R1 re-run. Per namespace, after its two new pods are Ready on 1000m:
- Locust at the current B (master `running` with 24 users; server rate within ±10 % of 12 req/s).
- One 20-min window starting ≥ 5 min after the second new pod is Ready. Heavy local work is kept out of it
  (plan §2 rule 7).
- If the host-speed canary is sustained above +20 % (plan §2 rule 5) inside the window, the window is void
  and is repeated. A void window is not a failure.

| # | Check (20-min window `W`, namespace `ns`) | Pass | Run 2 (200m) |
|---|---|---|---|
| A1 | `/items` 50–100 ms share: `(increase(bucket{le="0.1"}) − increase(bucket{le="0.05"})) ÷ increase(count)`, `handler="/items"` | **≤ 3 %** | 18 % both |
| A2 | sample-api throttled periods: `increase(container_cpu_cfs_throttled_periods_total) ÷ increase(container_cpu_cfs_periods_total)`, `container="sample-api"` | **≤ 0.5 %** | dev 8 %, prod 2 % |
| A3 | Mixed p95 inside the fast mode: `max_over_time(namespace:nexus_sample_api_latency_p95:2m[W])` | **≤ 20 ms** | dev 45–58 ms |
| A4 | Margin: `/items` share above 20 ms, `1 − increase(bucket{le="0.02"}) ÷ increase(count)` | **≤ 10 %** | prod 20 %, dev 35 % |

- Sums are over the namespace's sample-api pods; the `http_request_duration_seconds` series use
  `job="sample-api"`.
- Why A4: the mixed p95 crosses the gap when more than 25 % of `/items` exceed 20 ms (5 % of all requests).
  At 10 % or less, the slow share must grow 2.5× to cross.
- **All four pass in both namespaces → the R1 re-run may be proposed. Any fails → stop, report, no R1.**

**Checkpoint B** gates clean-window run 3. If the R1 re-run changes B, the same four checks are read at the
new B over the 20-min warm-up, with the same pass values. Any fails → stop, no run 3.

### What does not change
- The request (50m), the memory limit (128Mi), the image (0.3.1, `sha256:3321d6fa…f268`), the buckets, and the
  detection rules. The S5 pin holds: the five pinned files are untouched.
- The DB limit (500m) and its criterion (ADR-020 addendum).

## Consequences
- **R1 is re-run and B re-derived** (`TASKS.md` Later, R1 gate). The knee may move to another criterion (DB
  slots, DB CPU, latency). L2's `--users` and the frozen-B checks follow the new B; then the warm-up and
  clean-window run 3.
- Node CPU limits rise from 5.4 to about 8.6 of 12 cores (four sample-api pods, +0.8 each), plus 1 core per
  namespace during a rollout surge. At C4's 5-replica bound in both namespaces they would exceed 12. Limits
  can overcommit; requests (and scheduling) do not change.
- The pod-template change rolls both Deployments (maxSurge 1, maxUnavailable 0): prod at the `main` merge,
  dev after the forward-merge into `experiment/dev-state`. A rollout is a traffic event; no timed run spans it.
- The edit is under `apps/sample-api/**`, inside `ci.yml`'s path filter: the `main` merge builds and signs
  one more image from unchanged code. Nothing pins it; its run id and digest are recorded as NOT deployed.
- The M2 Later item "revisit the sample-api CPU limit and the per-request connection cost" is half done:
  the per-request DB connection stays (it is how S5's error reaches the log, ADR-020).
