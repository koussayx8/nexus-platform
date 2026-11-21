"""Block 9 runner items (ADR-047): the reset needs its baseline, `reset --locust` restarts the
baseline load, and the batch driver runs reviewed code only. Offline: no cluster, no Locust."""

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .. import batch, runner
from . import test_batch as tb

REPO = Path(__file__).resolve().parents[3]


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = runner.main(list(argv))
        except SystemExit as e:  # argparse's ap.error
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class ResetNeedsItsBaselineTest(unittest.TestCase):
    """The S3 lesson: a reset that does not restore the recorded commit leaves the fault deployed."""

    def test_without_a_baseline_ref_the_reset_is_refused(self):
        for mode in ("plan", "run"):
            rc, out, err = run_cli(mode, "reset", "--context", "ctx", "--git-dir", "/g")
            self.assertEqual(rc, 2, mode)
            self.assertIn("reset needs --baseline-ref", err)
            self.assertEqual(out, "")

    def test_without_a_git_dir_the_ref_would_be_ignored_so_it_is_refused(self):
        rc, _, err = run_cli(
            "plan", "reset", "--context", "ctx", "--baseline-ref", "abc123"
        )
        self.assertEqual(rc, 2)
        self.assertIn("--git-dir", err)

    def test_with_both_the_plan_restores_the_ref(self):
        rc, out, _ = run_cli(
            "plan",
            "reset",
            "--context",
            "ctx",
            "--git-dir",
            "/g",
            "--baseline-ref",
            "abc123",
        )
        self.assertEqual(rc, 0)
        argvs = [" ".join(s["argv"]) for s in json.loads(out)]
        self.assertIn("git restore --source=abc123 --staged --worktree -- .", argvs)


class ResetRestartsTheBaselineLoadTest(unittest.TestCase):
    def plan(self, *extra):
        rc, out, _ = run_cli("plan", "reset", "--context", "ctx", "--git-dir", "/g",
                             "--baseline-ref", "abc123", *extra)  # fmt: skip
        self.assertEqual(rc, 0)
        return [s for s in json.loads(out) if s["argv"][0] in ("GET", "POST")]

    def test_reset_locust_stops_then_starts_the_frozen_baseline(self):
        stop, swarm = self.plan("--locust", "http://l")
        self.assertEqual(stop["argv"], ["GET", "http://l/stop"])
        self.assertEqual(swarm["argv"][:2], ["POST", "http://l/swarm"])
        import urllib.parse

        self.assertEqual(
            urllib.parse.parse_qs(swarm["argv"][2]),
            {
                "user_count": ["160"],
                "spawn_rate": ["4"],
                "user_classes": ["DevUser", "ProdUser"],
            },
        )

    def test_without_locust_the_reset_has_no_locust_step(self):
        self.assertEqual(self.plan(), [])

    def test_the_baseline_is_the_frozen_one(self):
        master = (REPO / "platform/load/master.yaml").read_text()
        b = runner.BASELINE_SWARM
        self.assertIn(f"- --users={b['users']}\n", master)
        self.assertIn(f"- --spawn-rate={b['spawn_rate']}\n", master)
        classes = re.findall(
            r"^class (\w+)\(_Mix\):",
            (REPO / "platform/load/locustfile.py").read_text(),
            flags=re.MULTILINE,
        )
        self.assertEqual(sorted(b["classes"]), sorted(classes))
        # 160 = ProdUser's fixed 80 + 80 DevUsers (ADR-026 addendum)
        self.assertEqual(b["users"] - runner.PROD_USERS, 80)


class BatchCodeGuardTest(unittest.TestCase):
    """#266's guard, now in the batch driver too: `run --execute` refuses dirty or unmerged code
    before anything is built or run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        batch.Queue.create(self.state, tb.REF, tb.CELLS, precheck_cfg=tb.PRE)

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, guard_exec):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = batch.main(["run", "--state", self.state, "--context", "ctx", "--approval-ref", tb.REF,
                             "--execute", "--max-runs", "1"], guard_exec=guard_exec)  # fmt: skip
        return rc, err.getvalue()

    def test_dirty_experiments_refuse_before_any_live_object(self):
        def dirty(argv, cwd=None):
            return (
                (0, " M experiments/harness/batch.py\n")
                if argv[1] == "status"
                else (0, "")
            )

        with mock.patch.object(batch, "LiveRun", side_effect=AssertionError("built")), \
             mock.patch.object(batch, "drive", side_effect=AssertionError("driven")):  # fmt: skip
            rc, err = self.main(dirty)
        self.assertEqual(rc, 2)
        self.assertIn(
            "refusing to execute: uncommitted changes under experiments/", err
        )

    def test_an_unmerged_head_refuses(self):
        def unmerged(argv, cwd=None):
            if argv[1] == "status":
                return 0, ""
            if argv[1] == "rev-parse":
                return 0, "f" * 40 + "\n"
            return 1, ""  # merge-base --is-ancestor: not on origin/dev or origin/main

        with mock.patch.object(batch, "LiveRun", side_effect=AssertionError("built")):
            rc, err = self.main(unmerged)
        self.assertEqual(rc, 2)
        self.assertIn("is not a commit on origin/dev or origin/main", err)

    def test_reviewed_code_goes_on_to_the_driver(self):
        def clean(argv, cwd=None):
            return (0, "a" * 40 + "\n") if argv[1] == "rev-parse" else (0, "")

        with mock.patch.object(batch, "LiveRun") as live, \
             mock.patch.object(batch, "drive", return_value={"stopped": "max_runs"}) as drive:  # fmt: skip
            rc, _ = self.main(clean)
        self.assertEqual(rc, 0)
        self.assertTrue(live.called and drive.called)

    def test_a_dry_run_needs_no_guard(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = batch.main(["run", "--state", self.state, "--context", "ctx"],
                            guard_exec=lambda *a, **k: self.fail("guard called"))  # fmt: skip
        self.assertEqual((rc, json.loads(out.getvalue())["dry_run"]), (0, True))


if __name__ == "__main__":
    unittest.main()


class EveryCellNamesItsBaselineTest(unittest.TestCase):
    """ADR-047 addendum (owner): `init` refuses every cell without baseline_ref, one rule for all
    cells, the same as the runner's reset."""

    def create(self, cell):
        with tempfile.TemporaryDirectory() as d:
            return batch.Queue.create(d, tb.REF, [cell], precheck_cfg=tb.PRE)

    def test_a_cell_without_or_with_a_bad_baseline_ref_is_refused(self):
        good = dict(tb.CELLS[0])
        missing = {k: v for k, v in good.items() if k != "baseline_ref"}
        with self.assertRaises(batch.Refused) as cm:
            self.create(missing)
        self.assertIn("lacks 'baseline_ref'", str(cm.exception))
        for bad in ("", " ", "-x", "--source=evil", "a b", "abc\n", 42, None, True):
            with self.assertRaises(batch.Refused, msg=repr(bad)):
                self.create(good | {"baseline_ref": bad})

    def test_every_scenario_needs_it_not_only_s3(self):
        for sc in ("S1", "S2", "S3", "S4", "S5", "S6"):
            cell = {k: v for k, v in tb.CELLS[0].items() if k != "baseline_ref"} | {
                "scenario": sc
            }
            with self.assertRaises(batch.Refused, msg=sc):
                self.create(cell)

    def test_a_commit_or_a_ref_is_accepted_and_reaches_the_reset(self):
        for ref in (
            "a3adced",
            "a3adced2bf00615777002b5e4d943a699c44e57b",
            "origin/experiment/dev-state~1",
        ):
            q = self.create(dict(tb.CELLS[0]) | {"baseline_ref": ref})
            self.assertEqual(q.doc["cells"][0]["baseline_ref"], ref)
        r = runner.Runner("ctx", exec_=lambda *a, **k: (0, ""), git_dir="/g")
        flat = [" ".join(s.argv) for s in r.plan_reset(3, tb.CELLS[0]["baseline_ref"])]
        self.assertIn(
            f"git restore --source={tb.CELLS[0]['baseline_ref']} --staged --worktree -- .",
            flat,
        )

    def test_the_example_cells_file_names_one_everywhere(self):
        conf = json.loads(
            (REPO / "experiments/harness/batch-config.example.json").read_text()
        )
        self.assertTrue(all(c.get("baseline_ref") for c in conf["cells"]))
