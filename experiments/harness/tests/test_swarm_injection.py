"""Offline tests for the load injection of S2, S4 and S6: the /swarm form carries the spawn rate,
and a swarm that Locust refuses (HTTP 200, success false) is a failure, never a run to wait for."""

import io
import json
import re
import unittest
import urllib.parse

from .. import batch, runner
from . import test_batch as tb


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener(answer, seen=None):
    """urlopen that records the request and answers `answer` (a dict is sent as JSON)."""

    def open_(request, timeout=None):
        if seen is not None:
            seen.append((request.full_url, request.data.decode(), request.get_method()))
        body = answer if isinstance(answer, str) else json.dumps(answer)
        return FakeResponse(body.encode())

    return open_


class PlanTest(unittest.TestCase):
    def runner(self):
        return runner.Runner(
            "ctx", exec_=lambda argv, cwd=None: (0, "sample-api-a"), locust="http://l"
        )

    def form(self, step):
        return urllib.parse.parse_qs(step.argv[2])

    def test_every_load_scenario_sends_user_count_and_spawn_rate(self):
        for sc in ("S2", "S4", "S6"):
            step = self.runner().plan_inject(sc, users=300, spawn_rate=5)[0]
            self.assertEqual(step.argv[:2], ("POST", "http://l/swarm"))
            self.assertEqual(
                self.form(step), {"user_count": ["300"], "spawn_rate": ["5"]}, sc
            )

    def test_a_fractional_spawn_rate_is_sent_as_given(self):
        step = self.runner().plan_inject("S2", users=300, spawn_rate=2.5)[0]
        self.assertEqual(self.form(step)["spawn_rate"], ["2.5"])

    def test_a_load_scenario_without_a_usable_spawn_rate_is_refused(self):
        for bad in (None, 0, -1, True, "4", float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.runner().plan_inject("S4", users=300, spawn_rate=bad)

    def test_the_command_line_takes_the_spawn_rate(self):
        plan = io.StringIO()
        argv = ["plan", "inject", "S4", "--context", "c", "--level", "3", "--locust", "http://l", "--users", "240", "--spawn-rate", "4"]  # fmt: skip
        import contextlib
        from unittest import mock

        # the level check reads the namespace label first (read-only); the label says 3
        with (
            mock.patch.object(
                runner, "_subprocess_exec", lambda argv, cwd=None: (0, "3")
            ),
            contextlib.redirect_stdout(plan),
        ):
            self.assertEqual(runner.main(argv), 0)
        step = json.loads(plan.getvalue())[0]
        self.assertEqual(step["argv"][2], "user_count=240&spawn_rate=4.0")

    def test_other_scenarios_take_no_spawn_rate(self):
        self.assertEqual(len(self.runner().plan_inject("S1")), 1)


class PostFormTest(unittest.TestCase):
    def test_an_accepted_swarm_passes_and_sends_the_form(self):
        seen = []
        runner._post_form(
            "http://l/swarm",
            {"user_count": ["300"], "spawn_rate": ["5"]},
            opener=opener({"success": True, "message": "Swarming started"}, seen),
        )
        self.assertEqual(
            seen, [("http://l/swarm", "user_count=300&spawn_rate=5", "POST")]
        )

    def test_a_refused_swarm_raises_with_locusts_message(self):
        answer = {"success": False, "message": "spawn_rate is required"}
        with self.assertRaises(RuntimeError) as cm:
            runner._post_form(
                "http://l/swarm", {"user_count": ["300"]}, opener=opener(answer)
            )
        self.assertIn("spawn_rate is required", str(cm.exception))

    def test_an_answer_that_is_not_a_refusal_passes(self):
        for answer in (
            "ok",
            "",
            {"message": "Test stopped"},
            [1, 2],
            {"success": True},
        ):
            runner._post_form("http://l/stop", {}, opener=opener(answer))

    def test_a_refused_swarm_stops_the_plan_at_that_step(self):
        refused = opener({"success": False, "message": "no"})
        r = runner.Runner(
            "ctx",
            exec_=lambda argv, cwd=None: (0, "sample-api-a"),
            locust="http://l",
            http_post=lambda u, f: runner._post_form(u, f, opener=refused),
        )
        with self.assertRaises(RuntimeError):
            r.execute(r.plan_inject("S6", users=300, spawn_rate=5))


class BatchTest(tb.Base):
    """The batch passes the cell's users and spawn rate to the injection."""

    def run_cell(self, cell, http_post):
        ft = tb.FakeTime(self.clock)
        c = tb.FakeCluster(ft)
        r = runner.Runner("ctx", exec_=c, sleep=ft.sleep, locust="http://l", http_post=http_post,
                          http_get=lambda url: (200, json.dumps(c.locust_doc)))  # fmt: skip
        live = batch.LiveRun(r, tb.PRE, wait_s=20, poll_s=5, sleep=ft.sleep, monotonic=ft.monotonic,
                             wall=ft.wall, stat=tb.audit_fresh(ft))  # fmt: skip
        return live(cell, {}, batch.Guard(self.clock, 1.0))

    def test_the_cells_spawn_rate_reaches_the_swarm(self):
        posts = []
        cell = {"scenario": "S4", "reasoner": "R1", "level": 3, "users": 240, "spawn_rate": 4}  # fmt: skip
        self.run_cell(cell, lambda u, f: posts.append((u, f)))
        self.assertIn(
            ("http://l/swarm", {"user_count": ["240"], "spawn_rate": ["4"]}), posts
        )

    def test_a_load_cell_without_a_spawn_rate_injects_nothing(self):
        posts = []
        with self.assertRaises(ValueError):
            self.run_cell(
                {"scenario": "S4", "reasoner": "R1", "level": 3, "users": 240},
                lambda u, f: posts.append((u, f)),
            )
        self.assertNotIn("http://l/swarm", [u for u, _ in posts])


if __name__ == "__main__":
    unittest.main()


class SpikeTotalTest(unittest.TestCase):
    """A spike's users is Locust's total: ProdUser keeps its fixed_count, the rest go to nexus-dev
    (locustfile.py; owner, 2026-11-20). S2 is 220 (dev 140)."""

    def test_the_runner_constant_is_the_locustfiles_fixed_count(self):
        src = (runner.REPO / "platform/load/locustfile.py").read_text()
        body = src.split("class ProdUser(_Mix):", 1)[1].split("\nclass ", 1)[0]
        (n,) = re.findall(r"^\s+fixed_count = (\d+)", body, flags=re.MULTILINE)
        self.assertEqual(int(n), runner.PROD_USERS)
        dev = src.split("class DevUser(_Mix):", 1)[1].split("\nclass ", 1)[0]
        self.assertNotIn("fixed_count", dev)  # DevUser takes every other user

    def test_s2_is_a_plain_resize_to_220(self):
        r = PlanTest.runner(self)
        (step,) = r.plan_inject("S2", users=220, spawn_rate=4)
        self.assertEqual(
            step.argv, ("POST", "http://l/swarm", "user_count=220&spawn_rate=4")
        )
        # no /stop and no class field: the running swarm keeps both classes
        self.assertNotIn("user_classes", step.argv[2])

    def test_a_total_that_leaves_dev_nothing_is_refused(self):
        r = PlanTest.runner(self)
        for sc in ("S2", "S4", "S6"):
            for bad in (runner.PROD_USERS, 40, 0, True, 170.0):
                with self.assertRaises(ValueError, msg=f"{sc} {bad!r}"):
                    r.plan_inject(sc, users=bad, spawn_rate=4)
            self.assertTrue(
                r.plan_inject(sc, users=runner.PROD_USERS + 1, spawn_rate=4)
            )
