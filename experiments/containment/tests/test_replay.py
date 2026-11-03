"""The offline replay of the matrix through the Kyverno CLI with mocked context (needs `kyverno`).

CI sets REQUIRE_KYVERNO=1 so a missing CLI fails the job; locally the tests skip without it."""

import os
import shutil
import unittest

from .. import kyverno_replay as kr

HAVE = shutil.which("kyverno") is not None


@unittest.skipUnless(
    HAVE or os.environ.get("REQUIRE_KYVERNO"), "kyverno CLI not installed"
)
class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE:
            raise AssertionError(
                "REQUIRE_KYVERNO is set but the kyverno CLI is not on PATH"
            )
        cls.ky = kr.Kyverno()

    def test_the_cli_tells_allow_from_deny(self):
        self.ky.calibrate()

    def test_every_kyverno_cell_of_the_matrix_agrees(self):
        problems = kr.replay_matrix(self.ky)
        self.assertEqual(problems, [])
        # 2 responders x (3 scale requests + evict) x 4 cells in nexus-dev and nexus-prod x 3 Kill Switch states
        self.assertEqual(self.ky.seen["kyverno"], 96)

    def test_the_approval_path_k5(self):
        self.assertEqual(kr.replay_approval_path(self.ky), [])


if __name__ == "__main__":
    unittest.main()
