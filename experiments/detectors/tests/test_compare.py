"""Tests for the detector comparison on the committed FIXTURE runs. Needs scikit-learn (analysis-only,
pinned with hashes in experiments/detectors/requirements.txt); skipped where it is not installed.

Expected values are regression values of invented fixture data, with the Z-score ones worked out by
hand: a fault ramps in over 60 s, so the first true evaluation is 30 s after the injection, and `for: 1m`
needs three evaluations, so the alert fires 90 s after the injection."""

import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from .. import compare, iforest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "experiments" / "analysis" / "sample"
HAS_SKLEARN = importlib.util.find_spec("sklearn") is not None
MARK = "FIXTURE — not measured"


def build(out, runs=None, **kw):
    return compare.build(
        runs or SAMPLE / "runs", SAMPLE / "ground-truth.json", out, **kw
    )


class PersistTest(unittest.TestCase):
    def test_three_consecutive_flagged_points_fire_and_a_gap_resets(self):
        flags = {float(i * 30): f for i, f in enumerate([0, 1, 1, 0, 1, 1, 1, 0])}
        fired = iforest.persist({t: bool(f) for t, f in flags.items()})
        self.assertEqual(
            [t for t, f in fired.items() if f], [180.0]
        )  # points 4, 5, 6 -> fires at the third

    def test_a_missing_point_counts_as_not_flagged(self):
        self.assertFalse(
            any(iforest.persist({0.0: True, 30.0: True, 90.0: True}).values())
        )


@unittest.skipUnless(
    HAS_SKLEARN,
    "scikit-learn is analysis-only: install experiments/detectors/requirements.txt",
)
class ForestTest(unittest.TestCase):
    def test_flags_an_outlier_and_about_the_quantile_of_the_baseline(self):
        base = [
            [
                0.3 + 0.001 * (i % 7),
                0.05,
                2.0 + 0.01 * (i % 5),
                0.002,
                80.0 + 0.1 * (i % 11),
            ]
            for i in range(300)
        ]
        f = iforest.Detector().fit(base)
        self.assertEqual(f.flags([[0.9, 0.5, 40.0, 0.4, 80.0]]), [True])
        share = sum(f.flags(base)) / len(base)
        self.assertLessEqual(
            share, 0.03
        )  # the threshold is the 2 % quantile of the training scores

    def test_same_seed_same_scores_other_seed_may_differ(self):
        base = [
            [0.3 + 0.001 * (i % 7), 0.05, 2.0, 0.002, 80.0 + 0.1 * (i % 11)]
            for i in range(200)
        ]
        a = iforest.Detector().fit(base).scores(base[:5])
        b = iforest.Detector().fit(base).scores(base[:5])
        self.assertEqual(a, b)

    def test_needs_baseline_points(self):
        with self.assertRaises(ValueError):
            iforest.Detector().fit([[1, 2, 3, 4, 5]])

    def test_the_proposed_parameters(self):
        self.assertEqual(
            iforest.PARAMS,
            {
                "n_estimators": 200,
                "max_samples": 256,
                "max_features": 1.0,
                "bootstrap": False,
                "contamination": "auto",
                "random_state": 1,
                "n_jobs": 1,
            },
        )
        self.assertEqual(iforest.THRESHOLD_QUANTILE, 0.02)


@unittest.skipUnless(
    HAS_SKLEARN,
    "scikit-learn is analysis-only: install experiments/detectors/requirements.txt",
)
class CompareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = pathlib.Path(cls._tmp.name)
        cls.res = build(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_temporal_split_earlier_half_trains_later_half_tests(self):
        sp = self.res["split"]
        self.assertEqual((len(sp["train_runs"]), len(sp["test_runs"])), (12, 12))
        self.assertFalse(set(sp["train_runs"]) & set(sp["test_runs"]))
        self.assertTrue(all(r.startswith("A/R1/") for r in sp["train_runs"]))
        self.assertTrue(all(r.startswith("A/R3/") for r in sp["test_runs"]))
        self.assertNotIn(
            "A/R3/S1/L3#3", sp["train_runs"] + sp["test_runs"]
        )  # it does not reconcile: not counted, not used

    def test_the_forest_is_fitted_on_pre_injection_points_of_training_runs_only(self):
        self.assertEqual(
            self.res["forest"]["training_points"], 12 * (40 + 10)
        )  # 20 min of history + the 5 min no-fault window

    def test_zscore_delay_and_recall_by_hand(self):
        for s in ("S1", "S2", "S4", "S5", "S6"):
            z = self.res["per_scenario"][s]["zscore"]
            self.assertEqual((z["detected"]["k"], z["detected"]["n"]), (2, 2), s)
            self.assertEqual(z["delay_s"]["median"], 90.0, s)
        z3 = self.res["per_scenario"]["S3"]["zscore"]
        self.assertEqual(
            (z3["detected"]["k"], z3["f1"]), (0, None)
        )  # a readiness failure moves none of the five signals
        self.assertEqual(self.res["per_scenario"]["S3"]["iforest"]["detected"]["k"], 0)

    def test_false_positives_in_the_no_fault_windows(self):
        s4 = self.res["per_scenario"]["S4"]["zscore"]
        self.assertEqual(
            (s4["false_positive_windows"], s4["false_alerts"]), (1, 1)
        )  # the four-point traffic blip
        self.assertEqual((s4["precision"]["k"], s4["precision"]["n"]), (2, 3))
        for s, by in self.res["per_scenario"].items():
            if s != "S4":
                self.assertEqual(by["zscore"]["false_positive_windows"], 0, s)
        # the slow latency drift in the S2 no-fault window is followed by the adaptive baseline
        self.assertEqual(
            self.res["per_scenario"]["S2"]["zscore"]["false_positive_windows"], 0
        )

    def test_both_detectors_are_scored_on_the_same_windows(self):
        for s, by in self.res["per_scenario"].items():
            self.assertEqual(
                by["zscore"]["fault_windows"], by["iforest"]["fault_windows"], s
            )
            self.assertEqual(
                by["zscore"]["no_fault_windows"], by["iforest"]["no_fault_windows"], s
            )
        self.assertEqual(set(self.res["per_run"]), set(self.res["split"]["test_runs"]))

    def test_precision_recall_f1_are_the_spec_formulas(self):
        z = self.res["per_scenario"]["S4"]["zscore"]
        p, r = 2 / 3, 1.0
        self.assertAlmostEqual(z["f1"], 2 * p * r / (p + r))

    def test_sensitivity_includes_the_proposed_value_and_agrees_with_d1(self):
        sweep = self.res["sensitivity"]
        for s, by in sweep.items():
            self.assertIn(str(iforest.THRESHOLD_QUANTILE), by)
            self.assertEqual(
                by[str(iforest.THRESHOLD_QUANTILE)]["detected"],
                self.res["per_scenario"][s]["iforest"]["detected"]["k"],
            )
            self.assertEqual(set(by), {str(q) for q in iforest.SWEEP})

    def test_output_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            build(pathlib.Path(d))
            for f in sorted(self.out.iterdir()):
                self.assertEqual(
                    f.read_bytes(), (pathlib.Path(d) / f.name).read_bytes(), f.name
                )

    def test_the_seed_is_recorded_and_fixed(self):
        self.assertEqual(self.res["forest"]["params"]["random_state"], 1)
        with tempfile.TemporaryDirectory() as d:
            r2 = build(pathlib.Path(d), seed=7)
            self.assertEqual(r2["forest"]["params"]["random_state"], 7)

    def test_fixture_marker_on_every_output(self):
        for t in ("D1", "D2", "D3", "D4"):
            self.assertEqual(
                (self.out / f"{t}.csv").read_text().splitlines()[0], f"# {MARK}", t
            )
        md = (self.out / "detectors.md").read_text()
        self.assertEqual(md.count(f"> **{MARK}**"), 1 + md.count("\n### "))
        texts = [
            e.text
            for e in ET.fromstring((self.out / "F4.svg").read_text()).iter()
            if e.tag.endswith("text")
        ]
        self.assertIn(MARK, texts)
        self.assertTrue(
            json.loads((self.out / "detectors.json").read_text())["fixture"]
        )

    def test_measured_runs_carry_no_marker_and_mixing_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            shutil.copytree(SAMPLE / "runs", d / "runs")
            for m in (d / "runs").glob("*/MANIFEST.json"):
                doc = json.loads(m.read_text())
                doc["fixture"] = False
                m.write_text(json.dumps(doc))
            r = build(d / "out", runs=d / "runs")
            self.assertFalse(r["fixture"])
            self.assertFalse((d / "out" / "D1.csv").read_text().startswith("#"))
            self.assertNotIn(
                "FIXTURE",
                (d / "out" / "detectors.md").read_text()
                + (d / "out" / "F4.svg").read_text(),
            )
            doc = json.loads(
                (d / "runs" / "A-R1-S1-L3-1" / "MANIFEST.json").read_text()
            )
            doc["fixture"] = True
            (d / "runs" / "A-R1-S1-L3-1" / "MANIFEST.json").write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                build(d / "o2", runs=d / "runs")

    def test_a_run_whose_files_changed_is_not_used(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            shutil.copytree(SAMPLE / "runs", d / "runs")
            p = d / "runs" / "A-R3-S2-L3-1" / "metrics.json"
            p.write_text(p.read_text().replace("0.3", "0.9", 1))
            r = build(d / "out", runs=d / "runs")
            self.assertNotIn(
                "A/R3/S2/L3#1", r["split"]["train_runs"] + r["split"]["test_runs"]
            )

    def test_command_line_exit_codes(self):
        argv = [
            "--runs",
            str(SAMPLE / "runs"),
            "--truth",
            str(SAMPLE / "ground-truth.json"),
        ]
        with (
            tempfile.TemporaryDirectory() as d,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(compare.main([*argv, "--out", d]), 0)
        with (
            tempfile.TemporaryDirectory() as d,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                compare.main(["--runs", d + "/nope", "--truth", argv[3], "--out", d]), 2
            )
            with mock.patch.object(
                iforest, "_sklearn", side_effect=iforest.MissingDependency("no sklearn")
            ):
                self.assertEqual(compare.main([*argv, "--out", d]), 2)


if __name__ == "__main__":
    unittest.main()
