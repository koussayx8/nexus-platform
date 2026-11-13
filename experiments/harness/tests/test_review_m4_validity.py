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
    def test_the_runner_waits_for_applications_that_exist(self):
        self.assertTrue(application_names())  # the manifests were found
        self.assertLessEqual(set(runner.APPS), application_names())

    # V1, the effect the review names: a name the API server does not know fails the refresh step
    # and keeps wait_synced False until its timeout.
    def test_refresh_and_the_sync_wait_pass_on_a_cluster_that_has_only_the_real_applications(
        self,
    ):
        known, asked = application_names(), []

        def cluster(argv, cwd=None):
            if "application" not in argv:
                return 0, ""
            name = argv[argv.index("application") + 1]
            asked.append(name)
            return (0, "Synced/Healthy") if name in known else (1, "")  # NotFound

        r = runner.Runner("ctx", exec_=cluster, sleep=lambda s: None)
        r.execute(r.plan_refresh())  # raises on a step that fails
        self.assertTrue(r.wait_synced(timeout_s=0))
        self.assertEqual(set(asked), set(runner.APPS))

    # V19
    def test_the_reset_stops_locust_with_a_get(self):
        # Locust 2.46.7 serves /stop for GET only (locust/web.py: route("/stop"), no methods).
        r = runner.Runner(
            "ctx", exec_=lambda argv, cwd=None: (0, ""), locust="http://l"
        )
        stops = [
            s.argv[0] for s in r.plan_reset(3) if s.argv[1:2] == ("http://l/stop",)
        ]
        self.assertEqual(stops, ["GET"])

    # V19, executed: the stop goes out through the GET helper and never as a POST. A stop that is
    # refused (405 is what Locust answers to the wrong method) or gets no answer stops the reset at
    # that step, before the operator's configuration is written; it is never passed over.
    def test_a_refused_stop_stops_the_reset_before_the_operator_configuration(self):
        for code in (200, 405, 0):
            calls, gets = [], []

            def get(url, code=code, gets=gets):
                gets.append(url)
                return code, ""

            r = runner.Runner(
                "ctx",
                exec_=lambda argv, cwd=None, calls=calls: calls.append(argv) or (0, ""),
                sleep=lambda s: None,
                locust="http://l",
                http_post=lambda u, f: self.fail(f"POST {u}"),
                http_get=get,
            )
            if code == 200:
                r.execute(r.plan_reset(3, breaker_epoch=5))
            else:
                with self.assertRaises(RuntimeError):
                    r.execute(r.plan_reset(3, breaker_epoch=5))
            self.assertEqual(gets, ["http://l/stop"])
            self.assertEqual(any("configmap" in a for a in calls), code == 200, code)


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
    def test_the_run_record_carries_the_injection_time(self):
        class Timed(tb.FakeCluster):
            """Remembers the clock when the first hang request went out."""

            hung_at = None

            def __call__(self, argv, cwd=None):
                if self.hung_at is None and any("/fault/" in a for a in argv):
                    self.hung_at = self.ft.wall()
                return super().__call__(argv, cwd)

        c = Timed(self.ft)
        r = self.run_one(c)
        self.assertEqual(r["status"], "valid")
        self.assertIn("injection_at", r)
        self.assertEqual(r["injection_at"], batch._utc(c.hung_at))

    def test_a_run_that_never_injected_has_no_injection_time(self):
        c = tb.FakeCluster(self.ft)
        c.z = 5  # the baseline never clears: the pre-check times out
        r = self.run_one(c)
        self.assertEqual(r["discard_reason"], "precheck_timeout")
        self.assertIsNone(r["injection_at"])


S3_CELL = [
    {"cell": "C1/S3/L3", "scenario": "S3", "reasoner": "R1", "level": 3, "s3_patch": "bad.diff",
     "tier": 1, "valid_target": 1, "max_attempts": 2},
]  # fmt: skip
REVISION = "c0ffee" * 6 + "c0ff"
OLD_REVISION = "0" * 40


class ArgoApplies(tb.FakeCluster):
    """S3 on the fake cluster. The bad commit is pushed; `applies_after` reads of the Application
    later Argo CD has applied it (never, when None), and only then is the fault in."""

    def __init__(self, ft, applies_after=2, **kw):
        super().__init__(ft, **kw)
        self.applies_after, self.app_reads = applies_after, 0
        self.committed, self.pushed_at, self.deployed_at = False, None, None

    def __call__(self, argv, cwd=None):
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            self.calls.append(argv)
            return 0, REVISION + "\n"
        if argv[:2] == ["git", "commit"] and "inject S3" in argv[-1]:
            self.committed = True
        if argv[:2] == ["git", "push"] and self.committed and self.pushed_at is None:
            self.pushed_at = self.ft.wall()
        if argv[-4:] == ["application", runner.S3_APP, "-o", "json"]:
            self.calls.append(argv)
            history = [{"revision": OLD_REVISION, "deployedAt": "2026-11-01T00:00:00Z"}]
            if self.pushed_at is not None and self.applies_after is not None:
                self.app_reads += 1
                if self.app_reads > self.applies_after:
                    if self.deployed_at is None:  # applied 2 s before this read saw it
                        self.deployed_at = self.ft.wall() - 2
                        self.injected = True
                    history.append(
                        {
                            "revision": REVISION,
                            "deployedAt": batch._utc(self.deployed_at).replace(
                                "+00:00", "Z"
                            ),
                        }
                    )
            return 0, json.dumps({"status": {"history": history}})
        return super().__call__(argv, cwd)


class S3InjectionTimeTest(Live):
    """V5, the edge the review names: for S3 the injection is when Argo CD applied the revision."""

    cells = S3_CELL

    def run_s3(self, cluster) -> dict:
        clone = pathlib.Path(self.dir) / "clone"
        (clone / "overlays/dev").mkdir(parents=True)
        (clone / runner.LEVEL_FILE).write_text(
            f'metadata:\n  labels:\n    {runner.LEVEL_LABEL}: "3"\n'
        )
        live = batch.LiveRun(
            tb.runner_for(cluster, self.ft, git_dir=str(clone)),
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

    def test_for_s3_the_injection_time_is_when_argo_cd_applied_the_pushed_revision(
        self,
    ):
        c = ArgoApplies(self.ft)
        r = self.run_s3(c)
        self.assertEqual(r["status"], "valid")
        self.assertEqual(r["injection_revision"], REVISION)
        self.assertEqual(r["injection_at"], batch._utc(c.deployed_at))
        self.assertEqual(r["injection_pushed_at"], batch._utc(c.pushed_at))
        self.assertGreater(c.deployed_at, c.pushed_at)  # the push is not the injection

    def test_an_s3_revision_that_argo_cd_never_applies_discards_the_run(self):
        c = ArgoApplies(self.ft, applies_after=None)
        r = self.run_s3(c)
        self.assertEqual(
            (r["status"], r["discard_reason"]), ("discarded", "injection_not_applied")
        )
        self.assertIsNone(r["injection_at"])
        self.assertEqual(r["injection_pushed_at"], batch._utc(c.pushed_at))


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
