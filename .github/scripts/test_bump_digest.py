"""Offline tests for bump_digest.py. Run: python3 -m unittest discover -s .github/scripts -p 'test_*.py'"""

import os
import subprocess
import sys
import tempfile
import unittest

import bump_digest

OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
IMG = "ghcr.io/koussayx8/nexus-platform/sample-api"

KUSTOMIZE = f"""images:
  - name: {IMG}
    digest: {OLD}
  - name: ghcr.io/koussayx8/nexus-platform/other
    digest: {OLD}
"""

POD = f"""      containers:
        - name: operator
          image: ghcr.io/koussayx8/nexus-platform/nexus-operator@{OLD}
        - name: side
          image: ghcr.io/koussayx8/nexus-platform/nexus-operator-side@{OLD}
"""


class BumpTests(unittest.TestCase):
    def test_kustomize_entry_only_that_image(self):
        out = bump_digest.bump(KUSTOMIZE, IMG, NEW)
        self.assertEqual(out.count(NEW), 1)
        self.assertEqual(out.count(OLD), 1)
        self.assertLess(out.index(NEW), out.index("other"))

    def test_pod_spec_does_not_touch_a_longer_image_name(self):
        out = bump_digest.bump(
            POD, "ghcr.io/koussayx8/nexus-platform/nexus-operator", NEW
        )
        self.assertEqual(out.count(NEW), 1)
        self.assertIn(f"nexus-operator@{NEW}", out)
        self.assertIn(f"nexus-operator-side@{OLD}", out)

    def test_missing_image_is_an_error(self):
        with self.assertRaises(ValueError):
            bump_digest.bump(KUSTOMIZE, IMG + "-x", NEW)

    def test_two_matches_is_an_error(self):
        with self.assertRaises(ValueError):
            bump_digest.bump(KUSTOMIZE + KUSTOMIZE, IMG, NEW)

    def test_bad_digest_is_an_error(self):
        for bad in ("latest", "sha256:abc", NEW.upper(), NEW + "0"):
            with self.assertRaises(ValueError):
                bump_digest.bump(KUSTOMIZE, IMG, bad)

    def test_cli_rewrites_and_reports_no_change(self):
        script = os.path.join(os.path.dirname(__file__), "bump_digest.py")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kustomization.yaml")
            with open(path, "w") as f:
                f.write(KUSTOMIZE)
            ok = subprocess.run(
                [sys.executable, script, path, IMG, NEW],
                capture_output=True,
                check=False,
            )
            self.assertEqual(ok.returncode, 0)
            with open(path) as f:
                self.assertIn(f"digest: {NEW}", f.read())
            again = subprocess.run(
                [sys.executable, script, path, IMG, NEW],
                capture_output=True,
                check=False,
            )
            self.assertEqual(again.returncode, 3)


if __name__ == "__main__":
    unittest.main()
