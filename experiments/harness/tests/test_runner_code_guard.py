"""runner.code_guard: with --execute the runner refuses dirty or unreviewed code (owner, 2026-11-19)."""

import contextlib
import io
import pathlib
import subprocess
import tempfile
import unittest

from .. import runner


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


class RealRepo(unittest.TestCase):
    """A throwaway repository; origin/dev and origin/main are plain remote-tracking refs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        git(self.repo, "init", "-q")
        (self.repo / "experiments").mkdir()
        (self.repo / "experiments" / "a.py").write_text("x = 1\n")
        (self.repo / "README").write_text("r\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "update-ref", "refs/remotes/origin/dev", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self):
        return runner.code_guard(repo=self.repo)

    def test_a_clean_commit_on_origin_dev_passes(self):
        self.assertIsNone(self.guard())

    def test_a_commit_on_origin_main_only_passes(self):
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "on main")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertIsNone(self.guard())

    def test_an_unpushed_commit_is_refused(self):
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "local only")
        why = self.guard()
        self.assertIn("is not a commit on origin/dev or origin/main", why)

    def test_a_modified_or_new_file_under_experiments_is_refused(self):
        (self.repo / "experiments" / "a.py").write_text("x = 2\n")
        (self.repo / "experiments" / "new.patch").write_text("p\n")
        why = self.guard()
        self.assertIn("uncommitted changes under experiments/", why)
        self.assertIn("experiments/a.py", why)

    def test_a_change_outside_experiments_is_allowed(self):
        (self.repo / "README").write_text("changed\n")
        self.assertIsNone(self.guard())

    def test_not_a_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("cannot read git status", runner.code_guard(repo=d))


class Main(unittest.TestCase):
    def test_execute_is_refused_with_the_reason_and_nothing_runs(self):
        seen = []

        def fake(argv, cwd=None, timeout=120):
            seen.append(argv)
            return (
                (0, " M experiments/harness/runner.py\n")
                if "status" in argv
                else (0, "")
            )

        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock_runner() as made:
            rc = runner.main(
                ["run", "refresh", "--context", "c", "--execute"], guard_exec=fake
            )
        self.assertEqual(rc, 2)
        self.assertIn(
            "refusing to execute: uncommitted changes under experiments/",
            err.getvalue(),
        )
        self.assertIn("experiments/harness/runner.py", err.getvalue())
        self.assertEqual(made, [])  # no Runner, no step
        self.assertTrue(all(a[0] == "git" for a in seen))

    def test_plan_and_run_without_execute_do_not_check(self):
        def fake(argv, cwd=None, timeout=120):
            raise AssertionError("the guard ran without --execute")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                runner.main(["plan", "refresh", "--context", "c"], guard_exec=fake), 0
            )
            self.assertEqual(
                runner.main(["run", "refresh", "--context", "c"], guard_exec=fake), 0
            )


@contextlib.contextmanager
def mock_runner():
    """Records Runner constructions instead of making one."""
    made = []
    real = runner.Runner

    def record(*a, **k):
        made.append((a, k))
        raise AssertionError("a Runner was built after the guard refused")

    runner.Runner = record
    try:
        yield made
    finally:
        runner.Runner = real


if __name__ == "__main__":
    unittest.main()
