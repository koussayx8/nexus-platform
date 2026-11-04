"""The deployed Z-score detector, reimplemented offline from its recording rules. Standard library only.

Source of truth: platform/observability/alerts/nexus-detection.yaml (group `nexus-detection`,
evaluated every 30 s: `interval: 30s`, line 39). Cited per rule below; a test pins each constant to
that file, so a change to the rules fails the comparison until this module follows.

  signals      lines 41-82    namespace:nexus_sample_api_{cpu:rate2m, latency_p95:2m, inflight:sum,
                              error_ratio:rate2m, requests:rate2m} (the five `signals` of an export)
  baseline     lines 84-129   avg_over_time / stddev_over_time of the signal over `[15m] offset 3m`,
                              recorded only when the window holds at least 27 of its 30 samples
  Z-score      lines 131-153  (x - baseline_avg15m) / clamp_min(baseline_stddev15m, epsilon);
                              epsilon: cpu 0.02, latency 0.01, inflight 1, errors 0.01, requests 1
  alerts       lines 155-202  Z > 3 `for: 1m`: NexusLatencyAnomaly (latency or inflight),
                              NexusCpuAnomaly, NexusErrorRateAnomaly (also error ratio > 0.05),
                              NexusTrafficAnomaly (requests)

Prometheus semantics assumed here (UNVERIFIED against the deployed version, which the rules file
does not name): a range selector `[15m] offset 3m` at evaluation time t holds the samples with
timestamps in (t - 18 min, t - 3 min] (left-open, Prometheus 3); `stddev_over_time` is the population
standard deviation; a sample missing at t gives no value, so the condition is false; an alert fires at
the evaluation where its condition has held at every evaluation for `for` (60 s: three evaluations at
the 30 s step), and any gap resets it. Rules in a group run in order, so each reads the values the
rules before it recorded at the same timestamp: here, a lookup by timestamp.
"""

from __future__ import annotations

import math

STEP_S = 30
BASELINE_WINDOW_S = 15 * 60
BASELINE_OFFSET_S = 3 * 60
MIN_SAMPLES = 27
THRESHOLD = 3.0
ERROR_RATIO_FLOOR = 0.05  # NexusErrorRateAnomaly's second condition (line 184)
FOR_S = 60
EPSILON = {
    "cpu": 0.02,
    "latencyP95": 0.01,
    "inflight": 1.0,
    "errorRatio": 0.01,
    "requests": 1.0,
}
SIGNALS = tuple(EPSILON)
ALERTS = {
    "NexusLatencyAnomaly": ("latencyP95", "inflight"),
    "NexusCpuAnomaly": ("cpu",),
    "NexusErrorRateAnomaly": ("errorRatio",),
    "NexusTrafficAnomaly": ("requests",),
}


class Series:
    """One recorded series: (timestamp, value) points, looked up by timestamp."""

    def __init__(self, points):
        self.points = sorted((float(t), float(v)) for t, v in points)
        self.at = dict(self.points)

    def window(self, lo: float, hi: float) -> list[float]:
        """Values with lo < t <= hi."""
        return [v for t, v in self.points if lo < t <= hi]


def baseline(series: Series, t: float) -> tuple[float, float] | None:
    """(baseline_avg15m, baseline_stddev15m) at evaluation time t, or None: fewer than 27 samples."""
    hi = t - BASELINE_OFFSET_S
    vals = series.window(hi - BASELINE_WINDOW_S, hi)
    if len(vals) < MIN_SAMPLES:
        return None
    mean = sum(vals) / len(vals)
    return mean, math.sqrt(
        sum((v - mean) ** 2 for v in vals) / len(vals)
    )  # population stddev


def zscore(name: str, series: Series, t: float) -> float | None:
    x = series.at.get(float(t))
    base = baseline(series, t)
    if x is None or base is None:
        return None
    return (x - base[0]) / max(base[1], EPSILON[name])


def conditions(signals: dict, t: float) -> dict[str, bool]:
    """Each alert's expression at t (before `for`): False when a needed Z-score does not exist."""
    z = {n: zscore(n, signals[n], t) for n in SIGNALS}

    def over(n):
        return z[n] is not None and z[n] > THRESHOLD

    err = signals["errorRatio"].at.get(float(t))
    return {
        "NexusLatencyAnomaly": over("latencyP95") or over("inflight"),
        "NexusCpuAnomaly": over("cpu"),
        "NexusErrorRateAnomaly": over("errorRatio")
        and err is not None
        and err > ERROR_RATIO_FLOOR,
        "NexusTrafficAnomaly": over("requests"),
    }


def firing(
    signals: dict, times: list[float], for_s: int = FOR_S, step_s: int = STEP_S
) -> dict[str, dict[float, bool]]:
    """Per alert, whether it is firing at each evaluation time in `times` (the 30 s grid).
    `signals` maps the five names to Series."""
    cond = {t: conditions(signals, t) for t in times}
    need = (
        for_s // step_s + 1
    )  # evaluations that must all hold: now and the `for` before it
    out: dict[str, dict[float, bool]] = {a: {} for a in ALERTS}
    for a in ALERTS:
        for t in times:
            out[a][t] = all(
                cond.get(t - k * step_s, {}).get(a, False) for k in range(need)
            )
    return out


def any_firing(signals: dict, times: list[float]) -> dict[float, bool]:
    """Whether any of the four alerts fires at each time: an Incident would be created."""
    per = firing(signals, times)
    return {t: any(per[a][t] for a in ALERTS) for t in times}
