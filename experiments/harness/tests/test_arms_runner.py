"""Offline tests for arms.py and runner.py (fake exec: no cluster, no network)."""

import unittest

from .. import arms, runner


class ArmsTest(unittest.TestCase):
    def test_spec_arms_and_haiku(self):
        self.assertEqual(arms.arm_config("R2")["model"], "openai/gpt-oss-20b")
        self.assertEqual(arms.arm_config("R3")["model"], "openai/gpt-oss-120b")
        h = arms.arm_config("H1")
        self.assertEqual((h["provider"], h["mode"]), ("anthropic", "llm"))
        for a in ("R0", "R1"):
            self.assertIsNone(arms.arm_config(a)["model"])

    def test_common_settings_frozen(self):
        for a in ("R2", "R3", "H1", "R3-high"):
            c = arms.arm_config(a)
            self.assertEqual(
                (
                    c["temperature"],
                    c["max_output_tokens"],
                    c["timeout_s"],
                    c["attempts"],
                ),
                (0, 1024, 30, 1),
            )

    def test_sweep(self):
        s = arms.sweep("R3")
        self.assertEqual(sorted(s), ["R3-high", "R3-low", "R3-medium"])
        self.assertEqual(
            {v["reasoning_effort"] for v in s.values()}, {"low", "medium", "high"}
        )
        self.assertEqual(
            arms.arm_config("R3-low")["model"], arms.arm_config("R3")["model"]
        )
        with self.assertRaises(ValueError):
            arms.sweep("R1")
        with self.assertRaises(KeyError):
            arms.arm_config("R9")

    def test_configurations_refer_to_known_arms(self):
        for arms_, _levels, _phase in arms.CONFIGURATIONS.values():
            for a in arms_:
                self.assertIn(a, arms.ARMS)
        self.assertEqual(arms.CONFIGURATIONS["C7"][0], ("R0",))


class FakeExec:
    def __init__(self, pods="sample-api-a sample-api-b", sync="Synced/Healthy"):
        self.calls, self.pods, self.sync = [], pods, sync

    def __call__(self, argv, cwd=None):
        self.calls.append((list(argv), cwd))
        if "jsonpath={.items[*].metadata.name}" in argv:
            return 0, self.pods
        if any("sync.status" in a for a in argv):
            return 0, self.sync
        return 0, ""


class RunnerTest(unittest.TestCase):
    def runner(self, **kw):
        ex = FakeExec(**{k: v for k, v in kw.items() if k in ("pods", "sync")})
        return runner.Runner("ctx", exec_=ex, sleep=lambda s: None, locust=kw.get("locust"), git_dir=kw.get("git_dir"),
                             http_post=kw.get("http_post")), ex  # fmt: skip

    def test_context_required_and_always_named(self):
        with self.assertRaises(ValueError):
            runner.Runner("")
        r, _ = self.runner(locust="http://l", git_dir="/g")
        for step in r.plan_reset(3) + r.plan_inject("S1") + r.plan_refresh():
            if step.argv[0] == runner.CLI:
                self.assertEqual(step.argv[1:3], ("--context", "ctx"))

    def test_s1_hangs_every_pod_and_tolerates_the_cut_request(self):
        r, ex = self.runner()
        steps = r.plan_inject("S1")
        self.assertEqual(len(steps), 2)
        self.assertTrue(
            all(s.ignore_failure and "/fault/hang" in s.argv[-1] for s in steps)
        )
        self.assertEqual(
            ex.calls[-1][0][-1], "jsonpath={.items[*].metadata.name}"
        )  # only a read at plan time

    def test_load_scenarios_need_locust_and_users(self):
        r, _ = self.runner()
        with self.assertRaises(ValueError):
            r.plan_inject("S2", users=10)
        r, _ = self.runner(locust="http://l")
        with self.assertRaises(ValueError):
            r.plan_inject("S4")
        s6 = r.plan_inject("S6", users=15)
        self.assertEqual(s6[0].argv, ("POST", "http://l/swarm", "user_count=15"))
        self.assertTrue(any("/fault/inject-logs" in s.argv[-1] for s in s6[1:]))

    def test_s3_is_a_forward_push_only(self):
        r, _ = self.runner(git_dir="/g")
        steps = r.plan_inject("S3", s3_patch="p.diff")
        flat = [" ".join(s.argv) for s in steps]
        self.assertEqual(flat[-1], "git push origin HEAD:experiment/dev-state")
        self.assertFalse(any("force" in f or " -f" in f for f in flat))
        with self.assertRaises(ValueError):
            self.runner()[0].plan_inject("S3")

    def test_s5_targets_the_database_role(self):
        r, _ = self.runner(pods="dependency-db-0")
        (s,) = r.plan_inject("S5")
        self.assertIn("NOLOGIN", s.argv[-1])
        self.assertIn("pg_terminate_backend", s.argv[-1])
        with self.assertRaises(ValueError):
            r.plan_inject("S9")

    def test_reset_order_and_no_force(self):
        r, _ = self.runner(locust="http://l", git_dir="/g")
        steps = r.plan_reset(2, baseline_ref="abc123")
        flat = [" ".join(s.argv) for s in steps]
        order = [next(i for i, f in enumerate(flat) if key in f) for key in
                 ("git push origin", "refresh=hard", "rollout restart", "--replicas=2", "LOGIN", "/stop", "autonomy-level=2")]  # fmt: skip
        self.assertEqual(order, sorted(order))
        self.assertFalse(any("--force" in f or "reset --hard" in f for f in flat))
        self.assertIn(
            "chore(exp): reset to baseline",
            flat[3] if "commit" in flat[3] else " ".join(flat),
        )

    def test_refresh_annotates_without_sync(self):
        r, _ = self.runner()
        flat = [" ".join(s.argv) for s in r.plan_refresh()]
        self.assertEqual(len(flat), 2)
        self.assertTrue(
            all(
                "argocd.argoproj.io/refresh=hard" in f
                and "sync" not in f.replace("argocd.argoproj.io", "")
                for f in flat
            )
        )

    def test_execute_runs_steps_in_order_and_stops_on_failure(self):
        posts = []
        r, _ex = self.runner(
            locust="http://l", http_post=lambda u, f: posts.append((u, f))
        )
        log = r.execute(r.plan_reset(3))
        self.assertEqual(posts, [("http://l/stop", {})])
        self.assertEqual(len(log), len(r.plan_reset(3)))

        def failing(argv, cwd=None):
            return (1, "") if "rollout" in argv else (0, "")

        r2 = runner.Runner("ctx", exec_=failing, sleep=lambda s: None)
        with self.assertRaises(RuntimeError):
            r2.execute(r2.plan_reset(3))

    def test_ignore_failure_step_does_not_raise(self):
        r = runner.Runner(
            "ctx", exec_=lambda argv, cwd=None: (1, ""), sleep=lambda s: None
        )
        r.execute([runner.Step(("x",), True, "", True)])

    def test_wait_synced(self):
        r, _ = self.runner()
        self.assertTrue(r.wait_synced(timeout_s=5))
        r, _ = self.runner(sync="OutOfSync/Healthy")
        self.assertFalse(r.wait_synced(timeout_s=0))

    def test_cli_plan_prints_and_does_not_execute(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = runner.main(["plan", "refresh", "--context", "ctx"])
        self.assertEqual(rc, 0)
        self.assertIn("refresh=hard", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
