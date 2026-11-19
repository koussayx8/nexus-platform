"""The committed S3 fault (experiments/faults/s3-bad-rollout.patch): it touches only the sample-api
workload files in overlays/dev/, never the Namespace (the autonomy level), applies to the overlay
as committed, and makes the readiness probe ask for a route sample-api does not serve. The runner
stages exactly the patch's files and refuses any other patch shape. Offline: git on a temp copy."""

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

from .. import runner

ROOT = pathlib.Path(__file__).resolve().parents[3]
PATCH = ROOT / runner.S3_PATCH


def git(cwd, *args):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}  # fmt: skip
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


class CommittedPatch(unittest.TestCase):
    def test_touches_only_allowed_workload_files_never_the_namespace(self):
        files = runner.s3_patch_files(str(PATCH))
        self.assertEqual(files, ["overlays/dev/kustomization.yaml"])
        self.assertNotIn(runner.LEVEL_FILE, files)
        self.assertNotIn(
            "namespace.yaml", PATCH.read_text().split("\ndiff --git a/", 1)[1]
        )

    def test_applies_to_the_committed_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "overlays/dev").mkdir(parents=True)
            shutil.copy(
                ROOT / "overlays/dev/kustomization.yaml",
                pathlib.Path(d) / "overlays/dev/",
            )
            self.assertEqual(git(d, "init", "-q").returncode, 0)
            check = git(d, "apply", "--check", str(PATCH))
            self.assertEqual(check.returncode, 0, check.stderr)
            git(d, "apply", str(PATCH))
            text = (pathlib.Path(d) / "overlays/dev/kustomization.yaml").read_text()
        self.assertIn("readinessProbe:", text)
        self.assertRegex(text, r"\n {20}path: /readyz\n")

    def test_the_probe_route_does_not_exist_so_readiness_really_fails(self):
        app = (ROOT / "apps/sample-api/main.py").read_text()
        routes = set(re.findall(r'@app\.get\("([^"]+)"\)', app))
        self.assertIn("/ready", routes)  # the base probe's route
        self.assertNotIn("/readyz", routes)  # the patch's: a 404, never ready


class RunnerRefuses(unittest.TestCase):
    def _patch(self, body):
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
            f.write(body)
        self.addCleanup(os.unlink, f.name)
        return f.name

    def _diff(self, path):
        return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"

    def test_namespace_outside_lookalike_and_nested_paths(self):
        for path in (
            "overlays/dev/namespace.yaml",
            "platform/x.yaml",
            "overlays/dev-evil/kustomization.yaml",
            "overlays/prod/kustomization.yaml",
            "overlays/dev/sample-api/../namespace.yaml",
            "overlays/dev/sample-api/x.yaml",
        ):
            with self.assertRaises(ValueError, msg=path):
                runner.s3_patch_files(self._patch(self._diff(path)))

    def test_mixed_patch_is_refused_whole(self):
        body = self._diff("overlays/dev/kustomization.yaml") + self._diff(
            "overlays/dev/namespace.yaml"
        )
        with self.assertRaises(ValueError):
            runner.s3_patch_files(self._patch(body))

    def test_rename_plain_diff_and_missing_file(self):
        rename = "diff --git a/overlays/dev/kustomization.yaml b/overlays/dev/sample-api-x.yaml\nrename from x\n"
        for body in (rename, "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", ""):
            with self.assertRaises(ValueError):
                runner.s3_patch_files(self._patch(body))
        with self.assertRaises(ValueError):
            runner.s3_patch_files("/nonexistent/s3.patch")

    def test_a_sample_api_file_is_allowed(self):
        files = runner.s3_patch_files(
            self._patch(self._diff("overlays/dev/sample-api-probe.yaml"))
        )
        self.assertEqual(files, ["overlays/dev/sample-api-probe.yaml"])

    def test_relative_patch_is_read_from_the_git_dir(self):
        r = runner.Runner.__new__(
            runner.Runner
        )  # plan only; no cluster call is made below
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "experiments/faults").mkdir(parents=True)
            shutil.copy(PATCH, pathlib.Path(d) / runner.S3_PATCH)
            r.namespace, r.git_dir = runner.NS_DEV, d
            steps = r.plan_inject("S3", s3_patch=runner.S3_PATCH)
        self.assertEqual(
            steps[0].argv, ("git", "apply", str(pathlib.Path(d) / runner.S3_PATCH))
        )
        self.assertEqual(
            steps[1].argv, ("git", "add", "--", "overlays/dev/kustomization.yaml")
        )


if __name__ == "__main__":
    unittest.main()
