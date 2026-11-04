"""The recorder pre-check (spec §14, §20): nexus_operator_recorder_up must be exactly 1 before a run
starts, and a missing series fails closed. Offline: the fake cluster of test_batch answers the
Prometheus reads; nothing here reaches a cluster or the network."""

import unittest

from .. import batch, precheck
from .test_batch import (
    PRE,
    FakeClock,
    FakeCluster,
    FakeTime,
    audit_fresh,
    runner_for,
)


def series(*values):
    return [
        {"metric": {"pod": f"op-{i}"}, "value": [0, str(v)]}
        for i, v in enumerate(values)
    ]


class RecorderUpTest(unittest.TestCase):
    def setUp(self):
        self.ft = FakeTime(FakeClock())
        self.cfg = precheck.normalise(PRE)

    def prom(self, **kw):
        c = FakeCluster(self.ft)
        for k, v in kw.items():
            setattr(c, k, v)
        return precheck.Prom(runner_for(c, self.ft)), c

    def run_pre(self, c):
        r = runner_for(c, self.ft)
        live = batch.LiveRun(
            r, PRE, monotonic=self.ft.monotonic, wall=self.ft.wall, sleep=self.ft.sleep
        )
        return precheck.run_prechecks(
            precheck.Prom(r), self.cfg, live.incidents, set(), batch.model.TERMINAL_PHASES, 3,
            self.ft.monotonic, self.ft.wall, self.ft.sleep, audit_fresh(self.ft),
        )  # fmt: skip

    def test_one_passes(self):
        prom, _ = self.prom(recorder_up=1.0)
        self.assertEqual(precheck.recorder_failures(prom), [])

    def test_zero_fails(self):
        prom, _ = self.prom(recorder_up=0.0)
        self.assertEqual(precheck.recorder_failures(prom), ["recorder:up=0"])

    def test_a_missing_series_fails_closed(self):
        prom, _ = self.prom(recorder_up=[])
        self.assertEqual(precheck.recorder_failures(prom), ["recorder:no_series"])

    def test_no_answer_fails_closed(self):
        prom, _ = self.prom(recorder_up=None)
        self.assertEqual(precheck.recorder_failures(prom), ["recorder:no_answer"])

    def test_prometheus_error_status_fails_closed(self):
        c = FakeCluster(self.ft)
        r = runner_for(c, self.ft)
        orig = r.exec
        r.exec = lambda argv: (
            (0, '{"status":"error"}')
            if any(precheck.RECORDER_UP in a for a in argv)
            else orig(argv)
        )
        self.assertEqual(
            precheck.recorder_failures(precheck.Prom(r)), ["recorder:no_answer"]
        )

    def test_any_series_not_one_fails(self):
        prom, _ = self.prom(recorder_up=series(1, 0))
        self.assertEqual(precheck.recorder_failures(prom), ["recorder:up=0"])

    def test_values_near_or_above_one_fail(self):
        for v in (0.5, 0.999, 1.5, 2):
            prom, _ = self.prom(recorder_up=series(v))
            self.assertEqual(len(precheck.recorder_failures(prom)), 1, v)

    def test_nan_and_garbage_fail(self):
        for v in ("NaN", "up", ""):
            prom, _ = self.prom(recorder_up=series(v))
            self.assertEqual(len(precheck.recorder_failures(prom)), 1, v)
        prom, _ = self.prom(recorder_up=[{"metric": {}}])
        self.assertEqual(precheck.recorder_failures(prom), ["recorder:unparsable"])

    def test_two_healthy_series_pass(self):
        prom, _ = self.prom(recorder_up=series(1, 1))
        self.assertEqual(precheck.recorder_failures(prom), [])

    def test_the_run_does_not_start_while_the_recorder_is_down(self):
        c = FakeCluster(self.ft)
        c.recorder_up = 0.0
        pc = self.run_pre(c)
        self.assertFalse(pc.ok)
        self.assertEqual(set(pc.failures), {"recorder:up=0"})  # nothing else failed

    def test_the_run_does_not_start_when_the_series_is_missing(self):
        c = FakeCluster(self.ft)
        c.recorder_up = []
        pc = self.run_pre(c)
        self.assertFalse(pc.ok)
        self.assertEqual(pc.failures, ["recorder:no_series"])

    def test_the_run_starts_when_the_recorder_is_up(self):
        pc = self.run_pre(FakeCluster(self.ft))
        self.assertEqual((pc.ok, pc.failures), (True, []))

    def test_a_recovering_recorder_passes_within_the_cap(self):
        c = FakeCluster(self.ft)
        orig = c.promql
        c.promql = lambda q: (
            (0.0 if self.ft.t < 25 else 1.0) if q == precheck.RECORDER_UP else orig(q)
        )
        pc = self.run_pre(c)
        self.assertTrue(pc.ok)
        self.assertGreater(pc.polls, 1)


if __name__ == "__main__":
    unittest.main()
