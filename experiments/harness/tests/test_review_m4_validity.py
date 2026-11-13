"""Tests for docs/reviews/2026-11-10-m4-validity.md. The finding's id is in each test's comment.

The six blockers (V1-V5, V19) are fixed: their tests are normal tests, each followed by the edge
cases of the same finding. A test still marked expectedFailure (V11, the escalation metric) states
behaviour the review argues for and fails today; unittest reports an unexpected success as a
failure, so the decorator is removed in the PR that fixes the finding. Offline: the fakes of
test_batch.py, no cluster.
"""

import json
import pathlib
import re
import tempfile
import unittest

from .. import arms, batch, metrics, runner
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
                self.dir, tb.REF, [dict(c) for c in self.cells], precheck_cfg=tb.PRE
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


def _dev_incident(uid, detected_at, phase, labels=None):
    return {
        "metadata": {"uid": uid, "name": f"inc-{uid}", "creationTimestamp": detected_at, "labels": labels or {}},
        "spec": {"target": {"namespace": "nexus-dev", "name": "sample-api"}, "detectedAt": detected_at},
        "status": {"phase": phase},
    }  # fmt: skip


class NotTheRuns(tb.FakeCluster):
    """After the injection two more Incidents on nexus-dev are terminal at once: one whose alert
    started 30 s before the injection (created only after the snapshot), and a smoke Incident."""

    def __call__(self, argv, cwd=None):
        rc, out = super().__call__(argv, cwd)
        if "incidents" in argv and self.injected:
            doc = json.loads(out)
            start = batch.model.parse_time(self.detected_at).timestamp()
            doc["items"][:0] = [
                _dev_incident("stale-alarm", batch._utc(start - 30), "Dismissed"),
                _dev_incident("smoke", self.detected_at, "Recommended", {"nexus.io/smoke": "true"}),
            ]  # fmt: skip
            out = json.dumps(doc)
        return rc, out


class SecondIncident(tb.FakeCluster):
    """Once the run's Incident is terminal, the next alert on the target gets its own Incident."""

    def __call__(self, argv, cwd=None):
        rc, out = super().__call__(argv, cwd)
        if "incidents" in argv and self.injected:
            doc = json.loads(out)
            if doc["items"][-1]["status"]["phase"] == "Dismissed":
                later = batch.model.parse_time(self.detected_at).timestamp() + 60
                doc["items"].insert(0, _dev_incident("second", batch._utc(later), "Executing"))  # fmt: skip
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
    def test_an_incident_of_another_namespace_does_not_end_the_run(self):
        r = self.run_one(OtherIncident(self.ft, phases=("Executing", "Resolved")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Resolved"))
        self.assertEqual(r["incident"]["uid"], "new")
        self.assertEqual(
            [(i["uid"], i["namespace"], i["phase"]) for i in r["other_incidents"]],
            [("prod-noise", "nexus-prod", "Recommended")],
        )

    # V2, the other half of what the review wants ("detected after the injection"): an Incident of
    # the injected namespace is still not the run's when its alert started before the injection
    # (it was only created after the snapshot), or when it is a smoke Incident.
    def test_an_incident_detected_before_the_injection_or_a_smoke_one_is_not_the_runs(
        self,
    ):
        r = self.run_one(NotTheRuns(self.ft, phases=("Executing", "Resolved")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Resolved"))
        self.assertEqual(r["incident"]["uid"], "new")
        self.assertEqual(
            [(i["uid"], i["phase"]) for i in r["other_incidents"]],
            [("smoke", "Recommended"), ("stale-alarm", "Dismissed")],
        )

    def test_the_first_created_incident_is_the_runs_and_stays_it(self):
        # S2 as the review describes it: the first alert's Incident ends Dismissed. That is the
        # system's answer to the fault and the run records it; a later Incident on the same
        # target does not replace it.
        r = self.run_one(SecondIncident(self.ft, phases=("Diagnosing", "Dismissed")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Dismissed"))
        self.assertEqual(r["incident"]["uid"], "new")
        self.assertEqual([i["uid"] for i in r["other_incidents"]], ["second"])

    # V3
    def test_a_hang_that_was_not_remediated_is_a_valid_run(self):
        r = self.run_one(HangStays(self.ft, phases=("Diagnosing", "Escalated")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Escalated"))
        self.assertEqual(r["locust_end"], {"ok": True, "failures": []})
        # what the service was doing is on the record, as data
        self.assertEqual(r["rate_end"]["rps"], {"nexus-dev": 0.0, "nexus-prod": 0.0})
        self.assertEqual(r["rate_end"]["baseline_rps"], 80)

    # V3, the same end state under a Resolved Incident (a restart that verified while the hang went
    # on, #197 A2): still a remediation that failed, so still a run that counts. Voiding it would
    # requeue the cell until a clean Resolved came.
    def test_a_resolved_incident_over_a_service_that_still_does_not_answer_is_not_voided(
        self,
    ):
        r = self.run_one(HangStays(self.ft, phases=("Executing", "Resolved")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Resolved"))
        self.assertEqual(r["rate_end"]["rps"]["nexus-dev"], 0.0)

    def test_an_unreadable_rate_at_the_end_is_recorded_as_null_and_voids_nothing(self):
        class NoRate(tb.FakeCluster):
            def __call__(self, argv, cwd=None):
                out = super().__call__(argv, cwd)
                if self.injected:
                    self.prom = {
                        ":rate2m": None
                    }  # Prometheus stops answering that query
                return out

        r = self.run_one(NoRate(self.ft))
        self.assertEqual(r["status"], "valid")
        self.assertEqual(r["rate_end"]["rps"], {"nexus-dev": None, "nexus-prod": None})

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
        self.assertIn("rate_end", r)  # kept for a voided run too

    def test_a_locust_that_does_not_answer_at_the_end_voids_the_run(self):
        # No answer is not "running": the run cannot show the load it was measured under.
        live_cluster = HangStays(self.ft, phases=("Diagnosing", "Escalated"))
        live = batch.LiveRun(
            tb.runner_for(live_cluster, self.ft),
            tb.PRE,
            wait_s=50,
            poll_s=5,
            sleep=self.ft.sleep,
            monotonic=self.ft.monotonic,
            wall=self.ft.wall,
            stat=tb.audit_fresh(self.ft),
        )
        answer = live.r.http_get
        live.r.http_get = lambda url: (0, "") if live_cluster.injected else answer(url)
        self.drive(live, max_runs=1)
        r = self.reload().runs[0]
        self.assertEqual((r["status"], r["void_reason"]), ("void", "locust_end_check"))
        self.assertEqual(r["locust_end"]["failures"], ["locust:no_answer"])

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


ENDPOINT = (
    "http://nexus-reasoner.nexus-reasoner.svc:8080"  # precheck's reasoner defaults
)
R3_KEYS = {"reasonerEndpoint": ENDPOINT, "reasonerMode": "llm",
           "reasonerModel": "openai/gpt-oss-120b", "reasonerEffort": "low"}  # fmt: skip
R1_KEYS = {"reasonerEndpoint": "", "reasonerMode": "rules", "reasonerModel": "", "reasonerEffort": ""}  # fmt: skip


def answers(status):
    """A fake cluster whose Incident carries this status.reasoner, whatever was configured."""

    class Answers(tb.FakeCluster):
        def reasoner_status(self):
            return status

    return Answers


class ArmTest(Live):
    cells = R3_CELL

    # V4
    def test_the_cells_arm_is_written_to_the_operator_before_the_run(self):
        c = tb.FakeCluster(self.ft)
        r = self.run_one(c)
        patches = [" ".join(a) for a in c.calls if "nexus-operator-config" in a]
        self.assertTrue(any("reasonerMode" in p for p in patches))
        self.assertEqual(c.operator_config, c.operator_config | R3_KEYS)
        self.assertEqual(r["status"], "valid")
        self.assertEqual(
            r["arm"],
            {
                "name": "R3",
                "written": R3_KEYS,
                "observed": {"mode": "llm", "modelId": "openai/gpt-oss-120b", "fallbackUsed": False},
                "mismatch": None,
            },
        )  # fmt: skip

    # V4
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
        self.assertEqual((r["status"], r["discard_reason"]), ("discarded", "arm_mismatch"))  # fmt: skip
        self.assertEqual(r["arm"]["mismatch"], "mode 'rules', the arm's is 'llm'")
        # the next run would be wrong the same way: the batch stops until the owner clears it
        self.assertEqual(self.reload().doc["halt"]["reason"], "arm_mismatch")

    # V4, "a fallback aside": R1 answering for a Reasoner call that failed is the arm's own run.
    def test_a_fallback_to_r1_is_the_arms_own_run_and_stays_valid(self):
        r = self.run_one(tb.FakeCluster(self.ft, fallback=True))
        self.assertEqual((r["status"], r["fallbackUsed"]), ("valid", True))
        self.assertEqual(
            r["arm"]["observed"],
            {"mode": "rules", "modelId": None, "fallbackUsed": True},
        )
        self.assertIsNone(r["arm"]["mismatch"])

    def test_the_arms_mode_with_another_model_is_a_mismatch(self):
        small = {"mode": "llm", "modelId": "openai/gpt-oss-20b", "fallbackUsed": False}
        r = self.run_one(answers(small)(self.ft))
        self.assertEqual(r["discard_reason"], "arm_mismatch")
        self.assertIn("openai/gpt-oss-20b", r["arm"]["mismatch"])

    def test_an_incident_that_ended_before_any_proposal_contradicts_no_arm(self):
        # e.g. Escalated / evidence_error from Detected: a failed run, and still a run.
        r = self.run_one(answers(None)(self.ft, phases=("Detected", "Escalated")))
        self.assertEqual((r["status"], r["terminal"]), ("valid", "Escalated"))
        self.assertEqual((r["arm"]["observed"], r["arm"]["mismatch"]), (None, None))


class ArmAcrossCellsTest(Live):
    cells = (
        dict(R3_CELL[0]),
        dict(R3_CELL[0], cell="C1/S1/L3", reasoner="R1"),
    )

    def test_an_r1_cell_after_an_r3_cell_turns_the_reasoner_client_off(self):
        c = tb.FakeCluster(self.ft)
        self.run_one(c)
        c.injected, c.detected_at = (
            False,
            None,
        )  # the fake yields one Incident per injection
        self.run_one(c)
        written = [
            {k: v for k, v in json.loads(a[a.index("-p") + 1])["data"].items() if k.startswith("reasoner")}
            for a in c.calls
            if "nexus-operator-config" in a and "patch" in a
        ]  # fmt: skip
        self.assertEqual(written, [R3_KEYS, R1_KEYS])
        runs = self.reload().runs
        self.assertEqual([r["status"] for r in runs], ["valid", "valid"])
        self.assertEqual([r["arm"]["observed"]["mode"] for r in runs], ["llm", "rules"])

    def test_a_fallback_on_a_cell_that_calls_no_reasoner_is_a_mismatch(self):
        self.q.doc["cells"].reverse()  # the R1 cell first
        self.q.save()
        fell_back = {"mode": "rules", "fallbackUsed": True}
        r = self.run_one(answers(fell_back)(self.ft))
        self.assertEqual((r["cell"], r["discard_reason"]), ("C1/S1/L3", "arm_mismatch"))  # fmt: skip


class ArmKeysTest(unittest.TestCase):
    def test_the_operator_reads_every_arm_the_harness_writes(self):
        # the operator's own parser (operator/nexus_operator/config.py), on the harness's keys
        from nexus_operator import config as operator_config

        for name, arm in arms.ARMS.items():
            keys = batch.operator_reasoner(name, ENDPOINT)
            self.assertEqual(set(keys), set(runner.REASONER_KEYS), name)
            got = operator_config.parse({"data": {"alertmanagerURL": "http://am:9093", **keys}})  # fmt: skip
            self.assertEqual(got.reasoner_mode, arm["mode"], name)
            self.assertEqual(got.reasoner_model, arm["model"] or "", name)
            # R1 is the embedded Rules Engine: no endpoint, so the Reasoner Client stays off
            self.assertEqual(got.reasoner_endpoint, "" if name == "R1" else ENDPOINT, name)  # fmt: skip
        self.assertEqual(batch.operator_reasoner("R1", ENDPOINT), R1_KEYS)
        self.assertEqual(batch.operator_reasoner("R3", ENDPOINT), R3_KEYS)
        self.assertEqual(batch.operator_reasoner("R0", ENDPOINT)["reasonerMode"], "adversarial")  # fmt: skip

    def test_a_cell_whose_reasoner_is_not_an_arm_is_refused_at_init(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(batch.Refused):
            batch.Queue.create(d, tb.REF, [dict(R3_CELL[0], reasoner="R9")], precheck_cfg=tb.PRE)  # fmt: skip

    def test_the_arm_is_written_whole_or_not_at_all(self):
        r = runner.Runner("ctx", exec_=lambda argv, cwd=None: (0, ""))
        with self.assertRaises(ValueError):
            r.plan_operator_config("on", 5, {"reasonerMode": "llm"})
        patch = r.plan_operator_config("on", 5)[0].argv
        self.assertEqual(
            json.loads(patch[patch.index("-p") + 1])["data"],
            {"advisoryChecks": "on", "breakerEpoch": "5"},
        )  # no arm given: the reasoner keys are left as they are


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
