# ADR-024: Detection Rules — Z-scores Against a Lagged Baseline, the Four Anomaly Alerts and In-flight Requests

## Status: Proposed (M1b-7, branch `feat/m1b-7-detection`; number tentative, assigned in merge order)

## Context
Spec §3 makes detection declarative and shared by NEXUS and the static baseline: Z-score recording
rules for CPU, p95 latency, error ratio and request rate per namespace, and four anomaly alerts.
§25 freezes the window (15 min), the threshold (3), the duration (1 min) and the error-ratio floor
(5 %). It leaves open where the window ends, the rate windows, the evaluation interval, epsilon,
the `nexus_target` format and the handling of missing data. The interim `SampleAPIHighErrorRate`
stood in until now.

T-hang (M1b-5, offline, 2026-09-28; evidence `~/nexus-evidence/m1b-5/t-hang/`) showed that a real
deadlock is invisible to the §3 latency signal.
- prometheus-fastapi-instrumentator 8.1.0 records a request only after the handler returns, and
  uvicorn never cancels a hung request. This is the version in the deployed image
  `sha256:8ea896c2…` (CI run 36321366504).
- The instrumentator's `/metrics` is a plain `def` on the same 40-thread pool, so past 40 hung sync
  requests the pod cannot be scraped.
- With an async `/metrics` and the in-flight gauge on, scrapes stayed at 2–3 ms with 100 hung
  requests, and the gauge counted every one of them.

A first version of these rules used a window that includes the current sample, the literal reading
of "value − 15-minute mean". There, a step fault covering a fraction f of the window gives
Z = √((1 − f)/f) whatever its magnitude. Z > 3 only while f < 0.1, about 90 s of a 15 min window.
This is structural, not noise. promtool measured it: steps fired for 1–2 evaluations of 30 s, then
cleared while the fault went on.

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
- The group runs every **30 s**, set explicitly because the chart leaves `evaluationInterval` to
  the operator default.
- Rate windows are **2 min**, which holds at least 4 samples at the 30 s cAdvisor scrape;
  sample-api is scraped every 15 s.
- Epsilon sets the smallest change that can alert from a flat baseline (3 × epsilon). The values
  are **provisional** until the M1b-9 clean baseline.

### Baseline: the 15 min window that ends 3 min ago
- Z = (x(t) − mean_B) ÷ max(stddev_B, ε), where B = (t − 18 min, t − 3 min]:
  `avg_over_time(x[15m] offset 3m)` and `stddev_over_time(x[15m] offset 3m)`.
- **Spec v1.1 clarification:** "the baseline excludes the most recent 3 minutes". The window stays
  15 min, the threshold 3, the duration 1 min.
- For a step h at t₀ from a baseline with stddev σ:
  - Z = h ÷ max(σ, ε) until t₀ + 3 min, because the baseline is still clean;
  - after that the step enters B, and Z falls as √((1 − f)/f) once it dominates the stddev.
  - So Z stays above 3 for about 3 + 1.5 min after the onset, and a sustained step keeps its
    alert firing for at least the 3 min lag.
- **The 3 min, from the detection-time math:**
  - *Lower bound:* the baseline must still be clean when the Incident's evidence is captured, so
    the Z-scores in the bundle (read by reasoner rules 3–5) measure the fault against a clean
    baseline. Evidence capture comes at most 135 s after the onset: the first firing at most
    105 s (measured below), the Alert Poller at most 10 s later, and evidence starting within the
    20 s `Detected` limit (§7). So the lag is at least 2.25 min.
  - *Upper bound:* the §20 pre-check waits for the baseline the next fault will be compared with to
    be clean. That is B itself, so it needs 15 min + lag + the 2 min rate tail after the fault's
    last raw sample. §25 caps the wait at 20 min, so the lag is at most 3 min.
  - 3 min is the only whole minute between the two bounds.
  - 5 min would make the pre-check need 22 min: runs whose fault lasts until the reset (S1 unless
    it is repaired, S3, S5) would be discarded unless the budget changes, which is a spec v1.1
    decision.
- **Startup guard:** the baseline is recorded only while B holds at least 27 of its 30 samples.
  A fresh series, or one after a gap such as a Prometheus restart, starts with `rate()` at 5, 10,
  15 of 20 req/s. A lagged baseline of only those samples raised Z with no fault; promtool showed
  pending alerts at the start of every steady case. While the baseline is absent the Z-score is
  absent: no alert, and a failed pre-check.

### Alerts
- `NexusLatencyAnomaly`: p95 Z > 3 **or** in-flight Z > 3 (owner, U1 decision, 2026-09-28).
  Little's law, L = λW: at a constant arrival rate, in-flight is the latency signal for requests
  that never complete. This keeps the four alert names and reasoner rule 4 unchanged.
  **Spec v1.1 note:** the §3 latency signal includes in-flight requests.
- `NexusCpuAnomaly`: CPU Z > 3. `NexusErrorRateAnomaly`: Z > 3 **and** error ratio > 5 %.
  `NexusTrafficAnomaly`: request-rate Z > 3 (one-sided).
- `for: 1m`, `severity: warning`, `nexus_target: "<namespace>/sample-api"`.
- `max by (namespace)` gives one series per namespace, so an alert never carries two series with
  the same label set.
- Annotations are static, so the unit tests pin them; the Z value is the alert's value.

### Missing data
- p95 and the error ratio drop NaN samples (`>= 0`). A window with no completed request would
  otherwise record NaN and poison the baseline for its whole length.
- The instrumentator creates a `status="5xx"` series only after the first 5xx. The error ratio
  therefore records 0 while there is traffic and no 5xx series (`or … * 0`). Otherwise the
  baseline would contain only the fault, and S5 would never alert.
- A signal with no data records nothing, and no alert fires. The §20 pre-check treats an absent
  Z-score as a failure.

### Verification
- `scripts/tests/promtool-rules.sh` runs in `repo-checks`: promtool 3.12.0, the Prometheus that
  chart 86.2.2 runs, SHA-256 pinned.
- `platform/observability/tests/nexus-detection.test.yaml` covers nine scenarios: steady
  baseline, which also covers a fresh series; latency step; CPU step; errors 0 → 20 % (S5 shape);
  errors 0 → 4 % (floor); traffic ×1.5 (S4 shape); a sub-threshold CPU step (epsilon); missing
  series; a real deadlock (S1, T-hang numbers).
- Each sustained step is also checked to be still firing 3 min after its first firing.
- Six negative controls each fail their tests: in-flight branch removed; the `or … * 0` baseline
  removed; the NaN filter removed; CPU epsilon 0.001; no offset (a self-including window); the
  27-sample guard removed.

## Measured behaviour (promtool; baseline 20 req/s, fault at 20m15s)
| Scenario | Alert | First firing | Firing evaluations (30 s) |
|---|---|---|---|
| Latency step (<0.1 s → 0.1–0.5 s) | `NexusLatencyAnomaly` | 21m30s (+75 s) | 7 |
| CPU 0.1 → 0.4 cores | `NexusCpuAnomaly` | 21m30s (+75 s) | 8 |
| Errors 0 → 20 % | `NexusErrorRateAnomaly` | 21m30s (+75 s) | 8 |
| Traffic ×1.5 | `NexusTrafficAnomaly` | 22m00s (+105 s) | 7 |
| Deadlock, in-flight +20/s | `NexusLatencyAnomaly` | 21m30s (+75 s) | 21 |
| Errors 0 → 4 %; CPU +0.04 cores; no data; steady from a fresh series | none | — | 0 |

With the self-including window the same steps fired for 1–2 evaluations, and the deadlock for 5.

## Consequences
- **An alert that clears is not a recovery (M2 note).** A sustained fault enters the baseline
  3 min after its onset, and its Z-score falls back under 3 about 1.5 min later while the fault
  goes on. The Verifier and every repair check (`restart_v1`, `scale_v1`) must therefore compare
  the raw signal with its pre-fault baseline, recorded when the Incident was detected. They never
  use the alert state or the current Z-score.
- **Pre-check timing (M4):** with the lag, a clean baseline needs 15 + 3 + 2 = 20 min after a
  fault's last raw sample, the whole §25 budget. The margin is the part of the reset that follows
  the fault's end: pods replaced, the role restored, Locust back to baseline. That is UNVERIFIED
  until the M4 runner.
  - If runs hit the budget, the levers are a 1 min rate window for the 15 s-scraped app signals
    (a 1 min tail) or a spec v1.1 budget change.
  - The pre-check must use `baseline_stddev15m`, not a non-lagged window: that is the baseline the
    next fault is compared with.
- **Latency resolution.** The instrumentator's default buckets are 0.1, 0.5 and 1 s, so p95 cannot
  move until at least 5 % of requests take more than 100 ms. Finer buckets come in M1b-5
  (owner, 2026-09-28).
- **Until M1b-5 ships** the in-flight gauge and an async `/metrics`, `NexusLatencyAnomaly` rests on
  p95 alone, and a deadlock stays undetected.
- **S5 needs `/items` in the baseline mix** (M1b-9 gate rule), with a share large enough to push
  the error ratio well past 5 %.
- **Out of scope, proposed for Later:** the §3 kube-state-metrics alerts `NexusRolloutStuck` and
  `NexusCrashLooping`, and Alertmanager `group_by: [nexus_target]`.
