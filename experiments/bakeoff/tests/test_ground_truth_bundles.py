"""experiments/ground-truth.json agrees with the bake-off's corrected truths (bundles.yaml, owner,
2026-11-20) on S1, S3, S5 and S6. Offline; PyYAML reads bundles.yaml, as in test_bakeoff.py.

For each real bundle the action is the same, the accepted recommendation kinds are the same set,
and the bundle's accepted fault classes are the ones the ground truth accepts for that Incident:
its `fault_class`, plus an alternative whose `when` names the Incident's alert. S6 has two real
bundles of one run (the traffic Incident and the CPU one); only the first Incident of a run counts,
so the S6 truth must hold for each.
"""

import json
import pathlib
import unittest

from experiments.bakeoff import bakeoff as bo

ROOT = pathlib.Path(__file__).resolve().parents[3]
TRUTH = json.loads((ROOT / "experiments/ground-truth.json").read_text(encoding="utf-8"))
BUNDLES = {
    b.scenario: b
    for b in bo.parse_bundles(bo._yaml(ROOT / "experiments/bakeoff/bundles.yaml"))
}
# bundle -> (ground-truth scenario, the alert that named its Incident): the trial-run records under
# ~/nexus-evidence (S3, S5, S6) and docs/demo/manual-scenarios.md S1 (the 2026-11-19 R3 run)
REAL = {
    "S1": ("S1", "NexusLatencyAnomaly"),
    "S3": ("S3", "NexusRolloutStuck"),
    "S5": ("S5", "NexusErrorRateAnomaly"),
    "S6": ("S6", "NexusTrafficAnomaly"),
    "S6-cpu": ("S6", "NexusCpuAnomaly"),
}


def accepted_faults(sc: dict, alert: str) -> set:
    return {sc["fault_class"]} | {
        a["value"] for a in sc["fault_class_alternatives"] if alert in a["when"]
    }


def accepted_kinds(sc: dict) -> set:
    rec = sc["correct_action"].get("recommendation")
    return set() if rec is None else set(rec if isinstance(rec, list) else [rec])


class AgreementTest(unittest.TestCase):
    def test_the_real_bundles_are_the_ones_compared(self):
        self.assertLessEqual(set(REAL), set(BUNDLES))
        self.assertEqual({b for b, v in BUNDLES.items() if v.set == "real"}, set(REAL))

    def test_each_real_bundle_agrees(self):
        for name, (scen, alert) in REAL.items():
            with self.subTest(name):
                t, sc = BUNDLES[name].truth, TRUTH["scenarios"][scen]
                self.assertEqual(t.action, sc["correct_action"]["action"])
                self.assertEqual(set(bo.values(t.kind)), accepted_kinds(sc))
                self.assertEqual(
                    set(bo.values(t.fault_class)), accepted_faults(sc, alert)
                )
                # the class metrics.diagnosis_accuracy scores is accepted by the bundle too
                self.assertIn(sc["fault_class"], bo.values(t.fault_class))

    def test_s6_holds_for_both_incidents_of_the_run(self):
        sc = TRUTH["scenarios"]["S6"]
        for name in ("S6", "S6-cpu"):
            t = BUNDLES[name].truth
            self.assertTrue(t.injected)
            self.assertEqual(
                (t.action, sc["correct_action"]["action"]), ("no_action", "no_action")
            )
        union = set(bo.values(BUNDLES["S6"].truth.fault_class)) | set(
            bo.values(BUNDLES["S6-cpu"].truth.fault_class)
        )
        self.assertEqual(
            union,
            {sc["fault_class"]} | {a["value"] for a in sc["fault_class_alternatives"]},
        )


if __name__ == "__main__":
    unittest.main()
