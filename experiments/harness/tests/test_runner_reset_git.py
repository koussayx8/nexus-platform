"""The reset's Git steps, run with real git on a throwaway remote (Block 5 finding (i)): the
experiment worktree ends detached at the pushed commit, and no local branch is made or moved.

No network and no cluster: only the git and SETLEVEL steps of plan_reset run, against a bare
repository in a temporary directory; the git identity and config are the test's own."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from .. import runner

NS_FILE = """apiVersion: v1
kind: Namespace
metadata:
  name: nexus-dev
  labels:
    nexus.io/autonomy-level: "{level}"
"""


class ResetGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="reset-git-")
        root = Path(self.tmp.name)
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        self.remote, seed, self.wt = root / "remote.git", root / "seed", root / "wt"
        self.git(root, "init", "-q", "--bare", str(self.remote))
        self.git(root, "init", "-q", "-b", runner.BRANCH, str(seed))
        (seed / runner.LEVEL_FILE).parent.mkdir(parents=True)
        (seed / runner.LEVEL_FILE).write_text(NS_FILE.format(level=3))
        (seed / "app.yaml").write_text("probe: /ready\n")
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-q", "-m", "baseline")
        self.baseline = self.git(seed, "rev-parse", "HEAD")
        (seed / "app.yaml").write_text("probe: /readyz\n")  # the S3-like change to undo
        self.git(seed, "commit", "-q", "-am", "inject")
        self.git(seed, "push", "-q", str(self.remote), runner.BRANCH)
        self.git(
            root, "clone", "-q", "-b", runner.BRANCH, str(self.remote), str(self.wt)
        )

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, cwd, *args) -> str:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def exec_(self, argv, cwd=None):
        out = subprocess.run(
            argv, cwd=cwd, env=self.env, capture_output=True, text=True, check=False
        )
        return out.returncode, out.stdout

    def reset(self, level=3):
        r = runner.Runner("ctx", exec_=self.exec_, git_dir=str(self.wt))
        steps = r.plan_reset(level, baseline_ref=self.baseline, touch_locust=False)
        git_steps = [s for s in steps if s.argv[0] in ("git", "SETLEVEL")]
        return r.execute(git_steps)

    def assert_detached_at_remote(self):
        sym = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=self.wt,
            env=self.env,
            check=False,
        )
        self.assertNotEqual(sym.returncode, 0, "HEAD is on a branch")
        head = self.git(self.wt, "rev-parse", "HEAD")
        remote = self.git(self.wt, "ls-remote", "origin", f"refs/heads/{runner.BRANCH}")
        self.assertEqual(remote.split()[0], head)
        self.assertEqual((self.wt / "app.yaml").read_text(), "probe: /ready\n")

    def test_a_detached_worktree_stays_detached(self):
        self.git(self.wt, "checkout", "-q", "--detach", f"origin/{runner.BRANCH}")
        # as `git worktree add --detach` leaves it: no local branch
        self.git(self.wt, "branch", "-q", "-D", runner.BRANCH)
        self.reset()
        self.assert_detached_at_remote()
        self.assertEqual(self.git(self.wt, "branch", "--list", runner.BRANCH), "")

    def test_a_worktree_left_on_the_local_branch_ends_detached(self):
        # the state the old reset left ~/nexus-exp in (git checkout experiment/dev-state)
        self.assertEqual(
            self.git(self.wt, "symbolic-ref", "--short", "HEAD"), runner.BRANCH
        )
        before = self.git(self.wt, "rev-parse", runner.BRANCH)
        self.reset()
        self.assert_detached_at_remote()
        # the local branch is not moved
        self.assertEqual(self.git(self.wt, "rev-parse", runner.BRANCH), before)

    def test_the_tree_equals_the_baseline_and_the_push_is_forward(self):
        old_tip = self.git(self.wt, "rev-parse", f"origin/{runner.BRANCH}")
        self.reset()
        new_tip = self.git(self.wt, "rev-parse", "HEAD")
        self.assertNotEqual(new_tip, old_tip)
        self.git(self.wt, "merge-base", "--is-ancestor", old_tip, new_tip)
        self.assertEqual(
            self.git(self.wt, "rev-parse", "HEAD^{tree}"),
            self.git(self.wt, "rev-parse", f"{self.baseline}^{{tree}}"),
        )

    def test_a_second_reset_is_a_no_op_and_stays_detached(self):
        self.reset()
        tip = self.git(self.wt, "rev-parse", "HEAD")
        log = self.reset()
        self.assert_detached_at_remote()
        self.assertEqual(self.git(self.wt, "rev-parse", "HEAD"), tip)
        commits = [
            e["rc"] for e in log if tuple(e["step"]["argv"][:2]) == ("git", "commit")
        ]
        self.assertEqual(commits, [1, 1])  # both commits skipped: no diff


if __name__ == "__main__":
    unittest.main()
