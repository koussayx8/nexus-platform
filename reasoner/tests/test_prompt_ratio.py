"""experiments/calibration/prompt_ratio.py: the chars-per-token measurement (ADR-042 amendment)."""

import importlib.util
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from nexus_reasoner import prompt

from .helpers import REPO
from .test_prompt_budget import live_bundle

spec = importlib.util.spec_from_file_location(
    "prompt_ratio", REPO / "experiments/calibration/prompt_ratio.py"
)
prompt_ratio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompt_ratio)

CHUNKS = ["rb-gray-failure#1", "rb-gray-failure#2"]


class Ratio(unittest.TestCase):
    def test_the_ratio_is_the_rebuilt_prompts_characters_over_the_providers_count(self):
        b = live_bundle()
        out = prompt_ratio.measure(b, 5134, CHUNKS, "L3")
        self.assertEqual(out["chunks"], CHUNKS)
        p = prompt.plan(b, [prompt_ratio.Retriever(prompt_ratio.bm25.Index.from_dir(REPO / "runbooks"), 0)._by_id[c] for c in CHUNKS], "L3", "1")  # fmt: skip
        self.assertEqual(out["chars"], len(p.system) + len(p.user))
        self.assertAlmostEqual(out["ratio"], out["chars"] / 5134, places=3)
        self.assertEqual(out["ratioFloor1"], math.floor(out["chars"] / 5134 * 10) / 10)
        self.assertEqual(out["trimLevel"], p.level)

    def test_an_unknown_chunk_is_refused(self):
        with self.assertRaises(SystemExit):
            prompt_ratio.measure(live_bundle(), 5000, ["rb-nope#1"], "L3")

    def test_the_cli_refuses_another_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "b.json"
            f.write_text(json.dumps(live_bundle()))
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = prompt_ratio.main([str(f), "5000", "--chunks", ",".join(CHUNKS),
                                        "--corpus-hash", "sha256:" + "0" * 64])  # fmt: skip
            self.assertEqual(rc, 1)
            self.assertIn("is not the call's", err.getvalue())


if __name__ == "__main__":
    unittest.main()
