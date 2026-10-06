"""Offline tests for ramp.py (M1b-9; ADR-026). Standard library only:
python3 -m unittest discover -s experiments/calibration -p 'test_*.py'
No network: run() talks to a fake Locust and a fake Prometheus."""

import argparse
import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
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

    def test_server_rate_knee_even_when_locust_is_at_target(self):
        v = verdict("knee-achieved.csv")
        self.assertEqual((v["capacity"], v["knee_target"]), (10, 20))
        self.assertEqual(v["reasons"], ["server rate 18.3 < 95% of 20"])
        self.assertEqual(v["flags"], ["step 20: rate diff +9.3% > 5%"])

    def test_locust_four_percent_low_prometheus_at_target_is_no_knee(self):
        v = verdict("locust-low.csv")
        self.assertEqual(
            (v["capacity"], v["knee_target"], v["reached_max"]), (30, None, True)
        )
        self.assertEqual(
            v["flags"], []
        )  # -4 % is the expected bias: recorded, not acted on

    def test_no_prometheus_answer_stops_the_ramp_unjudged(self):
        v = verdict("no-prom.csv")
        self.assertEqual(
            (v["capacity"], v["knee_target"], v["unjudged_target"], v["last_ok"]),
            (None, None, 20, 10),
        )
        self.assertEqual(
            v["reasons"], ["server-side rate: no data, step cannot be judged"]
        )
        self.assertTrue(ramp.stopped(v))

    def test_knee_at_first_step_has_no_capacity(self):
        v = verdict("knee-first.csv")
        self.assertEqual(
            (v["capacity"], v["baseline"], v["knee_target"]), (None, None, 10)
        )

    def test_missing_throttle_answer_is_a_knee(self):
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


class CrossCheckTest(unittest.TestCase):
    def test_locust_window_average_and_total_rps(self):
        fx = json.loads((FIXTURES / "stats-window.json").read_text())
        row = ramp.locust_row(
            fx, fx["target"], fx["t_start"], fx["t_end"], "dev:/items"
        )
        self.assertEqual(row["locust_rps"], "9.417")  # 1130 requests / 120 s
        self.assertEqual(row["locust_total_rps"], "10.290")  # display only
        self.assertNotIn("achieved_rps", row)  # the knee's rate is server-side

    def test_rate_check_is_signed_and_flags_above_five_percent(self):
        self.assertEqual(ramp.rate_check(9.6, 10.0, 0.05), ("-0.0400", "ok"))
        self.assertEqual(ramp.rate_check(10.4, 10.0, 0.05), ("+0.0400", "ok"))
        self.assertEqual(
            ramp.rate_check(9.417, 10.0, 0.05), ("-0.0583", "rate diff -5.8% > 5%")
        )
        self.assertEqual(ramp.rate_check(10.0, "", 0.05), ("", ""))


class FakeHttp:
    """Locust: each step's stats come from `plan` (target -> (requests, failures,
    items_failures, p95)). Prometheus: the server-side rate is `server` (target -> value,
    None = empty, "down" = no answer), defaulting to requests / 120; `prom` maps a
    query substring to a value (None = empty answer) for the other queries."""

    def __init__(self, plan, prom=None, server=None):
        self.plan, self.prom, self.server = plan, prom or {}, server or {}
        self.users, self.calls = 0, []

    def post_form(self, url, fields, timeout=30):
        self.calls.append(("POST", url, dict(fields)))
        self.users = fields["user_count"]
        return {"success": True}

    def get(self, url, timeout=30):
        self.calls.append(("GET", url, None))
        assert url.endswith("/stats/reset"), url
        return b"ok"

    @staticmethod
    def answer(value):
        if value is None:
            return {"status": "success", "data": {"result": []}}
        return {"status": "success", "data": {"result": [{"value": [0, str(value)]}]}}

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
            if "http_requests_total" in url:
                value = self.server.get(self.users, self.plan[self.users][0] / 120)
                if value == "down":
                    raise urllib.error.URLError("connection refused")
                return self.answer(value)
            for key, value in self.prom.items():
                if key in url:
                    return self.answer(value)
            return self.answer(0)
        return {}


def args(out, **kw):
    base = {
        "locust": "http://locust:8089/",
        "out": out,
        "prom": "http://prom:9090",
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


STEADY = {t: (120 * t, 0, 0, 12) for t in (10, 20, 30, 40, 50)}


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
        http = FakeHttp({**STEADY, 30: (3600, 0, 0, 40)})
        v, rows, saved, raws = self.run_ramp(http)
        self.assertEqual((v["capacity"], v["knee_target"], v["baseline"]), (20, 30, 8))
        self.assertEqual([r["target"] for r in rows], ["10", "20", "30"])
        self.assertEqual(saved, v)
        self.assertEqual(raws, ["step-10.json", "step-20.json", "step-30.json"])
        swarms = [c[2] for c in http.calls if c[0] == "POST"]
        self.assertEqual([s["user_count"] for s in swarms], [10, 20, 30])
        self.assertEqual(swarms[0]["user_classes"], ["DevUser"])
        self.assertEqual(http.calls[-1][1], "http://locust:8089/stop")

    def test_reset_follows_settle_and_precedes_every_measure(self):
        http = FakeHttp(STEADY)
        v, rows, _, _ = self.run_ramp(http)
        self.assertTrue(v["reached_max"])
        urls = [c[1] for c in http.calls]
        swarms = [i for i, c in enumerate(http.calls) if c[0] == "POST"]
        resets = [i for i, u in enumerate(urls) if u.endswith("/stats/reset")]
        reads = [i for i, u in enumerate(urls) if u.endswith("/stats/requests")]
        self.assertEqual(len(resets), 5)
        self.assertTrue(all(a < r < s for a, r, s in zip(swarms, resets, reads)))
        self.assertEqual(
            (rows[0]["achieved_rps"], rows[0]["locust_rps"]), ("10.0", "10.000")
        )

    def test_server_rate_over_exactly_the_window_at_its_end(self):
        http = FakeHttp(STEADY)
        self.run_ramp(http, max=10)
        server = [c[1] for c in http.calls if "http_requests_total" in c[1]]
        self.assertEqual(len(server), 1)
        q = urllib.parse.unquote(server[0])
        self.assertIn('namespace="nexus-dev"', q)
        self.assertIn('handler!~"/health|/ready|/metrics"}[120s]', q)
        self.assertIn("time=1180.000", q)  # 1000 + 60 settle + 120 measure

    def test_locust_four_percent_low_prometheus_at_target_no_knee(self):
        low = {t: (round(120 * t * 0.96), 0, 0, 12) for t in (10, 20, 30)}
        http = FakeHttp(low, server={10: 10.0, 20: 20.0, 30: 30.0})
        v, rows, _, _ = self.run_ramp(http, max=30)
        self.assertEqual((v["capacity"], v["reached_max"], v["flags"]), (30, True, []))
        self.assertEqual(
            [(r["achieved_rps"], r["locust_rps"], r["rate_diff"]) for r in rows],
            [
                ("10.0", "9.600", "-0.0400"),
                ("20.0", "19.200", "-0.0400"),
                ("30.0", "28.800", "-0.0400"),
            ],
        )

    def test_server_rate_low_is_a_knee_while_locust_is_at_target(self):
        http = FakeHttp(STEADY, server={20: 18.0})
        v, rows, _, _ = self.run_ramp(http)
        self.assertEqual((v["capacity"], v["knee_target"]), (10, 20))
        self.assertIn("server rate 18.0 < 95% of 20", v["reasons"])
        self.assertEqual(rows[1]["rate_flag"], "rate diff +11.1% > 5%")

    def test_prometheus_down_stops_the_ramp(self):
        http = FakeHttp(STEADY, server={20: "down"})
        v, rows, _, _ = self.run_ramp(http)
        self.assertEqual(
            (v["capacity"], v["unjudged_target"], v["last_ok"]), (None, 20, 10)
        )
        self.assertEqual([r["target"] for r in rows], ["10", "20"])
        self.assertEqual(http.calls[-1][1], "http://locust:8089/stop")

    def test_prometheus_empty_answer_stops_the_ramp(self):
        v, rows, _, _ = self.run_ramp(FakeHttp(STEADY, server={10: None}))
        self.assertEqual((v["capacity"], v["unjudged_target"]), (None, 10))
        self.assertEqual(len(rows), 1)

    def test_resource_columns_and_empty_answers(self):
        http = FakeHttp(
            STEADY,
            prom={"dependency-db": 0.3, "sample-api": 0.0, "locust-worker": None},
        )
        v, rows, _, _ = self.run_ramp(http, max=20)
        self.assertEqual(rows[0]["db_throttle"], "0.3")
        self.assertEqual(rows[0]["worker_cpu"], "")  # empty answer stays empty, never 0
        self.assertTrue(v["reached_max"])  # DB throttling alone is not a knee
        queries = [c[1] for c in http.calls if "/api/v1/query" in c[1]]
        self.assertEqual(len(queries), 2 * (len(ramp.QUERIES) + 1))

    def test_stop_is_posted_even_when_a_step_fails(self):
        http = FakeHttp({})  # /stats/requests raises KeyError
        with self.assertRaises(KeyError):
            self.run_ramp(http)
        self.assertEqual(http.calls[-1][1], "http://locust:8089/stop")

    def test_prom_is_required(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ramp.parser().parse_args(["run", "--locust", "http://l", "--out", "/tmp/x"])


class LocustfileTest(unittest.TestCase):
    """The mix the locustfile sends, read from its source (Locust is not installed in CI)."""

    def test_mix_is_four_to_one_and_never_work_cpu(self):
        src = (Path(__file__).parents[2] / "platform/load/locustfile.py").read_text()
        self.assertIn('SEQUENCE = ("/", "/", "/", "/", "/items")', src)
        self.assertNotIn('"/work/cpu"', src.split('"""', 2)[2])


if __name__ == "__main__":
    unittest.main()
