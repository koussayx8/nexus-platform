"""Offline tests for zscore.py (standard library only): the reimplementation of the deployed rules.

The constants are pinned to platform/observability/alerts/nexus-detection.yaml, so a change to the
rules breaks these tests until the reimplementation follows."""

import math
import pathlib
import re
import unittest

from .. import zscore

RULES = (
    pathlib.Path(__file__).resolve().parents[3]
    / "platform/observability/alerts/nexus-detection.yaml"
).read_text()
NAMES = {
    "cpu": "cpu",
    "latencyP95": "latency_p95",
    "inflight": "inflight",
    "errorRatio": "error_ratio",
    "requests": "requests",
}


class RulesPinTest(unittest.TestCase):
    def test_epsilons_are_the_rules(self):
        for sig, rule in NAMES.items():
            m = re.search(
                rf"clamp_min\(namespace:nexus_sample_api_{rule}:baseline_stddev15m, ([0-9.]+)\)",
                RULES,
            )
            self.assertEqual(float(m.group(1)), zscore.EPSILON[sig], sig)

    def test_baseline_window_offset_and_minimum_samples(self):
        self.assertEqual(
            set(re.findall(r"\[15m\] offset 3m", RULES)), {"[15m] offset 3m"}
        )
        self.assertEqual(
            (zscore.BASELINE_WINDOW_S, zscore.BASELINE_OFFSET_S), (900, 180)
        )
        self.assertEqual(
            set(re.findall(r"count_over_time\([^\n]*\) >= (\d+)", RULES)),
            {str(zscore.MIN_SAMPLES)},
        )
        self.assertIn("avg_over_time", RULES)
        self.assertIn("stddev_over_time", RULES)

    def test_threshold_for_interval_and_error_floor(self):
        self.assertEqual(
            set(re.findall(r"zscore15m\)? > (\d+)", RULES)),
            {str(int(zscore.THRESHOLD))},
        )
        self.assertEqual(RULES.count("for: 1m"), len(zscore.ALERTS))
        self.assertEqual(zscore.FOR_S, 60)
        self.assertIn("interval: 30s", RULES)
        self.assertEqual(zscore.STEP_S, 30)
        self.assertIn(f"rate2m > {zscore.ERROR_RATIO_FLOOR}", RULES)

    def test_alert_names_and_their_signals(self):
        for alert in zscore.ALERTS:
            self.assertIn(f"alert: {alert}", RULES)
        lat = RULES[
            RULES.index("alert: NexusLatencyAnomaly") : RULES.index(
                "alert: NexusCpuAnomaly"
            )
        ]
        self.assertIn("latency_p95:zscore15m > 3", lat)
        self.assertIn(
            "inflight:zscore15m > 3", lat
        )  # latency OR inflight: a deadlock records no duration (ADR-022)


def steady(n=80, value=10.0, start=0.0):
    return [(start + i * 30.0, value) for i in range(n)]


def sigs(**over):
    base = {
        "cpu": 0.3,
        "latencyP95": 0.05,
        "inflight": 2.0,
        "errorRatio": 0.002,
        "requests": 80.0,
    }
    return {k: zscore.Series(over.get(k, steady(value=v))) for k, v in base.items()}


class BaselineTest(unittest.TestCase):
    def test_mean_and_population_stddev_over_the_lagged_window(self):
        pts = [(i * 30.0, float(i % 2)) for i in range(80)]  # 0, 1, 0, 1 ...
        mean, std = zscore.baseline(
            zscore.Series(pts), 2400.0
        )  # window (2400-180-900, 2400-180] = (1320, 2220]
        self.assertAlmostEqual(mean, 0.5)
        self.assertAlmostEqual(std, 0.5)  # population: sqrt(0.25), not the sample 0.508

    def test_the_window_is_left_open_and_lagged(self):
        s = zscore.Series(steady(80, 5.0))
        vals = s.window(2400 - 180 - 900, 2400 - 180)
        self.assertEqual(len(vals), 30)  # 1350 .. 2220 inclusive at the 30 s step
        self.assertEqual(s.window(1320.0, 2220.0)[0], 5.0)
        self.assertEqual(
            len(s.window(1350.0, 2220.0)), 29
        )  # t = 1350 itself is excluded when lo = 1350

    def test_fewer_than_27_samples_gives_no_baseline(self):
        pts = steady(80)
        self.assertIsNotNone(zscore.baseline(zscore.Series(pts), 2400.0))
        thin = [
            p for i, p in enumerate(pts) if not 45 <= i < 49
        ]  # 4 of the window's 30 samples missing
        self.assertEqual(len(zscore.Series(thin).window(1320, 2220)), 26)
        self.assertIsNone(zscore.baseline(zscore.Series(thin), 2400.0))
        self.assertIsNone(
            zscore.baseline(zscore.Series(pts[:20]), 600.0)
        )  # a fresh series


class ZscoreTest(unittest.TestCase):
    def series_with(self, x):
        pts = steady(80, 10.0)
        pts[-1] = (pts[-1][0], x)
        return zscore.Series(pts), pts[-1][0]

    def test_epsilon_floors_a_flat_baseline(self):
        s, t = self.series_with(10.05)  # baseline stddev 0 -> clamp_min(0, 0.02)
        self.assertAlmostEqual(zscore.zscore("cpu", s, t), 2.5, places=6)
        s, t = self.series_with(10.07)
        self.assertAlmostEqual(zscore.zscore("cpu", s, t), 3.5, places=6)

    def test_a_real_stddev_above_epsilon_is_used(self):
        pts = [
            (i * 30.0, 10.0 + (1.0 if i % 2 else -1.0)) for i in range(80)
        ]  # stddev 1 > epsilon 0.02
        s = zscore.Series(pts[:-1] + [(pts[-1][0], 14.0)])
        self.assertAlmostEqual(
            zscore.zscore("cpu", s, pts[-1][0]), (14.0 - 10.0) / 1.0, places=6
        )

    def test_no_sample_or_no_baseline_gives_none(self):
        self.assertIsNone(zscore.zscore("cpu", zscore.Series(steady(80)), 99999.0))
        self.assertIsNone(zscore.zscore("cpu", zscore.Series(steady(10)), 270.0))

    def test_exactly_three_is_not_above_three(self):
        s, t = self.series_with(
            13.0
        )  # inflight: epsilon 1, flat baseline 10 -> Z = 3.0 exactly
        self.assertEqual(zscore.zscore("inflight", s, t), 3.0)
        self.assertFalse(
            zscore.conditions({**sigs(), "inflight": s}, t)["NexusLatencyAnomaly"]
        )
        s, t = self.series_with(14.0)
        self.assertTrue(
            zscore.conditions({**sigs(), "inflight": s}, t)["NexusLatencyAnomaly"]
        )


class AlertTest(unittest.TestCase):
    def fire(self, signals, upto=2400.0):
        times = [i * 30.0 for i in range(80) if i * 30.0 <= upto]
        return zscore.firing(signals, times), times

    def spike(self, name, base, bump, at, n):
        pts = steady(80, base)
        for i in range(at, at + n):
            pts[i] = (pts[i][0], base + bump)
        return zscore.Series(pts)

    def test_for_one_minute_needs_three_consecutive_evaluations(self):
        # cpu +0.5 for 3 points starting at i = 60 (t = 1800): fires at the third, i = 62 (t = 1860 + 30)
        sg = sigs(cpu=self.spike("cpu", 0.3, 0.5, 60, 3).points)
        per, _ = self.fire(sg)
        on = [t for t, f in per["NexusCpuAnomaly"].items() if f]
        self.assertEqual(
            on, [1860.0]
        )  # exactly 60 s after the first true evaluation (1800)

    def test_a_two_point_blip_never_fires(self):
        sg = sigs(cpu=self.spike("cpu", 0.3, 0.5, 60, 2).points)
        per, _ = self.fire(sg)
        self.assertFalse(any(per["NexusCpuAnomaly"].values()))

    def test_a_gap_resets_the_wait(self):
        pts = steady(80, 0.3)
        for i in (60, 61, 63, 64):  # true, true, false, true, true
            pts[i] = (pts[i][0], 0.8)
        per, _ = self.fire(sigs(cpu=pts))
        self.assertFalse(any(per["NexusCpuAnomaly"].values()))

    def test_inflight_alone_raises_the_latency_alert(self):
        per, _ = self.fire(
            sigs(inflight=self.spike("inflight", 2.0, 30.0, 60, 6).points)
        )
        self.assertTrue(any(per["NexusLatencyAnomaly"].values()))
        self.assertFalse(any(per["NexusCpuAnomaly"].values()))

    def test_the_error_alert_also_needs_a_ratio_above_five_percent(self):
        small = sigs(
            errorRatio=self.spike("errorRatio", 0.002, 0.04, 60, 6).points
        )  # Z high, ratio 0.042 <= 0.05
        per, _ = self.fire(small)
        self.assertFalse(any(per["NexusErrorRateAnomaly"].values()))
        big = sigs(errorRatio=self.spike("errorRatio", 0.002, 0.2, 60, 6).points)
        per, _ = self.fire(big)
        self.assertTrue(any(per["NexusErrorRateAnomaly"].values()))

    def test_traffic_alert_and_any_firing(self):
        sg = sigs(requests=self.spike("requests", 80.0, 40.0, 60, 6).points)
        per, times = self.fire(sg)
        self.assertTrue(any(per["NexusTrafficAnomaly"].values()))
        anyf = zscore.any_firing(sg, times)
        self.assertEqual(
            {t for t, f in anyf.items() if f},
            {t for t, f in per["NexusTrafficAnomaly"].items() if f},
        )

    def test_a_slow_drift_is_followed_by_the_baseline(self):
        pts = [
            (i * 30.0, 0.05 + 0.025 * max(0, i - 40) / 40) for i in range(80)
        ]  # latency creeping up 0.025 over 20 min
        per, _ = self.fire(sigs(latencyP95=pts))
        self.assertFalse(
            any(per["NexusLatencyAnomaly"].values())
        )  # Z stays under 3: the baseline follows (epsilon 0.01)
        self.assertTrue(all(math.isfinite(v) for _, v in pts))


if __name__ == "__main__":
    unittest.main()
