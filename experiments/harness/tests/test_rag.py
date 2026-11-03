"""Offline tests for rag.py (lint, index) and the real runbooks/ corpus."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from .. import bm25, rag
from ..operator_lib import catalogue

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "runbooks"
EXPECTED = {
    "rb-gray-failure", "rb-cpu-saturation", "rb-bad-rollout", "rb-traffic-spike",
    "rb-image-pull", "rb-cert-expiry", "rb-dns-failure", "rb-disk-pressure",
}  # fmt: skip
GOOD = (CORPUS / "rb-traffic-spike.md").read_text()


class CorpusTest(unittest.TestCase):
    def test_eight_runbooks_lint_clean(self):
        self.assertEqual({p.stem for p in CORPUS.glob("rb-*.md")}, EXPECTED)
        self.assertEqual(rag.lint_dir(CORPUS), [])

    def test_no_s5_runbook(self):
        for c in bm25.Index.from_dir(CORPUS).chunks:
            self.assertNotIn("dependency_failure", c["fault_classes"])
            self.assertNotIn("dependency", c["runbook"])

    def test_allowed_actions_match_spec_table(self):
        allowed = {}
        for p in CORPUS.glob("rb-*.md"):
            meta, _ = bm25.parse_front_matter(p.read_text())
            allowed[meta["id"]] = set(meta["allowed_actions"])
        self.assertEqual(allowed["rb-gray-failure"], {"restart_workload", "escalate"})
        self.assertEqual(allowed["rb-cpu-saturation"], {"scale_deployment", "escalate"})
        self.assertEqual(allowed["rb-bad-rollout"], {"escalate"})
        self.assertEqual(allowed["rb-traffic-spike"], {"no_action"})
        for rb in EXPECTED - {
            "rb-gray-failure",
            "rb-cpu-saturation",
            "rb-traffic-spike",
        }:
            self.assertLessEqual(allowed[rb], {"escalate"})

    def test_five_sections_each(self):
        idx = bm25.Index.from_dir(CORPUS)
        self.assertEqual(len(idx.chunks), 40)
        for h in ("Decision", "Action"):
            self.assertEqual(sum(c["heading"] == h for c in idx.chunks), 8)
        self.assertEqual(idx.chunks[0]["id"], "rb-bad-rollout#1")  # rb-<id>#<n>, sorted


class LintTest(unittest.TestCase):
    def lint(self, src, name="rb-traffic-spike.md"):
        return rag.lint_text(name, src)[1]

    def test_good_file_clean(self):
        self.assertEqual(self.lint(GOOD), [])

    def test_missing_field(self):
        self.assertTrue(self.lint(GOOD.replace("signals:", "sigs:")))

    def test_action_outside_catalogue(self):
        bad = GOOD.replace(
            "allowed_actions: [no_action]", "allowed_actions: [delete_deployment]"
        )
        self.assertTrue(any("Action Catalogue" in e for e in self.lint(bad)))
        self.assertNotIn("delete_deployment", catalogue.CATALOGUE)

    def test_sections_and_name(self):
        self.assertTrue(self.lint(GOOD.replace("## Escalation", "## Other")))
        self.assertTrue(self.lint(GOOD, "rb-other.md"))

    def test_duplicate_id(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "rb-traffic-spike.md").write_text(GOOD)
            (Path(d) / "rb-copy.md").write_text(GOOD)
            self.assertTrue(any("duplicate id" in e for e in rag.lint_dir(Path(d))))

    def test_unchanged_version_on_changed_content(self):
        changed = GOOD.replace("do nothing", "do nothing at all")
        with tempfile.TemporaryDirectory() as d:

            def git(*a):
                subprocess.run(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                    cwd=d, check=True, capture_output=True,
                )  # fmt: skip

            git("init", "-q")
            f = Path(d) / "runbooks" / "rb-traffic-spike.md"
            f.parent.mkdir()
            f.write_text(GOOD)
            git("add", ".")
            git("commit", "-q", "-m", "base")
            cwd = os.getcwd()
            os.chdir(d)
            try:
                lint = lambda: rag.lint_dir(Path("runbooks"), "HEAD")
                f.write_text(changed)
                self.assertTrue(any("version" in e for e in lint()))
                f.write_text(changed.replace("version: 1", "version: 2"))
                self.assertEqual(lint(), [])
                f.write_text(GOOD)
                self.assertEqual(lint(), [])
            finally:
                os.chdir(cwd)


class IndexTest(unittest.TestCase):
    def test_build_is_deterministic_and_loadable(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            m1 = rag.build_index(CORPUS, Path(d1))
            rag.build_index(CORPUS, Path(d2))
            for n in ("index.json", "manifest.json"):
                self.assertEqual(
                    (Path(d1) / n).read_bytes(), (Path(d2) / n).read_bytes()
                )
            idx = bm25.Index.from_artifact(
                json.loads((Path(d1) / "index.json").read_text())
            )
            self.assertEqual(idx.corpus_hash, m1["corpus_hash"])
            self.assertEqual((m1["k1"], m1["b"], m1["chunk_count"]), (1.5, 0.75, 40))
            self.assertEqual(len(m1["corpus_hash"]), 64)

    def test_tampered_artifact_rejected(self):
        art = bm25.Index.from_dir(CORPUS).artifact()
        art["chunks"][0]["text"] += " injected"
        with self.assertRaises(ValueError):
            bm25.Index.from_artifact(art)

    def test_index_refuses_failing_lint(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "rb-traffic-spike.md").write_text(GOOD.replace("signals:", "x:"))
            with self.assertRaises(ValueError):
                rag.build_index(Path(d), Path(d) / "out")


class RetrievalEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (REPO / "experiments/retrieval/queries.json").read_bytes()
        cls.queries = json.loads(cls.raw)["queries"]
        cls.idx = bm25.Index.from_dir(CORPUS)

    def test_query_set_shape(self):
        labels = [q["label"] for q in self.queries]
        self.assertEqual(len(labels), 30)
        self.assertEqual(len({q["id"] for q in self.queries}), 30)
        for rb in EXPECTED:
            self.assertEqual(labels.count(rb), 3, rb)
        self.assertEqual(labels.count("no_runbook"), 6)

    def test_metrics_on_a_known_ranking(self):
        # Two in-corpus queries (hit at rank 1, hit at rank 2) and one out-of-corpus.
        qs = [
            {"id": "a", "label": "rb-traffic-spike", "bundle": {"incident": {"alerts": [{"name": "NexusTrafficAnomaly"}]}}},
            {"id": "b", "label": "rb-gray-failure", "bundle": {"incident": {"alerts": [{"name": "NexusTrafficAnomaly"}]}}},
            {"id": "c", "label": "no_runbook", "bundle": {}},
        ]  # fmt: skip
        m = rag.evaluate(self.idx, qs)["metrics"]
        self.assertEqual(m["recall_at_1"], 0.5)
        self.assertEqual(m["false_retrieval_rate_no_floor"], 0.0)  # empty query: no hit
        self.assertLessEqual(m["mrr"], 1.0)
        self.assertGreater(m["mrr"], 0.5)

    def test_committed_results_are_current(self):
        res = json.loads((REPO / "experiments/retrieval/results.json").read_text())
        self.assertEqual(res["corpus_hash"], self.idx.corpus_hash)
        self.assertEqual(res["queries_sha256"], hashlib.sha256(self.raw).hexdigest())
        again = rag.evaluate(self.idx, self.queries)
        self.assertEqual(res["metrics"], again["metrics"])
        self.assertEqual(res["queries"], again["queries"])

    def test_floor_makes_no_runbook(self):
        floor = json.loads((REPO / "experiments/retrieval/results.json").read_text())[
            "metrics"
        ]["proposed_floor"]
        empty = self.idx.retrieve([], floor)
        self.assertEqual(empty, [])


if __name__ == "__main__":
    unittest.main()
