# ADR-026: The Locust Load Baseline and the R1 Calibration

## Status: Proposed (M1b-9; L1 #106, R1 run 2026-10-07, L2 `feat/m1b-9-baseline`; plan `~/nexus-m1b9-plan.md`)

L1 recorded the design. L2 records R1, the frozen baseline and the session procedure. The M1b-9 closing PR
adds the 60-min clean-baseline results; the S5 demo adds the first-fire time.

## Context
Spec §3 runs Locust as "steady baseline traffic at all times", and §25 sets the baseline "in M1 to
about 40 % of two-replica capacity, then frozen". At idle only the CPU signal has data (M1b-7
gate): `/` and `/items` have no series, so the request, error-ratio, p95 and in-flight Z-scores
and their baselines are empty, and S5 cannot alert. M1b-9 builds the load, measures capacity (R1),
freezes the baseline, and checks the change-16 DB criteria (ADR-020). The S5 exit demo then runs
under that baseline.

## Decision

### Where Locust runs
- In-cluster, namespace `nexus-load`, managed by the `platform` Application (`platform/load/`, spec §3
  and §25). Not on the host: a port-forward on the request path would distort latency.
- One master (`locust-master`, web UI and API on 8089, workers on 5557) and one worker.
  `--class-picker`, so a ramp can select one environment.
- Image `locustio/locust` 2.46.7, pinned by its index digest `sha256:8492fcaa…7259f`. It runs as
  `locust`, UID/GID 1000, which has a passwd entry in the image (read from the image layer). That
  matters: the #100 operator crash came from a UID with no passwd entry.
- Restricted Pod Security, read-only root filesystem with `/tmp` on an `emptyDir`, no
  ServiceAccount token.
- Resources: master 100m/256Mi requests, 500m/512Mi limits; worker 200m/256Mi, 1000m/512Mi.
- L1 starts idle (no `--autostart`). R1 drives the master through its API over a port-forward
  (an `ask` rule). L2 freezes the baseline with `--autostart`, so a Locust restart returns to
  baseline by itself.

### The mix (owner, M1b-9 gate, 2026-10-06)
- `/` : `/items` = **4 : 1**. `/items` is 20 % of requests.
  - Source: the S5 shape in `platform/observability/tests/nexus-detection.test.yaml` case 4, where
    20 % of requests become 5xx. Under S5 every `/items` call fails, so the S5 error ratio is about 20 %.
  - The load is not shaped to the detector. S5's first firing checks promtool's prediction of
    +105 s after the onset (ADR-024).
- **`/work/cpu` is never called.** It is S2's load generator, not a path a scenario breaks, and
  leaving it out keeps the CPU baseline's variance low (M1b plan gate, 2026-09-28).
- The cycle is fixed, not random: every user runs `/`, `/`, `/`, `/`, `/items`. Users start at
  different positions in that cycle and at different phases within the second (golden-ratio
  spacing). Synchronised bursts would hit sample-api's 5 DB slots per pod (ADR-020) and create
  `db_slots_exhausted` 503s that come from the load, not from the system.
- One request per second per user (`constant_throughput(1)`). Two classes, `DevUser` and
  `ProdUser`, with equal weight, so the same rate goes to each namespace. Stats are named
  `<env>:<path>`.
- Local smoke (2026-10-06):
  - 10 users against the sample-api code with no DB sent 144 `/` and 36 `/items` requests.
    `/items` was 503 every time, a 20 % failure share.
  - 20 users with both classes gave 10.1 req/s to each environment.

### R1: capacity of the mix on `nexus-dev`'s 2 replicas
- `experiments/calibration/ramp.py run`: `DevUser` only.
  - Steps of +10 req/s from 10. Each step: 1 min settle, then Locust's statistics are reset, then a
    2 min measure window. Cap 200.
  - **Achieved rate (the knee input) is server-side** (owner, #106 gate): the counter behind
    `namespace:nexus_sample_api_requests:rate2m`, with its selector, summed for `nexus-dev`, as
    `sum(rate(http_requests_total{…}[120s]))` over exactly the measure window, evaluated at the
    window's end time. If Prometheus does not answer (or answers empty), the step cannot be judged
    and the ramp stops with no capacity.
  - **Prometheus transport** (owner, #107 gate): `kubectl get --raw` on the API server's service proxy,
    `/api/v1/namespaces/monitoring/services/http:observability-kube-prometh-prometheus:9090/proxy/api/v1/query`,
    with the PromQL URL-encoded and `time=` at the window's end. It is read-only. Never a port-forward, and never
    a local `kubectl proxy`, which would accept cluster writes as plain HTTP outside the guard patterns. A failed
    call (non-zero exit, timeout, unparsable answer) counts as no answer.
  - **Locust's window average is the cross-check:** the window's requests ÷ its measured seconds.
    A difference from the server rate above 5 % is flagged in the row and the verdict; it is not a
    knee. The signed difference is recorded at every step.
  - **Expected low bias of the cross-check, recorded and not acted on:** the master's reset does not
    reach the workers (3 s reports), and its API caches for 2 s, so the window count reads a few
    seconds of requests low. A 20 s local smoke read 9.0 against 10 req/s; over 120 s it is a few
    percent. This is why Locust's count does not decide the knee: a few percent low sits at the 95 %
    rule and could declare a false knee.
  - Locust's `total_rps` is a short-window snapshot, recorded for display only.
  - **p95 and the failure ratio stay Locust's**, from the same window (reset after the settle minute).
  - Every step records sample-api and dependency-db CFS throttling, DB CPU and peak working set,
    the worker's CPU and the node's CPU. An empty Prometheus answer is recorded as empty, never as 0.
- **Knee:** the first step with any of:
  - failures > 1 %;
  - any `/items` failure;
  - server-side achieved rate < 95 % of the target;
  - p95 > 2 × the first step's;
  - sample-api throttled periods > 10 %.

  C = the last step before the knee. The ramp stops at the knee, or at a step it cannot judge,
  and posts `/stop`.
- **B = floor(0.4 × C) req/s per namespace**, frozen in L2.
- DB throttling is recorded, not a knee criterion. The DB limit or the criterion is decided at
  the R1 gate, before L2 freezes B (owner, 2026-10-06). The criterion already fails at idle:
  30.3 % throttled over 30 min on 2026-10-06. `container_cpu_cfs_throttled_seconds_total` is
  absent for the DB container, so the throttled-seconds alternative in `TASKS.md` is unavailable.

### R1 result (2026-10-07, 11:47:29–11:59:51Z; accepted at the R1 gate)
`ramp.py` came from `dev` `ce5770f8`. R1 was valid on every discard rule: boot and k3s unchanged, no restart
or new pod uid, pause gap 38.9 s (under 90 s), no Application revision change. Evidence:
`~/nexus-evidence/m1b-9/r1/`.

| Target | Server req/s | p95 | sample-api throttled | sample-api CPU (2 pods) | DB throttled | DB CPU | DB working set |
|---|---|---|---|---|---|---|---|
| 10 | 9.82 | 14 ms | 0.0 % | 0.023 | 8.0 % | 0.027 | 42.6 MiB |
| 20 | 19.59 | 16 ms | 0.5 % | 0.046 | 4.7 % | 0.039 | 42.5 MiB |
| 30 | 29.47 | 21 ms | 6.2 % | 0.067 | 4.0 % | 0.048 | 42.6 MiB |
| 40 | 39.33 | 45 ms | 12.8 % | 0.085 | 4.1 % | 0.060 | 42.8 MiB |

- No failures and no `/items` failures at any step. Locust's window average was within ±0.35 % of the
  server rate (no flags).
- **Knee at 40:** p95 45 ms > 2 × 14 ms, and sample-api throttled 12.8 % > 10 %. **C = 30 req/s.**
- **The knee is throttling-driven, not CPU-bound.** At 40 req/s each pod averaged about 0.04 cores against
  its 200m limit, yet CFS throttled 12.8 % of its periods and p95 tripled (14 → 45 ms). Short bursts (one
  DB connection per `/items` call, Python request handling) fill the 20 ms of CPU each pod may use per
  100 ms period. No sample-api limit change now (owner, R1 gate). M2 revisits the limit and the
  per-request connection cost, and R1 is re-run if either changes (`TASKS.md` Later).
- **B = floor(0.4 × 30) = 12 req/s per namespace**: 9.6 req/s on `/` and 2.4 req/s on `/items`. This is
  below the 20 req/s of the promtool fixtures. The S5 error share stays 20 % (the 4 : 1 mix).

### L2: the frozen baseline (owner, R1 gate, 2026-10-07)
- `platform/load/master.yaml`: `--autostart --users=24 --spawn-rate=4 --expect-workers=1`. Equal class
  weights give 12 users, so 12 req/s, per namespace. Local smoke: 9.6 + 2.4 req/s per environment,
  exactly; autostart waited for the worker.
- **No self-heal.** No probe or job restarts a stopped swarm, because that would override an emergency Stop.
- **Known behaviour, kept on purpose:** a worker that misses the master's heartbeat for 60 s (a VM pause)
  exits. The master then stops the test and stays `stopped` after the worker reconnects (seen live on
  2026-10-07 after the overnight sleep, and reproduced locally).
- **Each session with timed runs starts by resuming the swarm**, then the 20-min warm-up. A VM pause breaks
  Prometheus's lagged baseline anyway. Resume = `POST /swarm` with `user_count=24`, `spawn_rate=4`,
  `user_classes=DevUser` and `user_classes=ProdUser`, through the port-forward (local smoke: back to 24 users).
- **Every timed run first checks Locust is running at B in both namespaces:** master `running` with 24
  users (service proxy), and the server-side `rate(http_requests_total[2m])` per namespace within ±10 % of 12.

### R2
Deferred to M2/S2 (owner, 2026-10-06). The S5 exit does not need it.

## Consequences
- Locust adds up to 1.5 CPU of limits on the node (3.9 → 5.4 of 12).
- The baseline start is a real traffic step and can raise Nexus alerts and Incidents. Its warm-up
  rules are in the M1b-9 plan (§3).
- Until M3's NetworkPolicies, any pod can reach the master's API. The runner reaches it only
  through a port-forward (§18).
- Merging L2 starts the baseline in both namespaces: a real traffic step that can raise
  `NexusTrafficAnomaly` (and CPU or latency) alerts. The result: an L0 `Recorded` Incident in `nexus-dev`, an L1
  `Escalated` one in `nexus-prod`. The 20-min warm-up follows (plan §3).
- To fill in: the 60-min clean-baseline results, and the S5 first-fire time against +105 s.
