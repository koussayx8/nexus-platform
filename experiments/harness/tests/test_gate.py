"""`runner.py gate` (manual-run gate): offline, on test_batch's fake cluster. Read-only, every
value required, a PASS or FAIL line per check, exit 1 on any FAIL."""

import contextlib
import io
import json
import os
import tempfile
import unittest

from .. import gate, runner
from .test_batch import PRE, FakeClock, FakeCluster, FakeTime, runner_for


class GateTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.config = os.path.join(d.name, "batch.json")
        with open(self.config, "w") as f:
            json.dump({"precheck": PRE}, f)
        self.ft = FakeTime(FakeClock())

    def run_gate(self, cluster, **over):
        args = {"--context": "ctx", "--namespace": "nexus-dev", "--locust": "http://l",
                "--locust-users": "160", "--locust-rate": "80", "--window": "20m",
                "--config": self.config}  # fmt: skip
        args.update(over)
        argv = [x for kv in args.items() for x in kv if x is not None]
        out = io.StringIO()
        rc = gate.main(
            argv,
            runner_factory=lambda ctx, locust=None: runner_for(cluster, self.ft),
            out=out,
        )
        return rc, out.getvalue()

    def cluster(self, **kw):
        return FakeCluster(self.ft, **kw)

    def test_all_pass(self):
        c = self.cluster()
        rc, out = self.run_gate(c)
        self.assertEqual(rc, 0, out)
        self.assertEqual(
            out.split(),
            [
                "PASS",
                "locust",
                "PASS",
                "alerts",
                "PASS",
                "baseline",
                "PASS",
                "recorder",
                "PASS",
                "incidents",
            ],
        )
        for argv in c.calls:  # read-only: only `get`
            self.assertIn("get", argv)
            self.assertNotIn("exec", argv)

    def assert_fails(self, c, check, needle, **over):
        rc, out = self.run_gate(c, **over)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"FAIL {check}", out)
        self.assertIn(needle, out)
        self.assertEqual(out.count("FAIL"), 1, out)  # only that check fails

    def test_locust_checks(self):
        c = self.cluster()
        c.locust_doc = {"state": "stopped", "user_count": 160}
        self.assert_fails(c, "locust", "state='stopped'")
        c = self.cluster()
        c.locust_doc = {"state": "running", "user_count": 100}
        self.assert_fails(c, "locust", "users=100")
        c = self.cluster()
        c.rps = 60.0
        self.assert_fails(c, "locust", "nexus-dev:rps=60.0")
        c = self.cluster()
        c.rps = 87.0  # within +-10 %
        self.assertEqual(self.run_gate(c)[0], 0)

    def test_the_given_values_are_the_baseline(self):
        c = self.cluster()
        self.assert_fails(c, "locust", "users=160!~200", **{"--locust-users": "200"})
        self.assert_fails(
            self.cluster(), "locust", "rps=80.0!~120", **{"--locust-rate": "120"}
        )

    def test_only_the_given_namespace_is_judged(self):
        prod_rate = 'requests:rate2m{namespace="nexus-prod"}'
        c = self.cluster()
        c.prom = {prod_rate: None}  # prod's rate unreadable; the dev gate must not care
        self.assertEqual(self.run_gate(c)[0], 0)
        c = self.cluster()
        c.prom = {prod_rate: None}
        self.assert_fails(
            c, "locust", "nexus-prod:no_rate", **{"--namespace": "nexus-prod"}
        )

    def test_alert_in_window(self):
        c = self.cluster()
        c.alerts = [
            {
                "alertname": "NexusErrorRateAnomaly",
                "alertstate": "pending",
                "namespace": "nexus-dev",
            }
        ]
        self.assert_fails(c, "alerts", "NexusErrorRateAnomaly:pending:nexus-dev")
        c = self.cluster()
        c.prom = {"ALERTS": None}  # unknown fails closed
        self.assert_fails(c, "alerts", "alerts:no_answer")

    def test_window_is_used(self):
        c = self.cluster()
        self.run_gate(c, **{"--window": "90s"})
        self.assertTrue(any("%5B90s%5D" in " ".join(a) for a in c.calls))

    def test_baseline(self):
        c = self.cluster()
        c.z = 3.0
        self.assert_fails(c, "baseline", "zscore=3.00")
        c = self.cluster()
        c.sd_factor = 5.0
        self.assert_fails(c, "baseline", "stddev=")

    def test_recorder(self):
        for val in (0.0, float("nan"), None, []):
            c = self.cluster()
            c.recorder_up = val
            self.assert_fails(c, "recorder", "recorder:")

    def test_non_terminal_incident(self):
        c = self.cluster(
            existing=[{"metadata": {"uid": "u1"}, "status": {"phase": "Diagnosing"}}]
        )
        self.assert_fails(c, "incidents", "non_terminal_incident:u1")
        c = self.cluster(
            existing=[{"metadata": {"uid": "u2"}, "status": {"phase": "Resolved"}}]
        )
        self.assertEqual(self.run_gate(c)[0], 0)

    def test_every_value_is_required_and_validated(self):
        base = ["--context", "c", "--namespace", "nexus-dev", "--locust", "http://l",
                "--locust-users", "160", "--locust-rate", "80", "--window", "20m", "--config", self.config]  # fmt: skip
        for i in range(0, len(base), 2):
            argv = base[:i] + base[i + 2 :]
            with (
                self.assertRaises(SystemExit),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                gate.main(argv)
        for bad in (
            ["--window", "soon"],
            ["--window", "0m"],
            ["--namespace", "nexus-data"],
        ):
            argv = base[:]
            argv[argv.index(bad[0]) + 1] = bad[1]
            with (
                self.assertRaises(SystemExit),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                gate.main(argv)

    def test_runner_dispatches_gate_and_exit_code_is_nonzero_on_fail(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            runner.main(["gate"])  # reaches gate.main: the required values are missing
        c = self.cluster()
        c.recorder_up = 0.0
        self.assertEqual(self.run_gate(c)[0], 1)


if __name__ == "__main__":
    unittest.main()
