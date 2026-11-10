"""Expected-failure tests for docs/reviews/2026-11-10-m4-validity.md.

Each test states the behaviour the review argues for and fails today; the finding's id is in its
name's comment. unittest reports an unexpected success as a failure, so the decorator is removed in
the PR that fixes the finding. Offline: the fakes of test_batch.py, no cluster.
"""

import json
import pathlib
import re
import tempfile
import unittest

from .. import batch, metrics, runner
from . import test_batch as tb

ROOT = pathlib.Path(__file__).resolve().parents[3]


def application_names() -> set[str]:
    """metadata.name of every Argo CD Application manifest in the repository."""
    names = set()
    for path in sorted((ROOT / "platform/argocd/applications").glob("*.yaml")):
        m = re.search(r"^metadata:\n  name: (\S+)", path.read_text(), re.MULTILINE)
        if m:
            names.add(m[1])
    return names


class RunnerTest(unittest.TestCase):
    # V1
    @unittest.expectedFailure
    def test_the_runner_waits_for_applications_that_exist(self):
        self.assertTrue(application_names())  # the manifests were found
        self.assertLessEqual(set(runner.APPS), application_names())

    # V19
    @unittest.expectedFailure
    def test_the_reset_stops_locust_with_a_get(self):
        # Locust 2.46.7 serves /stop for GET only (locust/web.py: route("/stop"), no methods).
        r = runner.Runner(
            "ctx", exec_=lambda argv, cwd=None: (0, ""), locust="http://l"
        )
        stops = [
            s.argv[0] for s in r.plan_reset(3) if s.argv[1:2] == ("http://l/stop",)
        ]
        self.assertEqual(stops, ["GET"])


class Live(tb.Base):
    """One live run on test_batch's fake cluster."""

    cells = tb.CELLS

    def setUp(self):
        super().setUp()
        self.ft = tb.FakeTime(self.clock)
        if self.cells is not tb.CELLS:
            self._own = tempfile.TemporaryDirectory()
            self.addCleanup(self._own.cleanup)
            self.dir = pathlib.Path(self._own.name)
            self.q = batch.Queue.create(
                self.dir, tb.REF, self.cells, precheck_cfg=tb.PRE
            )

    def run_one(self, cluster) -> dict:
        live = batch.LiveRun(
            tb.runner_for(cluster, self.ft),
            tb.PRE,
            wait_s=50,
            poll_s=5,
            sleep=self.ft.sleep,
            monotonic=self.ft.monotonic,
            wall=self.ft.wall,
            stat=tb.audit_fresh(self.ft),
        )
        self.drive(live, max_runs=1)
        return self.reload().runs[0]


class OtherIncident(tb.FakeCluster):
    """After the injection, an unrelated Incident (nexus-prod, no fault there) ends first."""

    def __call__(self, argv, cwd=None):
        rc, out = super().__call__(argv, cwd)
        if "incidents" in argv and self.injected:
            doc = json.loads(out)
            doc["items"].insert(
                0,
                {
                    "metadata": {"uid": "prod-noise"},
                    "spec": {
                        "target": {"namespace": "nexus-prod", "name": "sample-api"}
                    },
                    "status": {"phase": "Recommended"},
                },
            )
            out = json.dumps(doc)
        return rc, out


class HangStays(tb.FakeCluster):
    """S1 that is not remediated: Locust is up with all its users, the hung service answers nothing."""

    def __call__(self, argv, cwd=None):
        out = super().__call__(argv, cwd)
        if self.injected:
            self.rps = 0.0
        return out


class RunIdentityTest(Live):
    # V2
    @unittest.expectedFailure
    def test_an_incident_of_another_namespace_does_not_end_the_run(self):
        r = self.run_one(OtherIncident(self.ft, phases=("Executing", "Resolved")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Resolved"))

    # V3
    @unittest.expectedFailure
    def test_a_hang_that_was_not_remediated_is_a_valid_run(self):
        r = self.run_one(HangStays(self.ft, phases=("Diagnosing", "Escalated")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Escalated"))

    def test_a_dead_locust_still_voids_that_run(self):
        # What a fix must keep: the check exists to catch a Locust that stopped.
        class LocustDies(HangStays):
            def __call__(self, argv, cwd=None):
                out = super().__call__(argv, cwd)
                if self.injected:
                    self.locust_doc = {"state": "stopped", "user_count": 0}
                return out

        r = self.run_one(LocustDies(self.ft, phases=("Diagnosing", "Escalated")))
        self.assertEqual((r["status"], r["void_reason"]), ("void", "locust_end_check"))

    # V5
    @unittest.expectedFailure
    def test_the_run_record_carries_the_injection_time(self):
        r = self.run_one(tb.FakeCluster(self.ft))
        self.assertEqual(r["status"], "valid")
        self.assertIn("injection_at", r)


R3_CELL = [
    {"cell": "C2/S1/L3", "scenario": "S1", "reasoner": "R3", "level": 3,
     "tier": 1, "valid_target": 1, "max_attempts": 2},
]  # fmt: skip


class ArmTest(Live):
    cells = R3_CELL

    # V4
    @unittest.expectedFailure
    def test_the_cells_arm_is_written_to_the_operator_before_the_run(self):
        c = tb.FakeCluster(self.ft)
        self.run_one(c)
        patches = [" ".join(a) for a in c.calls if "nexus-operator-config" in a]
        self.assertTrue(any("reasonerMode" in p for p in patches))

    # V4
    @unittest.expectedFailure
    def test_a_run_diagnosed_by_another_arm_than_its_cell_is_not_valid(self):
        class RulesAnswered(tb.FakeCluster):
            """The operator ran R1 only (no Reasoner configured): mode rules, no fallback."""

            def __call__(self, argv, cwd=None):
                rc, out = super().__call__(argv, cwd)
                if "incidents" in argv and self.injected:
                    doc = json.loads(out)
                    for i in doc["items"]:
                        i["status"]["reasoner"] = {
                            "mode": "rules",
                            "fallbackUsed": False,
                        }
                    out = json.dumps(doc)
                return rc, out

        r = self.run_one(RulesAnswered(self.ft))
        self.assertNotEqual(r["status"], "valid")


TRUTH = {
    "S5": {
        "fault_class": "dependency_failure",
        "correct_action": {
            "action": "escalate",
            "recommendation": ["dependency_investigation", "investigate"],
        },
    }
}
ROW = {
    "reasoner": "R1",
    "scenario": "S5",
    "action_id": "escalate",
    "escalation_kind": "investigate",
    "replicas_delta": None,
}


class MetricsTest(unittest.TestCase):
    def test_the_action_metric_accepts_either_kind(self):
        got = metrics.action_selection_accuracy([ROW], TRUTH)
        self.assertEqual(got["R1/S5"]["k"], 1)

    # V8
    @unittest.expectedFailure
    def test_the_escalation_metric_accepts_the_same_kinds(self):
        got = metrics.correct_escalation_rate([ROW], TRUTH)
        self.assertEqual(got["R1"]["k"], 1)


if __name__ == "__main__":
    unittest.main()
