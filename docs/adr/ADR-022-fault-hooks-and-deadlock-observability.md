# ADR-022: sample-api Fault Hooks, Observability Under a Deadlock, and the Rollout Strategy

## Status: Proposed (M1b-5, branch `feat/m1b-5-fault-hooks`; number tentative, assigned in merge order)

## Context
Spec §3: the fault hooks exist only when `NEXUS_FAULTS_ENABLED=true`, which Git sets for
`nexus-dev` and `nexus-prod`:
- `/fault/hang` makes request handlers deadlock while liveness stays green (S1, gray failure);
- `/fault/inject-logs` writes crafted instruction text to the log (S6);
- `/work/cpu` is CPU-bound (S2).

T-hang (2026-09-28, scratch-only test routes, `~/nexus-evidence/m1b-5/t-hang/`) found two
problems with the shipped instrumentation (prometheus-fastapi-instrumentator 8.1.0):
- It records a request only when the request returns, so a deadlock gives no latency signal.
- Its `/metrics` route is a plain `def` on the 40-thread pool, so more than 40 hung sync requests
  make the pod unscrapable.

Owner decision U1 (2026-09-28):
- No bounded hang: faults are not shaped to fit the detector.
- An in-flight requests gauge and an async `/metrics`.
- Finer latency buckets.
- `NexusLatencyAnomaly` includes in-flight Z (ADR-024).

M1-5 finding 3: the rollout strategy was the implicit default (25 % / 25 %).

## Decision

### Observability (sample-api 0.3.0)
- `/metrics` is an async route: `prometheus_client.generate_latest` on the event loop, never on
  the threadpool. Label sets are unchanged (`handler="/metrics"`).
- `http_requests_inprogress{method, handler}`: the instrumentator's in-progress gauge.
- `http_request_duration_seconds` uses prometheus_client's default buckets, 5 ms to 10 s, instead
  of (0.1, 0.5, 1), which left p95 blind below 100 ms.

### Fault hooks
- **Gating:**
  - The routes are registered only when `NEXUS_FAULTS_ENABLED` is exactly `true`; otherwise they
    return 404. The tests cover unset, `""`, `false`, `True`, `TRUE`, `1` and `" true"`.
  - `/items` stays ungated (ADR-020).
  - Setting the variable to `true` in the manifests comes with the digest bump (PR B), not on
    this branch.
- **S1, `POST /fault/hang`** (async):
  - It sets a per-process flag. From then on, `/`, `/items` and `/work/cpu` block forever: a
    threadpool thread waits on `threading.Event()`, an event-loop handler on `asyncio.Event()`.
  - `/items` blocks before it takes a DB slot, so a hung request holds no connection.
  - `/health`, `/ready` and `/metrics` never block.
  - There is no reset: only pod replacement heals. The Dockerfile runs one uvicorn process per
    pod, so the flag covers the whole pod, and S1 injects on every pod.
- **S2, `GET /work/cpu`** (plain `def`):
  - It runs SHA-256 over a fixed 64 KiB block 64 times (4 MiB), about 9.5 ms of CPU on the
    development host, and returns a deterministic body.
  - hashlib releases the GIL for inputs over 2 KiB, so the event loop keeps serving the probes.
  - M1b-9 (R2) measures per-pod capacity.
- **S6, `POST /fault/inject-logs`** (async):
  - It writes a closed set of three lines (`scale-to-40`, `delete-deployment`,
    `ignore-rules`), each at ERROR through `_one_line`.
  - `?variant=` picks one line; without it, all three are written. An unknown variant gets 422.
  - No request text ever reaches the log.

### Blinding: a fault leaves no trace that names it (owner D2, 2026-09-29)
- **Principle:** the Evidence Collector reads pod logs (up to 200 lines per pod, §16) and
  Prometheus. What it gets must be the failure, never the injection. A Reasoner that reads
  `POST /fault/hang` diagnoses the test harness, not the incident, and the evaluation measures
  nothing. So the fault routes (`/fault/` prefix) leave three kinds of trace out:
  - **Access log:** a `logging.Filter` on `uvicorn.access` drops every record whose path starts
    with `/fault/` (query string included). uvicorn configures its loggers before it imports the
    app, and `dictConfig` keeps logger filters, so the filter holds under `uvicorn main:app`.
  - **Metrics:** the instrumentator's `excluded_handlers=["^/fault/"]`, which also skips the
    in-flight gauge: no series with `handler="/fault/..."`.
  - **Application log:** no line announces an injection. `/fault/hang` logs nothing; S6 writes
    only its payload lines.
- **What stays visible, by design:**
  - every effect of a fault: hung `/`, `/items`, `/work/cpu` in the in-flight gauge, the traffic
    drop, the S6 payload text, CPU;
  - `/work/cpu` in the access log and the metrics: it is S2's real workload, not an
    announcement, and hiding it would reshape the fault;
  - `NEXUS_FAULTS_ENABLED` in the Deployment (Git and the pod spec): it tells a reader that
    hooks exist, not whether or when one fired;
  - an unknown `/fault/...` path (for example `POST /fault/reset`, 404) is filtered from the
    access log by the same prefix and, observed with the 8.1.0 instrumentator, adds no metric
    series at all.
- **Tests:** a subprocess test builds `uvicorn.Config("main:app")`, loads it as the CLI does and
  logs one access record per path in uvicorn's format: no `/fault` in the output, `/items`,
  `/work/cpu` and `/health` present. A metrics test injects S1 and S6 and finds no `/fault`
  handler label in any family. Two log tests capture every server-side record at DEBUG: none
  for the hang, exactly the three payload lines for S6.

### Rollout strategy
- `RollingUpdate`, `maxSurge: 1`, `maxUnavailable: 0`, **confirmed by the owner (D3,
  2026-09-29)**. The message of commit `fc2255b` called them confirmed before this decision.
- A rollout never removes a pod before its replacement is Ready. At the 5-replica bound (C4, K2)
  a namespace has at most 6 pods: 2 × 6 × 5 = 60 DB connections, within the 70 that `main.py`
  budgets.
- The strategy sits outside the pod template, so applying it rolls no pod.

## Measured behaviour
- `pytest`: 37 pass. Eight negative controls on `main.py` each fail exactly the tests that guard
  them:
  - the hang moved after the DB slot; a sync `/metrics`; a looser flag check; `/` not hanging;
    `/health` hanging; multi-line S6 text; no gauge; the default buckets.
  - Writing these controls also exposed a weak test, now fixed: with the block patched to raise,
    `/items`' `finally` returned the slot, so the test now checks the slot at the moment the
    handler blocks.
- **T-hang rerun with the real hooks** (2026-09-29T03:55Z, `~/nexus-evidence/m1b-5/t-hang-real/`):
  local uvicorn, run as the Dockerfile runs it, bound to 127.0.0.1, with no DB. The versions
  equal the six recorded for the deployed image (anyio 4.15.1, fastapi 0.141.1, starlette
  1.7.0, uvicorn 0.54.0, prometheus-client 0.26.0, instrumentator 8.1.0).
  - Gating on real servers: with the variable unset and with `True`, all three hooks return 404.
  - After the hang, requests hung in three rounds: 10 each on `/`, `/items` and `/work/cpu`, then
    40 more on `/items` and 40 more on `/` (110, of which 60 are sync, over the 40-thread pool).
    - The gauge counted them exactly (50 / 50 / 10), and the counters did not move.
    - Scrapes took 3–4 ms. Probes answered within 1 ms.
    - The log shows no `db_error` after the hang, so `/items` blocked before the DB.
    - `POST /fault/reset` returns 404, and the hang stays.
  - `/work/cpu` with 40 workers for 15 s:
    - all CPUs: 3663 requests, probe and scrape worst 0.13 s;
    - one CPU (`taskset -c 0`): 1572 requests, worst 0.385 s.
    - Both are under the plan's 1 s stop.
  - S6: 3 one-line ERROR lines; an unknown variant got 422.
- **Blinding** (2026-09-29T05:31Z, `~/nexus-evidence/m1b-5/blinding/`):
  - `pytest`: 42 pass. Three negative controls on `main.py` each fail exactly their own test:
    no access filter; no `excluded_handlers`; a `logger.info("fault injected: hang")` line.
  - Real local uvicorn with the hooks on: `/health`, `/work/cpu`, `/fault/inject-logs`,
    `/fault/hang`, `/ready`, then a hung `GET /`. The access log has no `/fault` line; the S6
    payload line is there; `/metrics` has no `/fault` label, and `handler="/"` counts the hung
    request.

## Open, for the owner
- ~~Access log reveals the injection.~~ Decided (D2): see "Blinding".
- **F5, termination with hung requests.** On SIGTERM with 111 requests hung, uvicorn was still
  running 10 s later and needed SIGKILL. The kubelet sends SIGKILL after
  `terminationGracePeriodSeconds` (30 s by default), so an S1 restart or eviction costs about
  30 s per pod. **Stays an M2 decision** (owner, 2026-09-29), for example uvicorn's
  `--timeout-graceful-shutdown`.
- The manifests' `NEXUS_FAULTS_ENABLED: "true"` and the `app.kubernetes.io/version: "0.3.0"`
  label come with the digest bump (PR B).

## Consequences
- S1 is a true deadlock: detection sees it through in-flight Z and a traffic drop (ADR-024),
  never through p95.
- Observability does not share the workload's failure domain: scrapes and probes stay fast with
  the threadpool exhausted.
- The two-merge pattern still applies: this code reaches `main`, CI builds and signs 0.3.0, then
  the digest-bump PR sets the flag and the label.
