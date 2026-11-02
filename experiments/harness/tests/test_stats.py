"""Offline tests for stats.py against hand-computed values."""

import unittest

from .. import stats


class StatsTest(unittest.TestCase):
    def test_wilson_ten_of_ten_lower_bound(self):
        lo, hi = stats.wilson(10, 10)
        self.assertAlmostEqual(lo, 0.722, places=3)  # spec: "about 0.72"
        self.assertAlmostEqual(hi, 1.0, places=6)

    def test_wilson_zero_and_empty(self):
        lo, hi = stats.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.278, places=3)
        self.assertIsNone(stats.wilson(0, 0))
        with self.assertRaises(ValueError):
            stats.wilson(11, 10)

    def test_rule_of_three(self):
        self.assertAlmostEqual(stats.rule_of_three(120), 0.025)
        self.assertIsNone(stats.rule_of_three(0))

    def test_median_iqr(self):
        r = stats.median_iqr([1, 2, 3, 4, 100])
        self.assertEqual((r["median"], r["q1"], r["q3"], r["n"]), (3, 2, 4, 5))
        self.assertNotIn("mean", r)
        self.assertIsNone(stats.median_iqr([]))


if __name__ == "__main__":
    unittest.main()
