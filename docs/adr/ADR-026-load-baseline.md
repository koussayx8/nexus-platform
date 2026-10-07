# ADR-026: The Locust Load Baseline and the R1 Calibration

## Status: Proposed (M1b-9, PR L1 `feat/m1b-9-locust`; plan `~/nexus-m1b9-plan.md`, approved with changes by Koussay, 2026-10-06)

Draft. L1 records the design; the values (C, B) and the measured results are added by L2 and the
M1b-9 closing PR.

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

### R2
Deferred to M2/S2 (owner, 2026-10-06). The S5 exit does not need it.

## Consequences
- Locust adds up to 1.5 CPU of limits on the node (3.9 → 5.4 of 12).
- The baseline start is a real traffic step and can raise Nexus alerts and Incidents. Its warm-up
  rules are in the M1b-9 plan (§3).
- Until M3's NetworkPolicies, any pod can reach the master's API. The runner reaches it only
  through a port-forward (§18).
- To fill in: C, B, the R1 table, the 60-min clean-baseline results, the S5 first-fire time
  against +105 s.
