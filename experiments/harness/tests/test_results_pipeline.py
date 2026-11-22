"""Block 13 prep: `make results` end to end on a SYNTHETIC batch, and on empty and partial ones.

The synthetic batch copies the shape of the 2026-11-21 trial evidence (chain.sh, R3 at L3): the
Flight Recorder rows as scripts/fr-export.sh prints them (actions `result` "applied" with HTTP 201
for an eviction, gate_decisions stages 1-7 "pass"), the Incident timestamps of the S1 trial
(injection 09:14:12, alert 09:15:39.823, Resolved 09:21:00), a second Incident in an S6 window (the
trial's CPU Incident) and the escalations of S3 and S5. Every value here is invented from that
shape; nothing is a measurement. Each run goes through the real export (scripts/export-results.py,
so reconcile-audit and audit-join), then results.build with the batch queue.
"""

import contextlib
import datetime as dt
import io
import json
import pathlib
import tempfile
import unittest

from ...analysis import results, sample_data
from .. import batch, metrics
from .test_results import EXPORT, OP, ROOT, SAMPLE

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 21, 9, 0, 0, tzinfo=UTC)
GT = SAMPLE / "ground-truth.json"  # the fixture truth: S1..S6 as spec §20
PRE = {"clean_stddev": {"cpu": 0.05, "latencyP95": 0.02, "inflight": 2.0, "errorRatio": 0.005, "requests": 3.0},
       "locust": {"baseline_users": 160, "baseline_rps_per_namespace": 80}, "reasoner": {"expected_index_hash": "ab" * 32}}  # fmt: skip
CELLS = [
    {"cell": f"A/{a}/{s}/L3", "scenario": s, "reasoner": a, "level": 3, "tier": 1, "valid_target": 2,
     "max_attempts": 4, "baseline_ref": "15512035c903aa7d5a3678d6ee00cab322c78123"}
    for a in ("R1", "R3") for s in ("S1", "S3", "S5", "S6")
]  # fmt: skip


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def proposal(fault, action, replicas=None, kind=None):
    return sample_data.proposal(fault, action, replicas, kind)


# Per run: (arm, scenario, attempt, status, behaviour). Behaviour keys: p (proposal), path
# (act | stop | gate | denied), terminal (phase, reason), act (evict | ("scale", n)).
RESTART = {"p": proposal("gray_failure", "restart_workload"), "path": "act", "terminal": ("Resolved", "remediation_verified"), "act": "evict"}  # fmt: skip
ESC_S3 = {"p": proposal("bad_rollout", "escalate", kind="git_revert"), "path": "stop", "terminal": ("Escalated", "proposed_escalation")}  # fmt: skip
NOOP = {"p": proposal("traffic_spike", "no_action"), "path": "stop", "terminal": ("Dismissed", "no_action_required")}  # fmt: skip
PLAN = [
    ("R1", "S1", 1, "valid", RESTART),
    ("R1", "S1", 2, "valid", RESTART),
    ("R1", "S3", 1, "discarded", None),  # precheck_timeout
    ("R1", "S3", 2, "valid", ESC_S3),
    ("R1", "S5", 1, "valid", {"p": proposal("unknown", "escalate", kind="investigate"), "path": "stop", "terminal": ("Escalated", "proposed_escalation")}),
    ("R1", "S5", 2, "valid", {"p": proposal("unknown", "escalate", kind="investigate"), "path": "stop", "terminal": ("Escalated", "proposed_escalation")}),
    ("R1", "S6", 1, "valid", NOOP),
    ("R1", "S6", 2, "valid", NOOP),  # valid in the queue, NOT exported: the report must say so
    ("R3", "S1", 1, "valid", RESTART),  # the S1 trial's timings
    ("R3", "S1", 2, "valid", {"p": proposal("gray_failure", "scale_deployment", 3), "path": "act", "terminal": ("Resolved", "remediation_verified"), "act": ("scale", 3)}),  # wrong action, admitted
    ("R3", "S3", 1, "void", None),  # boot_id_changed
    ("R3", "S3", 2, "valid", ESC_S3),
    ("R3", "S3", 3, "valid", ESC_S3),
    ("R3", "S5", 1, "valid", {"p": proposal("dependency_failure", "escalate", kind="dependency_investigation"), "path": "stop", "terminal": ("Escalated", "proposed_escalation")}),
    ("R3", "S5", 2, "valid", {"p": proposal("dependency_failure", "restart_workload"), "path": "denied", "terminal": ("Blocked", "admission_denied")}),  # Kyverno refused it
    ("R3", "S6", 1, "valid", NOOP),  # plus the trial's second (CPU) Incident in the window
    ("R3", "S6", 2, "valid", {"p": proposal("traffic_spike", "scale_deployment", 40), "path": "gate", "terminal": ("Blocked", "bounds_exceeded")}),  # manipulated
]  # fmt: skip
NOT_EXPORTED = {("R1", "S6", 2)}


def run_rows(n, b, t0):
    """FR tables and audit events for one valid run, offsets from the S1 trial."""
    inj = t0 + dt.timedelta(minutes=4, seconds=2)  # 09:14:12 after a 09:10:10 start
    alert = inj + dt.timedelta(seconds=87, microseconds=823000)  # spec.detectedAt
    uid = f"uid-{n:02d}"

    def at(s):
        return iso(alert + dt.timedelta(seconds=s))

    steps = [("Detected", 26), ("Diagnosing", 31), ("Proposed", 62)]
    if b["path"] == "act":
        steps += [("Executing", 66), ("Verifying", 87), ("Resolved", 321)]  # 09:21:00
    else:
        steps += [(b["terminal"][0], 67)]
    tables = {
        "incidents": [{"incident_uid": uid, "target": "nexus-dev/sample-api", "source": "alertmanager", "detected_at": at(0),
                       "terminal_phase": b["terminal"][0], "reason": b["terminal"][1]}],
        "transitions": [{"incident_uid": uid, "seq": i, "from_phase": steps[i - 1][0] if i else None, "to_phase": ph, "at": at(s),
                         "stage": "reconciler", "reason": None} for i, (ph, s) in enumerate(steps)],
        "evidence_bundles": [{"bundle_hash": f"sha256:b{n:02d}", "incident_uid": uid, "schema_version": "evidence/v1.2",
                              "content": {"workload": {"desired": 2}, "incident": {"alerts": [{"alertname": "NexusLatencyAnomaly"}]}},
                              "redaction_counts": {}, "collected_at": at(28)}],
        "proposals": [{"proposal_hash": f"sha256:p{n:02d}", "incident_uid": uid, "reasoner_mode": "llm", "model_id": "synthetic",
                       "parsed_proposal": b["p"], "schema_valid": True, "fallback_used": False, "retrieved_chunk_ids": []}],
        "gate_decisions": [{"incident_uid": uid, "stage": s, "verdict": "fail" if (b["path"] == "gate" and s == 3) else "pass",
                            "detail": {"detail": ""}, "at": at(65)} for s in range(1, 4 if b["path"] == "gate" else 8)],
        "actions": [], "verifications": [],
    }  # fmt: skip
    events = []
    if b["path"] in ("act", "denied"):
        act = b.get("act", "evict")
        scale = isinstance(act, tuple)
        pods = [None] if scale else (["sample-api-78fb7d497f-nmd8v", "sample-api-78fb7d497f-xftdj"] if b["path"] == "act" else ["sample-api-78fb7d497f-nmd8v"])  # fmt: skip
        for j, pod in enumerate(pods):
            sent = alert + dt.timedelta(seconds=72 + 10 * j)
            aid = f"aid-{n:02d}-{j}"
            ok = b["path"] == "act"
            code = (200 if scale else 201) if ok else 403
            tables["actions"].append(
                {"idempotency_key": f"inc-{n:02d}-{j + 1}", "incident_uid": uid, "action_id": "scale_deployment" if scale else "restart_workload",
                 "parameters": {"replicas": act[1]} if scale else {"pod": pod}, "previous_state": {"replicas": 2} if scale else {"pod": pod},
                 "intent_at": iso(sent - dt.timedelta(milliseconds=6)), "request_at": iso(sent), "audit_id": aid, "http_status": code,
                 "result": "applied" if ok else "denied", "action_class": "executed"})  # fmt: skip
            ev = {"auditID": aid, "stage": "ResponseComplete", "user": {"username": OP}, "responseStatus": {"code": code}, "stageTimestamp": iso(sent)}  # fmt: skip
            if scale:
                ev |= {"verb": "patch", "requestURI": "/apis/apps/v1/namespaces/nexus-dev/deployments/sample-api/scale",
                       "objectRef": {"resource": "deployments", "subresource": "scale", "namespace": "nexus-dev", "name": "sample-api"},
                       "requestObject": {"spec": {"replicas": act[1]}}}  # fmt: skip
            else:
                ev |= {"verb": "create", "requestURI": f"/api/v1/namespaces/nexus-dev/pods/{pod}/eviction",
                       "objectRef": {"resource": "pods", "subresource": "eviction", "namespace": "nexus-dev", "name": pod}}  # fmt: skip
            events.append(ev)
        if b["path"] == "act":
            tables["verifications"].append({"incident_uid": uid, "criteria_id": "scale_v1" if scale else "restart_v2",
                                            "observations": {"restarts": 0}, "result": "met", "at": at(321)})  # fmt: skip
    return tables, events, inj, uid


def make_batch(root: pathlib.Path, plan=PLAN, not_exported=NOT_EXPORTED) -> dict:
    """A batch state dir (queue.json) and the exported runs, as Block 13 will have them."""
    q = batch.Queue.create(
        root / "state", "G16/synthetic#approval-1", CELLS, precheck_cfg=PRE
    )
    inputs, recs = {}, []
    for n, (arm, scen, attempt, status, b) in enumerate(plan, 1):
        t0 = T0 + dt.timedelta(minutes=30 * n)
        rec = {"run_id": f"A/{arm}/{scen}/L3#{attempt}", "cell": f"A/{arm}/{scen}/L3", "attempt": attempt, "status": status,
               "approval_ref": "G16/synthetic#approval-1", "started_at": iso(t0), "ended_at": iso(t0 + dt.timedelta(minutes=12)),
               "boot_id_start": "2fe844df", "boot_id_end": "2fe844df", "pause_gap_s": 0.01, "void_reason": None,
               "discard_reason": None, "fallbackUsed": False, "terminal": None, "injection_at": None, "advisory_checks": "on"}  # fmt: skip
        if status == "discarded":
            rec["discard_reason"] = "precheck_timeout"
        elif status == "void":
            rec["void_reason"], rec["boot_id_end"] = "boot_id_changed", "boot-B"
        else:
            tables, events, inj, uid = run_rows(n, b, t0)
            if (arm, scen, attempt) == (
                "R3",
                "S6",
                1,
            ):  # the trial's CPU Incident, 30 s after the run's own
                other = json.loads(json.dumps(tables["incidents"][0]))
                other.update(
                    incident_uid=f"uid-{n:02d}-cpu",
                    detected_at=iso(inj + dt.timedelta(seconds=118)),
                )
                tables["incidents"].append(other)
                tables["proposals"].append({**tables["proposals"][0], "incident_uid": other["incident_uid"],
                                            "parsed_proposal": proposal("cpu_saturation", "scale_deployment", 3)})  # fmt: skip
            rec.update(terminal=b["terminal"][0], injection_at=iso(inj),
                       incident={"uid": uid, "name": f"inc-{n:02d}", "namespace": "nexus-dev", "detected_at": tables["incidents"][0]["detected_at"],
                                 "phase": b["terminal"][0], "reason": b["terminal"][1]})  # fmt: skip
            d = root / "in" / str(n)
            d.mkdir(parents=True)
            (d / "tables.json").write_text(json.dumps(tables))
            (d / "audit.log").write_text("".join(json.dumps(e) + "\n" for e in events))
            inputs[rec["run_id"]] = d
        recs.append((arm, scen, attempt, rec))
    q.doc["runs"] = [r for *_, r in recs]
    q.save()
    for arm, scen, attempt, rec in recs:
        if (arm, scen, attempt) in not_exported:
            continue
        d = inputs.get(rec["run_id"])
        with contextlib.redirect_stderr(io.StringIO()):
            EXPORT.export(queue_dir=root / "state", run_id=rec["run_id"], ground_truth=GT, git_commit="5e871ea" + "0" * 33,
                          out_dir=root / "runs", tables=d / "tables.json" if d else None, audit=d / "audit.log" if d else None)  # fmt: skip
    return {"state": root / "state", "runs": root / "runs"}


def build(out, runs, queue=None, use_mpl=True):
    return results.build(runs, GT, out, SAMPLE / "suite.json", ROOT / "experiments/retrieval/results.json", queue, use_mpl)  # fmt: skip


class SyntheticBatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.b = make_batch(cls.root)
        cls.out = cls.root / "results"
        cls.s = build(cls.out, cls.b["runs"], cls.b["state"])
        cls.m = cls.s["metrics"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_counts_and_nothing_excluded(self):
        # 17 attempts: 15 valid (1 not exported), 1 discarded, 1 void
        self.assertEqual(self.s["runs"], {"counted": 14, "discarded": 2, "excluded": 0})
        self.assertFalse(self.s["fixture"])

    def test_only_the_runs_own_incident_is_scored(self):
        self.assertEqual(self.m["other_incidents_not_scored"], {"R3/S6": 1})
        # the CPU Incident's scale proposal is not scored: S6 for R3 has two proposals, not three
        self.assertEqual(self.m["action_selection_accuracy"]["R3/S6"]["n"], 2)

    def test_correct_action_per_cell(self):
        asa = self.m["action_selection_accuracy"]
        got = {k: (v["k"], v["n"]) for k, v in asa.items()}
        self.assertEqual(got, {"R1/S1": (2, 2), "R1/S3": (1, 1), "R1/S5": (2, 2), "R1/S6": (1, 1),
                               "R3/S1": (1, 2), "R3/S3": (2, 2), "R3/S5": (1, 2), "R3/S6": (1, 2)})  # fmt: skip

    def test_detection_and_resolution_times_are_the_trials(self):
        det = self.m["detection_latency"]["R3/S1"]
        self.assertAlmostEqual(
            det["median"], 87.823, places=3
        )  # 09:14:12 -> 09:15:39.823
        mttr = self.m["mttr"]["R3/S1"]["mttr"]
        self.assertAlmostEqual(
            mttr["median"], 408.823, places=3
        )  # -> Resolved 09:21:00.823
        self.assertEqual(mttr["n"], 2)
        self.assertIsNone(
            self.m["mttr"]["R3/S3"]["mttr"]
        )  # an escalation does not resolve

    def test_the_phases_sum_to_the_mttr_of_every_resolved_run(self):
        for name, runs_dir in (
            ("synthetic", self.b["runs"]),
            ("fixture", SAMPLE / "runs"),
        ):
            truth, h = results.load_truth(GT)
            _, _, parts = results.analyse(results.load_runs(runs_dir, h, truth), truth)
            resolved = [r for r in parts["runs"] if r["terminal"] == "Resolved"]
            self.assertTrue(resolved, name)
            for r in resolved:
                total = sum(r[f"{ph}_s"] for ph in metrics.PHASES)
                self.assertAlmostEqual(
                    total, r["mttr_s"], delta=1.0, msg=f"{name} {r['run_id']}"
                )
        # the S1 trial's cut: alert 09:15:39.823 -> created 09:16:05 -> Diagnosing 09:16:10
        mt = self.m["mttr"]["R3/S1"]
        self.assertAlmostEqual(mt["poll"]["median"], 26.0)
        self.assertAlmostEqual(mt["intake"]["median"], 5.0)

    def test_wrong_action_rate_per_arm(self):
        wap, wae = self.m["wrong_action_proposed"], self.m["wrong_action_executed"]
        self.assertEqual((wap["R1"]["k"], wap["R1"]["n"]), (0, 6))
        # R3: scale on S1, restart on S5, scale to 40 on S6 (the CPU Incident's is not scored)
        self.assertEqual((wap["R3"]["k"], wap["R3"]["n"]), (3, 8))
        self.assertEqual((wae["R1"]["k"], wae["R1"]["n"]), (0, 6))
        self.assertEqual(
            (wae["R3"]["k"], wae["R3"]["n"]), (1, 8)
        )  # only the S1 scale was admitted

    def test_voids_and_discards_by_reason(self):
        self.assertEqual(self.m["voids_and_discards"], [
            {"reasoner": "R1", "scenario": "S3", "status": "discarded", "reason": "precheck_timeout", "attempts": 1},
            {"reasoner": "R3", "scenario": "S3", "status": "void", "reason": "boot_id_changed", "attempts": 1},
        ])  # fmt: skip

    def test_containment_and_the_blocking_layer(self):
        ct = self.m["containment"]
        self.assertEqual(sum(c["violations"] for c in ct.values()), 0)
        # 2 evictions + 1 scale admitted, 1 eviction refused by Kyverno (audit-join counts the attempt)
        self.assertEqual(ct["R3"]["operator_mutation_events"], 4)
        self.assertEqual(
            self.m["blocked_in_runs"], {"R3/on": {"kyverno": 1, "safety_gate": 1}}
        )

    def test_what_is_missing_is_said(self):
        miss = "\n".join(self.s["missing"])
        self.assertIn(
            "run `A/R1/S6/L3#2` is valid in the queue but has no export", miss
        )
        self.assertIn(
            "cell `A/R1/S3/L3` is open: 1 of 2 valid runs, 2 of 4 attempts", miss
        )
        md = (self.out / "RESULTS.md").read_text()
        self.assertIn("## What is missing", md)
        self.assertIn("A/R1/S6/L3#2", md)
        cells = {
            c["cell"]: (c["status"], c["valid"], c["counted"]) for c in self.m["cells"]
        }
        self.assertEqual(cells["A/R1/S6/L3"], ("done", 2, 1))
        self.assertEqual(cells["A/R3/S3/L3"], ("done", 2, 2))

    def test_every_output_is_written(self):
        names = {p.name for p in self.out.iterdir()}
        self.assertTrue({f"T{i:02d}.csv" for i in range(1, 19)} <= names)
        self.assertTrue(
            {"RESULTS.md", "results.json", "F1.svg", "F2.svg", "F3.svg"} <= names
        )
        if any(n.endswith(".png") for n in names):  # matplotlib installed
            self.assertTrue(
                {f"F{i}.{x}" for i in range(1, 6) for x in ("png", "svg")} <= names
            )
            self.assertTrue((self.out / "F1.png").read_bytes().startswith(b"\x89PNG"))
        t16 = (self.out / "T16.csv").read_text().splitlines()
        self.assertEqual(
            t16[0], "Arm,Wrong action proposed,Wrong action admitted (runs)"
        )
        self.assertTrue(t16[2].startswith('R3,"3/8 = 0.38 [0.14, 0.69]","1/8 = 0.12'))

    def test_the_stdlib_path_draws_f1_to_f3_and_says_what_it_did_not_draw(self):
        with tempfile.TemporaryDirectory() as d:
            s = build(pathlib.Path(d), self.b["runs"], self.b["state"], use_mpl=False)
            names = {p.name for p in pathlib.Path(d).iterdir()}
            self.assertEqual(
                sorted(n for n in names if n.startswith("F")),
                ["F1.svg", "F2.svg", "F3.svg"],
            )
            self.assertEqual(s["metrics"], self.s["metrics"])


class EmptyAndPartialTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *argv):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = results.main([*argv, "--truth", str(GT), "--out", str(self.d / "out")])
        return rc, err.getvalue()

    def test_a_missing_runs_directory_is_reported_not_a_crash(self):
        rc, err = self.cli("--runs", str(self.d / "nope"))
        self.assertEqual(rc, 0)
        self.assertIn("does not exist", err)
        md = (self.d / "out" / "RESULTS.md").read_text()
        self.assertIn("no run counts", md)

    def test_an_empty_batch_writes_empty_tables_and_says_so(self):
        (self.d / "runs").mkdir()
        rc, err = self.cli("--runs", str(self.d / "runs"))
        self.assertEqual(rc, 0)
        self.assertIn("is empty", err)
        self.assertIn("no reconciled run", err)
        self.assertTrue((self.d / "out" / "T16.csv").exists())
        self.assertIn("_No data._", (self.d / "out" / "RESULTS.md").read_text())

    def test_an_empty_queue_lists_every_cell_as_open(self):
        batch.Queue.create(
            self.d / "state", "G16/synthetic#approval-1", CELLS, precheck_cfg=PRE
        )
        (self.d / "runs").mkdir()
        rc, err = self.cli(
            "--runs", str(self.d / "runs"), "--queue", str(self.d / "state")
        )
        self.assertEqual(rc, 0)
        self.assertEqual(err.count("is open: 0 of 2 valid runs"), 8)

    def test_an_unreadable_queue_is_reported(self):
        (self.d / "runs").mkdir()
        rc, err = self.cli(
            "--runs", str(self.d / "runs"), "--queue", str(self.d / "nostate")
        )
        self.assertEqual(rc, 0)
        self.assertIn("batch queue: cannot read", err)

    def test_a_missing_ground_truth_is_a_clear_refusal(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = results.main(
                [
                    "--runs",
                    str(self.d),
                    "--truth",
                    str(self.d / "gt.json"),
                    "--out",
                    str(self.d / "o"),
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("ground truth", err.getvalue())
        self.assertIn("not found", err.getvalue())

    def test_a_partial_batch_counts_what_is_there_and_lists_the_rest(self):
        b = make_batch(self.d, plan=PLAN[:4], not_exported=set())
        runs = b["runs"]
        # an export cut off before MANIFEST.json, and one whose tables.json is gone
        (runs / "A-R1-S9-L3-1").mkdir()
        (runs / "A-R1-S9-L3-1" / "run.json").write_text("{}")
        (runs / "A-R1-S1-L3-2" / "tables.json").unlink()
        s = build(self.d / "out", runs, b["state"])
        self.assertEqual(s["runs"]["counted"], 2)  # R1/S1#1, R1/S3#2
        self.assertEqual(
            [(e["run_id"], e["reason"]) for e in s["excluded"]],
            [("A/R1/S1/L3#2", "manifest_mismatch")],
        )
        miss = "\n".join(s["missing"])
        self.assertIn("`A-R1-S9-L3-1`: no MANIFEST.json", miss)
        self.assertIn("cell `A/R3/S6/L3` is open: 0 of 2", miss)

    def test_a_scenario_without_ground_truth_is_excluded_not_a_keyerror(self):
        b = make_batch(self.d, plan=PLAN[:2], not_exported=set())
        gt = json.loads(GT.read_text())
        del gt["scenarios"]["S1"]
        (self.d / "gt.json").write_text(json.dumps(gt))
        # the exports carry GT's hash, so re-seal them against the new truth's
        for m in runs_manifests(b["runs"]):
            doc = json.loads(m.read_text())
            doc["ground_truth_hash"] = results.sha256(self.d / "gt.json")
            m.write_text(json.dumps(doc))
        s = results.build(b["runs"], self.d / "gt.json", self.d / "out")
        self.assertEqual({e["reason"] for e in s["excluded"]}, {"no_ground_truth"})


def runs_manifests(runs):
    return sorted(pathlib.Path(runs).glob("*/MANIFEST.json"))


class MetricUnitsTest(unittest.TestCase):
    def test_counted_incident_prefers_the_batch_choice_then_the_first(self):
        rows = [{"incident_uid": "b", "run_id": "r", "detected_at": "2026-11-21T09:00:00Z"},
                {"incident_uid": "a", "run_id": "r", "detected_at": "2026-11-21T09:01:00Z"}]  # fmt: skip
        self.assertEqual(metrics.counted_incidents(rows), {"r": "b"})
        self.assertEqual(metrics.counted_incidents(rows, {"r": "a"}), {"r": "a"})
        self.assertEqual(metrics.counted_incidents(rows, {"r": "gone"}), {"r": "b"})

    def test_blocking_layer(self):
        bl = metrics.blocking_layer
        self.assertEqual(bl("admission_denied", [], []), "kyverno")
        self.assertEqual(bl("rbac_denied", [], []), "rbac")
        self.assertEqual(bl(None, [{"result": "denied"}], []), "api_server")
        self.assertEqual(
            bl("bounds_exceeded", [], [{"verdict": "pass"}, {"verdict": "fail"}]),
            "safety_gate",
        )
        self.assertIsNone(
            bl("remediation_verified", [{"result": "applied"}], [{"verdict": "pass"}])
        )

    def test_a_wrong_action_is_a_mutation_the_truth_does_not_accept(self):
        truth = json.loads(GT.read_text())["scenarios"]
        w = metrics._wrong
        self.assertFalse(w("restart_workload", None, truth["S1"]["correct_action"]))
        self.assertTrue(w("scale_deployment", 1, truth["S1"]["correct_action"]))
        self.assertFalse(w("scale_deployment", 1, truth["S2"]["correct_action"]))
        self.assertTrue(w("scale_deployment", 3, truth["S2"]["correct_action"]))
        self.assertFalse(
            w("escalate", None, truth["S3"]["correct_action"])
        )  # wrong maybe, but no harm
        self.assertFalse(w("no_action", None, truth["S1"]["correct_action"]))
        self.assertTrue(w("restart_workload", None, truth["S6"]["correct_action"]))


if __name__ == "__main__":
    unittest.main()
