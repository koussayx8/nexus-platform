"""Offline tests for arms.py and runner.py (fake exec: no cluster, no network)."""

import contextlib
import io
import json
import unittest
from unittest import mock

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

    def test_haiku_and_sweep_are_phase_b_only(self):
        for name in ("H1", "R3-low", "R3-medium", "R3-high"):
            self.assertEqual(arms.arm_config(name)["phases"], ("B",), name)
        for phase in ("A", "C", "D"):
            offending = {"H1", "R3-low", "R3-medium", "R3-high"} & set(
                arms.arms_for_phase(phase)
            )
            self.assertFalse(offending, (phase, offending))
        self.assertEqual(arms.arms_for_phase("A"), ["R1", "R3"])  # C1 and C2
        self.assertEqual(arms.arms_for_phase("D"), ["R0"])  # C7
        self.assertEqual(
            arms.arms_for_phase("B"),
            ["H1", "R0", "R1", "R2", "R3", "R3-high", "R3-low", "R3-medium"],
        )

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
        keys = ("reset to baseline", "SETLEVEL 2", "git push origin", "refresh=hard", "rollout restart",
                "--replicas=2", "LOGIN", "/stop")  # fmt: skip
        order = [next(i for i, f in enumerate(flat) if key in f) for key in keys]
        self.assertEqual(order, sorted(order))
        self.assertFalse(any("--force" in f or "reset --hard" in f for f in flat))

    def test_the_level_is_a_commit_never_a_kubectl_label(self):
        """selfHeal reverts a hand-set label (the Namespace is declared in overlays/dev, tracked by
        sample-api-dev), so the reset commits the level on experiment/dev-state instead."""
        r, _ = self.runner(locust="http://l", git_dir="/g")
        flat = [" ".join(s.argv) for s in r.plan_reset(3, breaker_epoch=5)]
        self.assertFalse(any(" label " in f for f in flat))
        self.assertIn("git commit -m chore(exp): nexus-dev level 3", flat)
        i = flat.index("SETLEVEL 3")
        self.assertEqual(flat[i + 1], "git add overlays/dev/namespace.yaml")
        self.assertTrue(
            flat[i + 3].startswith("git push origin HEAD:experiment/dev-state")
        )
        self.assertLess(
            i, next(j for j, f in enumerate(flat) if "refresh=hard" in f)
        )  # refresh picks it up

    def test_set_level_edits_only_the_label_and_is_idempotent(self):
        import pathlib
        import tempfile

        src = (
            pathlib.Path(__file__).resolve().parents[3] / "overlays/dev/namespace.yaml"
        )
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d, "overlays/dev/namespace.yaml")
            f.parent.mkdir(parents=True)
            f.write_text(src.read_text())
            before = f.read_text()
            self.assertEqual(runner.set_level(d, 3), 0)
            after = f.read_text()
            self.assertIn('nexus.io/autonomy-level: "3"', after)
            self.assertEqual(
                before.replace('autonomy-level: "0"', 'autonomy-level: "3"'), after
            )
            self.assertEqual(
                runner.set_level(d, 3), 0
            )  # same value: no change, no error
            self.assertEqual(f.read_text(), after)
            f.write_text("kind: Namespace\n")
            self.assertEqual(
                runner.set_level(d, 1), 1
            )  # no label to set: a failed step
            self.assertEqual(runner.set_level(d + "/nope", 1), 1)

    def test_executing_setlevel_runs_in_the_clone(self):
        import pathlib
        import tempfile

        src = (
            pathlib.Path(__file__).resolve().parents[3] / "overlays/dev/namespace.yaml"
        )
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d, "overlays/dev/namespace.yaml")
            f.parent.mkdir(parents=True)
            f.write_text(src.read_text())
            r = runner.Runner(
                "ctx",
                exec_=lambda argv, cwd=None: (0, ""),
                git_dir=d,
                sleep=lambda s: None,
            )
            r.execute([runner.Step(("SETLEVEL", "2"))])
            self.assertIn('autonomy-level: "2"', f.read_text())

    def test_operator_config_patch_then_restart_then_wait(self):
        r, _ = self.runner()
        steps = r.plan_operator_config("off", 1_800_000_000)
        flat = [" ".join(s.argv) for s in steps]
        self.assertIn(
            "-n nexus-system patch configmap nexus-operator-config --type merge -p",
            flat[0],
        )
        self.assertEqual(
            json.loads(flat[0].split(" -p ", 1)[1]),
            {"data": {"advisoryChecks": "off", "breakerEpoch": "1800000000"}},
        )
        self.assertTrue(flat[1].endswith("rollout restart deployment/nexus-operator"))
        self.assertTrue(
            flat[2].endswith("rollout status deployment/nexus-operator --timeout=180s")
        )
        for bad in (("maybe", 1), ("on", -1), ("on", "1")):
            with self.assertRaises(ValueError):
                r.plan_operator_config(*bad)

    def test_reset_sets_breaker_epoch_last_and_only_when_asked(self):
        r, _ = self.runner(locust="http://l")
        self.assertFalse(any("configmap" in " ".join(s.argv) for s in r.plan_reset(3)))
        flat = [" ".join(s.argv) for s in r.plan_reset(3, breaker_epoch=5)]
        self.assertIn("patch configmap", flat[-3])
        self.assertIn("rollout restart deployment/nexus-operator", flat[-2])

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


class ProdTargetTest(unittest.TestCase):
    """--namespace nexus-prod: the fault is refused unless nexus-prod's autonomy level is 0 or 1."""

    def runner(self, level, ns="nexus-prod", rc=0):
        calls = []

        def ex(argv, cwd=None):
            calls.append(list(argv))
            if "namespace" in argv:
                return rc, level
            if "jsonpath={.items[*].metadata.name}" in argv:
                return 0, "p1"
            return 0, ""

        return runner.Runner("ctx", exec_=ex, sleep=lambda s: None, namespace=ns), calls

    def test_default_is_nexus_dev_and_reads_no_level(self):
        r, calls = self.runner("3", ns="nexus-dev")
        (s,) = r.plan_inject("S1")
        self.assertEqual(s.argv[3:5], ("-n", "nexus-dev"))
        self.assertFalse(any("namespace" in c for c in calls))
        self.assertEqual(runner.Runner("ctx").namespace, "nexus-dev")

    def test_unknown_namespace_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.Runner("ctx", namespace="nexus-data")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            runner.main(
                ["plan", "inject", "S1", "--context", "c", "--namespace", "kube-system"]
            )

    def test_prod_at_level_0_or_1_targets_prod_pods(self):
        for level in ("0", "1"):
            r, _ = self.runner(level)
            (s,) = r.plan_inject("S1")
            self.assertEqual(s.argv[3:5], ("-n", "nexus-prod"))

    def test_prod_above_level_1_or_unreadable_is_refused(self):
        for level, rc in (
            ("2", 0),
            ("3", 0),
            ("10", 0),
            ("", 0),
            ("abc", 0),
            ("-1", 0),
            ("0", 1),
        ):
            r, _ = self.runner(level, rc=rc)
            with self.assertRaises(ValueError, msg=f"{level!r} rc={rc}"):
                r.plan_inject("S1")

    def test_prod_refusal_covers_every_scenario_and_s3_never_targets_prod(self):
        r, _ = self.runner("3")
        for sc in ("S1", "S2", "S4", "S5", "S6"):
            with self.assertRaises(ValueError):
                r.plan_inject(sc, users=5)
        r, _ = self.runner("0")
        with self.assertRaises(ValueError):
            r.plan_inject("S3", s3_patch="p.diff")

    def test_s5_alters_the_namespaces_own_role_only(self):
        for ns, level, role, other in (
            ("nexus-prod", "1", "app_prod", "app_dev"),
            ("nexus-dev", "3", "app_dev", "app_prod"),
        ):
            r, _ = self.runner(level, ns=ns)
            (s,) = r.plan_inject("S5")
            sql = s.argv[-1]
            self.assertIn(f"ALTER ROLE {role} NOLOGIN", sql)
            self.assertIn(f"usename='{role}'", sql)
            self.assertNotIn(other, sql)

    def test_reset_restores_both_roles(self):
        r, _ = self.runner("1", ns="nexus-dev")
        sql = " ".join(a for s in r.plan_reset(3) for a in s.argv if "LOGIN" in a)
        self.assertIn("ALTER ROLE app_dev LOGIN", sql)
        self.assertIn("ALTER ROLE app_prod LOGIN", sql)

    def test_prod_still_needs_execute(self):
        calls = []

        def ex(argv, cwd=None):
            calls.append(list(argv))
            return 0, "1" if "namespace" in argv else "p1"

        for flags in ([], ["--execute"]):
            calls.clear()
            with (
                mock.patch.object(runner, "_subprocess_exec", ex),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = runner.main(
                    [
                        "run",
                        "inject",
                        "S1",
                        "--context",
                        "c",
                        "--namespace",
                        "nexus-prod",
                    ]
                    + flags
                )
            self.assertEqual(rc, 0)
            mutating = [c for c in calls if "exec" in c]  # the S1 kubectl exec
            self.assertEqual(bool(mutating), bool(flags))


if __name__ == "__main__":
    unittest.main()
