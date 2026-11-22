"""The quiet window (owner, 2026-11-22; ADR-041 "Live gate, 2026-11-22"): a pre-check poll passes
only when quiet_s has passed since the window started and Prometheus shows no NEXUS alert pending
or firing for the namespace in the last quiet_s. With the real values (quiet_s 360, poll_s 30,
cap_s 1200). Offline: the fake cluster of test_batch answers every read; nothing here reaches a
cluster or the network."""

import json
import pathlib
import unittest
import urllib.parse

from .. import batch, precheck
from .test_batch import (
    PRE,
    FakeClock,
    FakeCluster,
    FakeTime,
    audit_fresh,
    firing,
    runner_for,
)

REAL = {**PRE, "cap_s": 1200, "poll_s": 30, "quiet_s": 360}
EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "batch-config.example.json"


class TimedAlerts(FakeCluster):
    """ALERTS answers as max_over_time does: an alert counts in a query over [Ns] when it was
    active at any time in the last N seconds. `episodes` are (alert, start_s, end_s) on the fake
    monotonic clock; end_s None = still active."""

    def __init__(self, ft, episodes, **kw):
        super().__init__(ft, **kw)
        self.ft, self.episodes = ft, episodes

    def promql(self, q):
        if "ALERTS" not in q:
            return super().promql(q)
        secs = int(q.split("[")[1].split("s]")[0])
        now = self.ft.monotonic()
        return [
            {"metric": a}
            for a, start, end in self.episodes
            if start <= now and (end is None or end > now - secs)
        ]


class QuietWindowTest(unittest.TestCase):
    def setUp(self):
        self.ft = FakeTime(FakeClock())
        self.cfg = precheck.normalise(REAL)

    def run_pc(self, c):
        r = runner_for(c, self.ft)
        live = batch.LiveRun(
            r, REAL, monotonic=self.ft.monotonic, wall=self.ft.wall, sleep=self.ft.sleep
        )
        return precheck.run_prechecks(
            precheck.Prom(r), self.cfg, live.incidents, set(), batch.model.TERMINAL_PHASES, 3,
            self.ft.monotonic, self.ft.wall, self.ft.sleep, audit_fresh(self.ft),
        )  # fmt: skip

    def quiet(self, c, waited):
        return precheck.quiet_failures(
            precheck.Prom(runner_for(c, self.ft)), self.cfg, waited
        )

    # ---------------------------------------------------------------- the window's length

    def test_a_window_shorter_than_quiet_s_fails_with_its_reason(self):
        self.assertEqual(
            self.quiet(FakeCluster(self.ft), 359.9), ["quiet:window=359s<360s"]
        )

    def test_a_clean_window_of_quiet_s_passes(self):
        self.assertEqual(self.quiet(FakeCluster(self.ft), 360), [])

    def test_a_clean_run_passes_at_the_first_poll_past_quiet_s_not_before(self):
        pc = self.run_pc(FakeCluster(self.ft))
        # polls at 0, 30, ..., 360 s: the 13th is the first with 360 s behind it
        self.assertEqual(
            (pc.ok, pc.polls, pc.waited_s, pc.failures), (True, 13, 360, [])
        )

    # ---------------------------------------------------------------- alerts in the window

    def test_an_alert_pending_inside_the_window_fails_the_poll(self):
        c = TimedAlerts(self.ft, [(firing("NexusLatencyAnomaly", "pending"), 0, None)])
        self.assertEqual(
            self.quiet(c, 400), ["quiet:alert=NexusLatencyAnomaly/pending"]
        )

    def test_a_firing_alert_reads_its_state(self):
        c = TimedAlerts(self.ft, [(firing("NexusCpuAnomaly"), 0, None)])
        self.assertEqual(self.quiet(c, 400), ["quiet:alert=NexusCpuAnomaly/firing"])

    def test_the_warm_up_holds_the_injection_until_quiet_s_after_it_ends(self):
        # 2026-11-22: pending from +0m41 after Ready, firing at +1m41; here it clears at 150 s
        c = TimedAlerts(self.ft, [
            (firing("NexusLatencyAnomaly", "pending"), 41, 101),
            (firing("NexusLatencyAnomaly"), 101, 150),
        ])  # fmt: skip
        pc = self.run_pc(c)
        # the first poll with nothing in the last 360 s is at 510 s (150 + 360, on the 30 s grid)
        self.assertEqual((pc.ok, pc.waited_s, pc.failures), (True, 510, []))

    def test_an_alert_that_never_clears_runs_to_the_cap_as_a_discard_not_a_halt(self):
        c = TimedAlerts(self.ft, [(firing("NexusLatencyAnomaly", "pending"), 0, None)])
        pc = self.run_pc(c)
        self.assertFalse(pc.ok)
        self.assertIsNone(pc.stop_reason)
        self.assertEqual(pc.failures, ["quiet:alert=NexusLatencyAnomaly/pending"])
        self.assertGreaterEqual(pc.waited_s, REAL["cap_s"])

    def test_an_alert_for_another_namespace_does_not_hold_the_window(self):
        c = TimedAlerts(
            self.ft, [(firing("NexusCpuAnomaly", ns="nexus-prod"), 0, None)]
        )
        self.assertEqual(self.quiet(c, 400), [])
        self.assertEqual(self.run_pc(c).ok, True)

    def test_every_alert_is_named_once_sorted(self):
        c = TimedAlerts(self.ft, [
            (firing("NexusLatencyAnomaly", "pending"), 0, None),
            (firing("NexusLatencyAnomaly"), 0, None),
            (firing("NexusCpuAnomaly"), 0, None),
        ])  # fmt: skip
        self.assertEqual(self.quiet(c, 400), [
            "quiet:alert=NexusCpuAnomaly/firing",
            "quiet:alert=NexusLatencyAnomaly/firing",
            "quiet:alert=NexusLatencyAnomaly/pending",
        ])  # fmt: skip

    def test_no_answer_from_prometheus_fails_closed(self):
        c = FakeCluster(self.ft)
        c.prom = {"ALERTS": None}
        self.assertEqual(self.quiet(c, 400), ["quiet:alerts:no_answer"])
        self.assertEqual(
            self.quiet(c, 10), ["quiet:window=10s<360s", "quiet:alerts:no_answer"]
        )

    def test_the_query_reads_every_nexus_alert_over_the_last_quiet_s(self):
        c = FakeCluster(self.ft)
        self.quiet(c, 400)
        (q,) = [
            urllib.parse.unquote(a[-1])
            for a in c.calls
            if "--raw" in a and "ALERTS" in a[-1]
        ]
        self.assertIn('max_over_time(ALERTS{alertname=~"Nexus.*"}[360s])', q)
        self.assertIn("alertstate", q)  # pending and firing both

    # ---------------------------------------------------------------- configuration

    def test_the_default_is_the_owner_value(self):
        cfg = precheck.normalise({k: v for k, v in REAL.items() if k != "quiet_s"})
        self.assertEqual(cfg["quiet_s"], 360)

    def test_quiet_s_must_be_positive_and_below_the_cap(self):
        for bad in (0, -1, True, "360", float("nan")):
            with self.assertRaises(precheck.ConfigError):
                precheck.normalise({**REAL, "quiet_s": bad})
        with self.assertRaises(precheck.ConfigError) as e:
            precheck.normalise({**REAL, "quiet_s": 1200})
        self.assertIn("quiet_s must be below precheck.cap_s", str(e.exception))

    def test_the_example_config_carries_the_owner_value(self):
        doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(doc["precheck"]["quiet_s"], 360)
        precheck.normalise(doc["precheck"])


if __name__ == "__main__":
    unittest.main()
