"""The committed ADR-039 R1 regression (experiments/regression/adr-039) is current and clean."""

import json
import unittest

from experiments.harness import r1_regression as rr


class R1RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = json.loads((rr.OUT_DIR / "result.json").read_text())
        cls.rows = {r["label"]: r for r in cls.res["rows"]}

    def test_committed_result_is_current_for_the_current_rules(self):
        self.assertEqual(rr.check(rr.OUT_DIR), [])

    def test_no_change_outside_s1_and_the_base_reproduces_the_recorder(self):
        s = self.res["summary"]
        self.assertEqual((s["changed_outside_s1"], s["recorded_mismatches"]), (0, 0))
        fr = [r for r in self.res["rows"] if r["source"] == "flight recorder"]
        self.assertEqual(len(fr), 8)
        self.assertTrue(all(r["base"] == r["recorded"] for r in fr))

    def test_the_named_rows(self):
        def row(prefix):
            [r] = [r for label, r in self.rows.items() if label.startswith(prefix)]
            return r["base"], r["new"]

        restart, escalate = "gray_failure/restart_workload", "unknown/escalate"
        self.assertEqual(row("nexus-prod Incident 5fa4f476"), (escalate, restart))
        self.assertEqual(row("nexus-dev 2026-11-07T07:50:12Z"), (escalate, escalate))
        self.assertEqual(row("nexus-prod Incident 49fe844f"), (escalate, escalate))
        self.assertEqual(row("q27 dependency timing out (ready"), (restart, restart))
        s4 = [r for r in self.res["rows"] if r["source"] == "S4 sweep"]
        self.assertEqual(len(s4), 15)
        self.assertTrue(
            all(r["base"] == r["new"] == "traffic_spike/no_action" for r in s4)
        )


if __name__ == "__main__":
    unittest.main()
