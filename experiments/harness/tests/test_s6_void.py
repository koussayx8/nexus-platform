"""The S6 void rule (pre-registered on #285, experiments/ground-truth.json scenarios.S6.void_if):
an S6 run whose counted Incident's evidence bundle has metrics.zscores.latencyP95 > 3 is void.
Offline: the live run against test_batch's fake cluster, whose Flight Recorder answers the read."""

import json
import pathlib
import re
import unittest

from .. import batch, metrics
from .test_batch import (
    ABSENT,
    BASELINE_REF,
    CELLS,
    FAIL,
    PRE,
    REF,
    Base,
    FakeCluster,
    FakeTime,
    audit_fresh,
    runner_for,
)

GROUND_TRUTH = pathlib.Path(__file__).resolve().parents[2] / "ground-truth.json"
# #285 at b3a66054aa932cb980b74c55471827ea0a48587d, scenarios.S6.void_if.condition, verbatim.
CONDITION_285 = (
    "The counted Incident's evidence bundle has metrics.zscores.latencyP95 > 3."
)
S6 = {"cell": "C1/S6/L3", "scenario": "S6", "reasoner": "R1", "level": 3, "tier": 1,
      "valid_target": 1, "max_attempts": 3, "baseline_ref": BASELINE_REF, "users": 160, "spawn_rate": 4}  # fmt: skip


class PinnedToThePreRegistrationTest(unittest.TestCase):
    def test_the_threshold_signal_and_scenario_are_285s_wording(self):
        self.assertEqual(batch.S6_VOID_CONDITION, CONDITION_285)
        m = re.fullmatch(
            r"The counted Incident's evidence bundle has metrics\.zscores\.(\w+) > (\d+)\.",
            CONDITION_285,
        )
        self.assertEqual(
            (m.group(1), int(m.group(2))), (batch.S6_VOID_SIGNAL, batch.S6_VOID_Z)
        )
        self.assertEqual(batch.S6_VOID_SCENARIO, "S6")

    @unittest.skipUnless(
        GROUND_TRUTH.exists(), "ground-truth.json lands with #285 at the freeze"
    )
    def test_the_merged_ground_truth_still_says_the_same(self):
        s6 = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["scenarios"]["S6"]
        self.assertEqual(s6["void_if"]["condition"], batch.S6_VOID_CONDITION)
        self.assertTrue(s6["void_if"]["outcome"].startswith("void"))
        others = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["scenarios"]
        self.assertEqual(
            [k for k, v in others.items() if isinstance(v, dict) and "void_if" in v],
            ["S6"],
        )


class RuleTest(unittest.TestCase):
    def test_above_three_voids_three_and_below_do_not(self):
        self.assertEqual(batch.s6_void("S6", 3.5), batch.S6_VOID_ABOVE)
        self.assertEqual(batch.s6_void("s6", 3.0001), batch.S6_VOID_ABOVE)
        for z in (2.9, 3, 3.0, -7.0, 0):
            self.assertIsNone(batch.s6_void("S6", z), z)

    def test_a_z_that_is_not_a_number_is_unknown_never_valid(self):
        for z in (None, "3.5", True, float("nan"), [], {}):
            self.assertEqual(batch.s6_void("S6", z), batch.S6_VOID_UNKNOWN, repr(z))

    def test_other_scenarios_are_never_voided_by_it(self):
        for sc in ("S1", "S2", "S3", "S4", "S5"):
            for z in (3.5, 99.0, None, "x"):
                self.assertIsNone(batch.s6_void(sc, z), (sc, z))

    def test_only_a_bundle_hash_reaches_the_sql(self):
        h = "sha256:" + "0" * 64
        self.assertIn(f"bundle_hash = '{h}'", batch.bundle_z_sql(h))
        self.assertIn(
            "content->'metrics'->'zscores'->'latencyP95'", batch.bundle_z_sql(h)
        )
        for bad in ("sha256:abc", "sha256:" + "0" * 63 + "'", "'; DROP TABLE runs; --"):
            with self.assertRaises(ValueError):
                batch.bundle_z_sql(bad)


class LiveS6Test(Base):
    def setUp(self):
        super().setUp()
        self.ft = FakeTime(self.clock)

    def run_cell(self, cell, **cluster):
        c = FakeCluster(self.ft)
        for k, v in cluster.items():
            setattr(c, k, v)
        self.q = batch.Queue.create(self.dir / cell["cell"].replace("/", "-"), REF, [cell], precheck_cfg=PRE)  # fmt: skip
        r = runner_for(c, self.ft)
        # S2/S4's fault is the swarm alone (Locust runs the baseline, so the reset sends none)
        r.http_post = lambda url, f: setattr(c, "injected", True) if url.endswith("/swarm") else None  # fmt: skip
        live = batch.LiveRun(r, PRE, wait_s=50, poll_s=5, sleep=self.ft.sleep,
                             monotonic=self.ft.monotonic, wall=self.ft.wall, stat=audit_fresh(self.ft))  # fmt: skip
        self.drive(live, max_runs=1)
        fr = [a for a in c.calls if "flight-recorder-db-0" in a]
        return self.q.runs[0], fr

    def test_z_3_5_voids_the_run_and_the_cell_requeues_it(self):
        r, fr = self.run_cell(S6, bundle_z=3.5)
        self.assertEqual((r["status"], r["void_reason"]), ("void", "s6_latency_p95_z_above_3"))  # fmt: skip
        self.assertEqual(
            r["s6_void_check"],
            {"bundle_hash": "sha256:" + "c" * 64, "z": 3.5, "why": None, "void_reason": "s6_latency_p95_z_above_3"},
        )  # fmt: skip
        st = self.q.cell_state(S6["cell"])
        self.assertEqual((st["status"], st["void"], st["valid"]), ("open", 1, 0))
        # re-run, on the attempt cap
        self.assertEqual(self.q.next_cell()["cell"], S6["cell"])
        # read-only, by the counted Incident's bundle hash, as fr-export.sh reads the recorder
        self.assertEqual(len(fr), 1)
        self.assertEqual(
            fr[0][:7],
            ["kubectl", "--context", "ctx", "-n", "nexus-system", "exec", "flight-recorder-db-0"],
        )  # fmt: skip
        self.assertEqual(fr[0][fr[0].index("-U") + 1], "nexus_reader")
        self.assertIn("sha256:" + "c" * 64, fr[0][-1])
        # the void reaches the export and the metrics as a reported, unscored attempt
        (x,) = self.q.export_runs()
        self.assertEqual((x["discarded"], x["discard_reason"]), (True, "s6_latency_p95_z_above_3"))  # fmt: skip
        self.assertEqual(
            metrics.discards([x])["R1/S6"],
            {"valid": 0, "discarded": ["s6_latency_p95_z_above_3"]},
        )

    def test_z_2_9_is_valid(self):
        r, _ = self.run_cell(S6, bundle_z=2.9)
        self.assertEqual((r["status"], r["void_reason"]), ("valid", None))
        self.assertEqual((r["s6_void_check"]["z"], r["s6_void_check"]["void_reason"]), (2.9, None))  # fmt: skip
        self.assertEqual(self.q.cell_state(S6["cell"])["status"], "done")

    def test_z_exactly_3_is_valid(self):
        r, _ = self.run_cell(S6, bundle_z=3)
        self.assertEqual(r["status"], "valid")

    def test_a_missing_z_is_reported_and_voids_never_silently_valid(self):
        cases = {
            # the bundle's Z is null (no Prometheus answer)
            "z_missing": {"bundle_z": None},
            "no_bundle_row": {"bundle_z": ABSENT},
            "recorder_unreadable": {"bundle_z": FAIL},
            "no_bundle_hash": {"bundle_hash": None},  # status.evidence never written
            "z_not_a_number": {"bundle_z": "3.5"},
        }
        for why, cluster in cases.items():
            with self.subTest(why):
                r, _ = self.run_cell({**S6, "cell": f"S6-{why}"}, **cluster)
                self.assertEqual((r["status"], r["void_reason"]), ("void", "s6_latency_p95_z_unknown"))  # fmt: skip
                self.assertEqual(r["s6_void_check"]["why"], why)

    def test_other_scenarios_are_never_voided_and_never_read(self):
        for cell in (CELLS[0], {**CELLS[1], "users": 160, "spawn_rate": 4}):  # S1, S4
            with self.subTest(cell["scenario"]):
                r, fr = self.run_cell(cell, bundle_z=3.5)
                self.assertEqual((r["status"], r["void_reason"]), ("valid", None))
                self.assertNotIn("s6_void_check", r)
                self.assertEqual(fr, [])


if __name__ == "__main__":
    unittest.main()
