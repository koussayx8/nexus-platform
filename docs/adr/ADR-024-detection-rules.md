# ADR-024: Detection Rules — Z-scores, the Four Anomaly Alerts and In-flight Requests

## Status: Proposed (M1b-7, branch `feat/m1b-7-detection`; number tentative, assigned in merge order)

## Context
Spec §3 makes detection declarative and shared by NEXUS and the static baseline: Z-score recording
rules for CPU, p95 latency, error ratio and request rate per namespace, and four anomaly alerts.
§25 freezes the window (15 min), the threshold (3), the duration (1 min) and the error-ratio floor
(5 %). It leaves open the rate windows, the evaluation interval, epsilon, the `nexus_target`
format and the handling of missing data. The interim `SampleAPIHighErrorRate` stood in until now.

T-hang (M1b-5, offline, 2026-09-28; evidence `~/nexus-evidence/m1b-5/t-hang/`) showed that a real
deadlock is invisible to the §3 latency signal: prometheus-fastapi-instrumentator 8.1.0, the
version in the deployed image `sha256:8ea896c2…` (CI run 36321366504), records a request only after
the handler returns, and uvicorn never cancels a hung request. It also showed that the
instrumentator's `/metrics` is a plain `def` on the same 40-thread pool, so past 40 hung sync
requests the pod cannot be scraped at all. With an async `/metrics` and the in-flight gauge on,
scrapes stayed at 2–3 ms with 100 hung requests, and the gauge counted every one of them.

## Decision

### Signals (per namespace, `nexus-dev` and `nexus-prod`; business handlers only)
| Signal | Source | Epsilon |
|---|---|---|
| CPU, cores | `rate(container_cpu_usage_seconds_total{job="kubelet",container="sample-api"}[2m])` | 0.02 |
| p95 latency, s | `histogram_quantile(0.95, … http_request_duration_seconds_bucket …[2m])` | 0.01 |
| In-flight requests | `http_requests_inprogress` (gauge, M1b-5) | 1 |
| Error ratio | 5xx ÷ all, `[2m]` | 0.01 |
| Request rate, 1/s | `rate(http_requests_total[2m])` | 1 |

- `/health`, `/ready` and `/metrics` are excluded, as in the interim rule. That includes the
  scrape's own in-flight request.
- Z = (value − 15 min mean) ÷ max(15 min stddev, epsilon). The mean and stddev series also feed
  the §20 pre-check.
- The group runs every **30 s**, set explicitly because the chart leaves `evaluationInterval` to
  the operator default. Rate windows are **2 min**, which holds at least 4 samples at the 30 s
  cAdvisor scrape; sample-api is scraped every 15 s.
- Epsilon sets the smallest change that can alert from a flat baseline (3 × epsilon). The values
  are **provisional** until the M1b-9 clean baseline.

### Alerts
- `NexusLatencyAnomaly`: p95 Z > 3 **or** in-flight Z > 3 (owner, U1 decision, 2026-09-28).
  Little's law, L = λW: at a constant arrival rate, in-flight is the latency signal for requests
  that never complete. This keeps the four alert names and reasoner rule 4 unchanged.
- `NexusCpuAnomaly`: CPU Z > 3. `NexusErrorRateAnomaly`: Z > 3 **and** error ratio > 5 %.
  `NexusTrafficAnomaly`: request-rate Z > 3 (one-sided).
- `for: 1m`, `severity: warning`, `nexus_target: "<namespace>/sample-api"`.
- `max by (namespace)` gives one series per namespace, so an alert never carries two series with
  the same label set.
- Annotations are static, so the unit tests pin them; the Z value is the alert's value.

### Missing data
- p95 and the error ratio drop NaN samples (`>= 0`). A window with no completed request would
  otherwise record NaN and poison the 15 min mean and stddev for the next 15 min.
- The instrumentator creates a `status="5xx"` series only after the first 5xx. The error ratio
  therefore records 0 while there is traffic and no 5xx series (`or … * 0`). Otherwise the 15 min
  mean would contain only the fault, and S5 would never alert.
- A signal with no data records nothing, and no alert fires. The §20 pre-check treats an absent
  Z-score as a failure.

### Verification
- `scripts/tests/promtool-rules.sh` runs in `repo-checks`: promtool 3.12.0, the Prometheus that
  chart 86.2.2 runs, SHA-256 pinned.
- `platform/observability/tests/nexus-detection.test.yaml` covers nine scenarios: steady baseline;
  latency step; CPU step; errors 0 → 20 % (S5 shape); errors 0 → 4 % (floor); traffic ×1.5 (S4
  shape); a sub-threshold CPU step (epsilon); missing series; a real deadlock (S1, T-hang).
- Four negative controls each fail their test: in-flight branch removed, the `or … * 0` baseline
  removed, the NaN filter removed, CPU epsilon 0.001.

## Measured behaviour (promtool; baseline 20 req/s, fault at 20m15s)
| Scenario | Alert | First firing | Firing evaluations (30 s) |
|---|---|---|---|
| Latency step (<0.1 s → 0.1–0.5 s) | `NexusLatencyAnomaly` | 21m30s (+75 s) | 1 |
| CPU 0.1 → 0.4 cores | `NexusCpuAnomaly` | 21m30s (+75 s) | 2 |
| Errors 0 → 20 % | `NexusErrorRateAnomaly` | 21m30s (+75 s) | 2 |
| Traffic ×1.5 | `NexusTrafficAnomaly` | 22m00s (+105 s) | 1 |
| Deadlock, in-flight +20/s | `NexusLatencyAnomaly` | 21m30s (+75 s) | 5 |
| Errors 0 → 4 %; CPU +0.04 cores; no data | none | — | 0 |

## Consequences
- **The alerts mark onsets, not states.** The spec's window includes the current sample, so an
  anomaly enters its own baseline.
  - A step keeps Z above 3 only while the new values are under about 10 % of the window. From a
    flat baseline a linear ramp gives Z = 5.39, 4.80, 4.29, 3.89, 3.58, 3.32, 3.11, 2.94 at
    successive evaluations, whatever its slope.
  - So an alert fires for 30 s to 2.5 min and then resolves while the fault continues.
  - The Alert Poller (10 s) creates the Incident at the first firing, which is enough for NEXUS.
    The risk is real noise: with a non-zero baseline stddev, Z may not stay above 3 for the
    `for: 1m` duration. M1b-9 measures this under the Locust baseline.
  - If it fails there, a lagged baseline window (`offset`) is the change to consider. It would
    need a spec v1.1 note, so it is not made here.
- **Latency resolution.** The instrumentator's default buckets for
  `http_request_duration_seconds` are 0.1, 0.5 and 1 s. p95 cannot move until at least 5 % of
  requests take more than 100 ms. Finer buckets are an app change for M1b-5 (owner decision).
- **Until M1b-5 ships** the in-flight gauge and an async `/metrics`, `NexusLatencyAnomaly` rests on
  p95 alone, and a deadlock stays undetected.
- **S5 needs `/items` in the baseline mix** (M1b-9 gate rule), with a share large enough to push
  the error ratio well past 5 %.
- **Spec v1.1 note:** the §3 latency signal includes in-flight requests (Little's law).
- **Out of scope, proposed for Later:** the §3 kube-state-metrics alerts `NexusRolloutStuck` and
  `NexusCrashLooping`, and Alertmanager `group_by: [nexus_target]`.
