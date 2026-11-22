"""The host-disturbance void rule (owner, 2026-11-22): a nexus-dev run is void (`host_disturbance`)
when any NEXUS alert on nexus-prod was firing between injection_at and the end of the measurement;
the cell requeues it. Every nexus-prod NEXUS alert in that window, pending or firing, is recorded
(`prod_alerts`) and decides nothing. Offline: the fake cluster of test_batch answers every read;
nothing here reaches a cluster or the network."""

import math
import unittest
import urllib.parse

from .. import batch
from .test_batch import (
    CELLS,
    PRE,
    REF,
    Base,
    FakeCluster,
    FakeTime,
    audit_fresh,
    firing,
    runner_for,
)


class AfterInjection(FakeCluster):
    """ALERTS answers `before` until the fault is injected and `after` from then on; None as an
    answer means Prometheus does not answer that read."""

    def __init__(self, ft, before=(), after=(), **kw):
        super().__init__(ft, **kw)
        self.before, self.after = list(before), after
        self.reads_after = []  # (wall time, query) of every ALERTS read after the injection

    def promql(self, q):
        if "ALERTS" not in q:
            return super().promql(q)
        if self.injected:
            self.reads_after.append((self.ft.wall(), q))
        alerts = self.after if self.injected else self.before
        return None if alerts is None else [{"metric": m} for m in alerts]


class HostDisturbanceTest(Base):
    def setUp(self):
        super().setUp()
        self.ft = FakeTime(self.clock)

    def run_one(self, cluster):
        live = batch.LiveRun(runner_for(cluster, self.ft), PRE, wait_s=50, poll_s=5, sleep=self.ft.sleep,
                             monotonic=self.ft.monotonic, wall=self.ft.wall, stat=audit_fresh(self.ft))  # fmt: skip
        self.drive(live, max_runs=1)
        return self.reload().runs[0]

    def alert_queries(self, cluster):
        return [
            urllib.parse.unquote(a[-1])
            for a in cluster.calls
            if "--raw" in a and "ALERTS" in a[-1]
        ]

    # ---------------------------------------------------------------- the rule

    def test_a_firing_prod_alert_in_the_measurement_voids_the_run_and_the_cell_requeues_it(
        self,
    ):
        c = AfterInjection(self.ft, after=[firing("NexusCpuAnomaly", ns="nexus-prod")],
                           phases=("Diagnosing", "Resolved"))  # fmt: skip
        r = self.run_one(c)
        self.assertEqual((r["status"], r["void_reason"]), ("void", "host_disturbance"))
        self.assertEqual(
            r["prod_alerts"], [{"alertname": "NexusCpuAnomaly", "state": "firing"}]
        )
        q = self.reload()
        self.assertEqual(q.next_cell()["cell"], CELLS[0]["cell"])  # requeued at once
        self.assertEqual(q.cell_state(CELLS[0]["cell"])["valid"], 0)

    def test_a_pending_only_prod_alert_does_not_void_but_is_recorded(self):
        c = AfterInjection(self.ft, after=[firing("NexusLatencyAnomaly", "pending", ns="nexus-prod")],
                           phases=("Diagnosing", "Resolved"))  # fmt: skip
        r = self.run_one(c)
        self.assertEqual(
            (r["status"], r["terminal"], r["void_reason"]), ("valid", "Resolved", None)
        )
        self.assertEqual(
            r["prod_alerts"], [{"alertname": "NexusLatencyAnomaly", "state": "pending"}]
        )

    def test_pending_then_firing_is_recorded_with_both_states_and_voids(self):
        c = AfterInjection(self.ft, after=[
            firing("NexusCpuAnomaly", "pending", ns="nexus-prod"),
            firing("NexusCpuAnomaly", ns="nexus-prod"),
        ], phases=("Diagnosing", "Resolved"))  # fmt: skip
        r = self.run_one(c)
        self.assertEqual(r["void_reason"], "host_disturbance")
        self.assertEqual(r["prod_alerts"], [
            {"alertname": "NexusCpuAnomaly", "state": "firing"},
            {"alertname": "NexusCpuAnomaly", "state": "pending"},
        ])  # fmt: skip

    def test_no_prod_alert_no_void(self):
        c = AfterInjection(self.ft, after=[], phases=("Diagnosing", "Resolved"))
        r = self.run_one(c)
        self.assertEqual(
            (r["status"], r["terminal"], r["prod_alerts"]), ("valid", "Resolved", [])
        )

    def test_nexus_dev_alerts_in_the_measurement_are_the_fault_not_a_disturbance(self):
        c = AfterInjection(self.ft, after=[firing("NexusLatencyAnomaly"), firing("NexusCpuAnomaly")],
                           phases=("Diagnosing", "Resolved"))  # fmt: skip
        r = self.run_one(c)
        self.assertEqual((r["status"], r["prod_alerts"]), ("valid", []))

    def test_a_prod_alert_before_the_injection_is_not_the_runs(self):
        # firing during the pre-checks only (the quiet window holds nexus-dev alone)
        c = AfterInjection(self.ft, before=[firing("NexusCpuAnomaly", ns="nexus-prod")], after=[],
                           phases=("Diagnosing", "Resolved"))  # fmt: skip
        r = self.run_one(c)
        self.assertEqual((r["status"], r["prod_alerts"]), ("valid", []))

    def test_prometheus_not_answering_is_never_quiet(self):
        c = AfterInjection(self.ft, after=None, phases=("Diagnosing", "Resolved"))
        r = self.run_one(c)
        self.assertEqual(
            (r["status"], r["void_reason"]), ("void", "host_disturbance_unknown")
        )
        self.assertIsNone(r["prod_alerts"])

    def test_the_window_runs_from_injection_at_to_the_end_of_the_measurement(self):
        c = AfterInjection(self.ft, after=[], phases=("Diagnosing", "Resolved"))
        r = self.run_one(c)
        injected = batch.model.parse_time(r["injection_at"]).timestamp()
        ((at, q),) = c.reads_after  # one read after the injection: the host rule's
        secs = int(q.split("[")[1].split("s]")[0])
        self.assertIn('max_over_time(ALERTS{alertname=~"Nexus.*"}', q)
        self.assertEqual(
            secs, max(math.ceil(at - injected), 1)
        )  # injection_at to the read
        # read at the end of the measurement (wait_s 50 at most), before the settle's 60 s
        self.assertLessEqual(at - injected, 50 + 5)

    def test_the_settle_still_holds_the_next_reset_when_the_run_is_void(self):
        c = AfterInjection(self.ft, after=[firing("NexusCpuAnomaly", ns="nexus-prod")],
                           phases=("Diagnosing", "Resolved"))  # fmt: skip
        r = self.run_one(c)
        self.assertEqual(r["void_reason"], "host_disturbance")
        self.assertTrue(r["settle"]["settled"])

    def test_a_run_that_never_injected_records_prod_alerts_null(self):
        q = batch.Queue.create(
            self.dir / "pc", REF, CELLS, precheck_cfg={**PRE, "cap_s": 10}
        )
        c = AfterInjection(
            self.ft, before=[firing("NexusLatencyAnomaly")]
        )  # holds the quiet window
        live = batch.LiveRun(runner_for(c, self.ft), {**PRE, "cap_s": 10}, wait_s=50, poll_s=5,
                             sleep=self.ft.sleep, monotonic=self.ft.monotonic, wall=self.ft.wall,
                             stat=audit_fresh(self.ft))  # fmt: skip
        self.drive(live, queue=q, max_runs=1)
        r = q.runs[0]
        self.assertEqual(
            (r["status"], r["discard_reason"]), ("discarded", "precheck_timeout")
        )
        self.assertIsNone(r["prod_alerts"])


class HostDisturbanceRuleTest(unittest.TestCase):
    def test_only_firing_voids(self):
        f = batch.host_disturbance
        self.assertIsNone(f([]))
        self.assertIsNone(f([{"alertname": "NexusCpuAnomaly", "state": "pending"}]))
        self.assertEqual(
            f([{"alertname": "NexusCpuAnomaly", "state": "firing"}]), "host_disturbance"
        )
        self.assertEqual(f(None), "host_disturbance_unknown")


if __name__ == "__main__":
    unittest.main()
