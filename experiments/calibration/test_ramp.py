"""Offline tests for ramp.py (M1b-9; ADR-026). Standard library only:
python3 -m unittest discover -s experiments/calibration -p 'test_*.py'
No network: run() talks to a fake Locust and a fake Prometheus."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import ramp

FIXTURES = Path(__file__).parent / "fixtures"


def verdict(name, **kw):
    return ramp.knee(ramp.read_steps(FIXTURES / name), **kw)


class KneeTest(unittest.TestCase):
    def test_no_knee_reaches_max(self):
        v = verdict("no-knee.csv")
        self.assertEqual(
            (v["capacity"], v["knee_target"], v["reached_max"]), (30, None, True)
        )
        self.assertEqual(v["baseline"], 12)

    def test_p95_knee(self):
        v = verdict("knee-p95.csv")
        self.assertEqual((v["capacity"], v["knee_target"]), (30, 40))
        self.assertTrue(any(r.startswith("p95 30") for r in v["reasons"]), v["reasons"])

    def test_any_items_failure_is_a_knee_even_below_one_percent(self):
        v = verdict("knee-items.csv")
        self.assertEqual((v["capacity"], v["knee_target"]), (20, 30))
        self.assertEqual(v["reasons"], ["/items failures 3"])

    def test_throttle_knee(self):
        v = verdict("knee-throttle.csv")
        self.assertEqual((v["capacity"], v["knee_target"]), (10, 20))
        self.assertIn("sample-api throttled 15.00% > 10%", v["reasons"])

    def test_achieved_knee(self):
        v = verdict("knee-achieved.csv")
        self.assertEqual((v["capacity"], v["knee_target"]), (10, 20))

    def test_knee_at_first_step_has_no_capacity(self):
        v = verdict("knee-first.csv")
        self.assertEqual(
            (v["capacity"], v["baseline"], v["knee_target"]), (None, None, 10)
        )

    def test_without_prometheus_columns_throttle_is_not_required(self):
        v = verdict("no-prom.csv")
        self.assertEqual((v["capacity"], v["reached_max"]), (20, True))

    def test_missing_throttle_answer_stops_when_prometheus_is_used(self):
        v = verdict("knee-missing.csv")
        self.assertEqual((v["capacity"], v["knee_target"]), (10, 20))
        self.assertEqual(v["reasons"], ["sample-api throttling: no data"])

    def test_thresholds_are_options(self):
        v = verdict("knee-throttle.csv", throttle_max=0.20)
        self.assertEqual(v["knee_target"], None)

    def test_db_throttling_is_recorded_not_a_knee(self):
        # 30 % DB throttling at every step (the idle reading): the decision is the owner's.
        self.assertTrue(verdict("no-knee.csv")["reached_max"])

    def test_baseline_is_floor_of_forty_percent(self):
        self.assertEqual(
            [ramp.baseline(c) for c in (10, 25, 30, 100, 101)], [4, 10, 12, 40, 40]
        )


class FakeHttp:
    """Locust: each step's stats come from `plan` (target -> (requests, failures, items_failures,
    p95)). Prometheus: `prom` maps a query-name substring to a value (None = empty answer)."""

    def __init__(self, plan, prom=None):
        self.plan, self.prom, self.users, self.calls = plan, prom or {}, 0, []

    def post_form(self, url, fields, timeout=30):
        self.calls.append(("POST", url, dict(fields)))
        self.users = fields["user_count"]
        return {"success": True}

    def get(self, url, timeout=30):
        self.calls.append(("GET", url, None))
        assert url.endswith("/stats/reset"), url
        return b"ok"

    def get_json(self, url, timeout=30):
        self.calls.append(("GET", url, None))
        if url.endswith("/stats/requests"):
            req, fail, items_fail, p95 = self.plan[self.users]
            return {
                "stats": [
                    {
                        "name": "dev:/items",
                        "num_requests": req // 5,
                        "num_failures": items_fail,
                    },
                    {
                        "name": "Aggregated",
                        "num_requests": req,
                        "num_failures": fail,
                        "response_time_percentile_0.95": p95,
                    },
                ]
            }
        if "/api/v1/query" in url:
            for key, value in self.prom.items():
                if key in url:
                    if value is None:
                        return {"status": "success", "data": {"result": []}}
                    return {
                        "status": "success",
                        "data": {"result": [{"value": [0, str(value)]}]},
                    }
            return {"status": "success", "data": {"result": [{"value": [0, "0"]}]}}
        return {}


def args(out, prom=None, **kw):
    base = {
        "locust": "http://locust:8089/",
        "out": out,
        "prom": prom,
        "namespace": "nexus-dev",
        "classes": ["DevUser"],
        "start": 10,
        "step": 10,
        "max": 50,
        "settle": 60,
        "measure": 120,
        "spawn_rate": 5,
        "p95_factor": 2.0,
        "fail_max": 0.01,
        "achieved_min": 0.95,
        "throttle_max": 0.10,
        "rate_diff_max": 0.05,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


class RunTest(unittest.TestCase):
    def run_ramp(self, http, **kw):
        clock = Clock()
        with tempfile.TemporaryDirectory() as d:
            v = ramp.run(
                args(d, **kw),
                http=http,
                sleep=clock.sleep,
                now=clock.now,
                log=lambda *_: None,
            )
            rows = ramp.read_steps(Path(d) / "steps.csv")
            saved = json.loads((Path(d) / "verdict.json").read_text())
            raws = sorted(p.name for p in Path(d).glob("step-*.json"))
        return v, rows, saved, raws

    def test_stops_at_knee_and_never_runs_the_next_step(self):
        http = FakeHttp(
            {
                10: (1200, 0, 0, 12),
                20: (2400, 0, 0, 13),
                30: (3600, 0, 0, 40),
                40: (4800, 0, 0, 12),
            }
        )
        v, rows, saved, raws = self.run_ramp(http)
        self.assertEqual((v["capacity"], v["knee_target"], v["baseline"]), (20, 30, 8))
        self.assertEqual([r["target"] for r in rows], ["10", "20", "30"])
        self.assertEqual(saved, v)
        self.assertEqual(raws, ["step-10.json", "step-20.json", "step-30.json"])
        swarms = [c[2] for c in http.calls if c[0] == "POST"]
        self.assertEqual([s["user_count"] for s in swarms], [10, 20, 30])
        self.assertEqual(swarms[0]["user_classes"], ["DevUser"])
        self.assertEqual(http.calls[-1][1], "http://locust:8089/stop")

    def test_reset_precedes_every_measure(self):
        http = FakeHttp(
            {
                10: (1200, 0, 0, 12),
                20: (2400, 0, 0, 12),
                30: (3600, 0, 0, 12),
                40: (4800, 0, 0, 12),
                50: (6000, 0, 0, 12),
            }
        )
        v, rows, _, _ = self.run_ramp(http)
        self.assertTrue(v["reached_max"])
        urls = [c[1] for c in http.calls]
        resets = [i for i, u in enumerate(urls) if u.endswith("/stats/reset")]
        reads = [i for i, u in enumerate(urls) if u.endswith("/stats/requests")]
        self.assertEqual(len(resets), 5)
        self.assertTrue(all(r < s for r, s in zip(resets, reads)))
        self.assertEqual(
            rows[0]["achieved_rps"], "10.000"
        )  # no total_rps: 1200 requests / 120 s

    def test_achieved_rate_is_the_window_average_not_total_rps(self):
        fx = json.loads((FIXTURES / "stats-window.json").read_text())
        row = ramp.locust_row(
            fx, fx["target"], fx["t_start"], fx["t_end"], "dev:/items"
        )
        self.assertEqual(row["achieved_rps"], "9.417")  # 1130 requests / 120 s
        self.assertEqual(row["locust_total_rps"], "10.290")  # display only
        row.update({f: "" for f in ramp.FIELDS if f not in row})
        v = ramp.knee([row])
        self.assertEqual(v["reasons"], ["achieved 9.4 < 95% of 10"])

    def test_rate_cross_check(self):
        self.assertEqual(ramp.rate_check(10.0, 10.4, 0.05), ("0.0400", "ok"))
        self.assertEqual(
            ramp.rate_check(10.0, 9.4, 0.05), ("0.0600", "rate diff 6.0% > 5%")
        )
        self.assertEqual(
            ramp.rate_check(10.0, "", 0.05), ("", "prometheus rate: no data")
        )

    def test_prometheus_columns_and_empty_answers(self):
        http = FakeHttp(
            {10: (1200, 0, 0, 12), 20: (2400, 0, 0, 12)},
            prom={
                "dependency-db": 0.3,
                "sample-api": 0.0,
                "locust-worker": None,
                "nexus_sample_api_requests": 10.2,
            },
        )
        v, rows, _, _ = self.run_ramp(http, prom="http://prom:9090", max=20)
        self.assertEqual(rows[0]["db_throttle"], "0.3")
        # the server-side rate: 10.2 against 10.0 is ok; against 20.0 it is flagged, not a knee
        self.assertEqual((rows[0]["prom_rps"], rows[0]["rate_flag"]), ("10.2", "ok"))
        self.assertEqual(rows[1]["rate_flag"], "rate diff 49.0% > 5%")
        self.assertEqual(v["flags"], ["step 20: rate diff 49.0% > 5%"])
        self.assertEqual(rows[0]["worker_cpu"], "")  # empty answer stays empty, never 0
        self.assertTrue(v["reached_max"])  # DB throttling alone is not a knee
        queries = [c[1] for c in http.calls if "/api/v1/query" in c[1]]
        self.assertEqual(
            len(queries), 2 * (len(ramp.QUERIES) + 1)
        )  # + the rate cross-check
        self.assertIn("120s", queries[0].replace("%5B", "[").replace("%5D", "]"))

    def test_stop_is_posted_even_when_a_step_fails(self):
        http = FakeHttp({})  # /stats/requests raises KeyError
        with self.assertRaises(KeyError):
            self.run_ramp(http)
        self.assertEqual(http.calls[-1][1], "http://locust:8089/stop")


class LocustfileTest(unittest.TestCase):
    """The mix the locustfile sends, read from its source (Locust is not installed in CI)."""

    def test_mix_is_four_to_one_and_never_work_cpu(self):
        src = (Path(__file__).parents[2] / "platform/load/locustfile.py").read_text()
        self.assertIn('SEQUENCE = ("/", "/", "/", "/", "/items")', src)
        self.assertNotIn('"/work/cpu"', src.split('"""', 2)[2])


if __name__ == "__main__":
    unittest.main()
