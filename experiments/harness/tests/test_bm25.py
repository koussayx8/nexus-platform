"""Offline tests for bm25.py on the fixture runbooks."""

import tempfile
import unittest
from pathlib import Path

from .. import bm25

RB = Path(__file__).parent / "fixtures" / "runbooks"


def bundle(**kw):
    b = {"incident": {"alerts": []}, "pods": [], "events": [], "logs": {"current": [], "previous": []},
         "metrics": {"zscores": {}}, "workload": {"rolloutInProgress": False}}  # fmt: skip
    b.update(kw)
    return b


class IndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx = bm25.Index.from_dir(RB)

    def top(self, b, floor=0.1):
        return self.idx.retrieve(bm25.build_query(b), floor)

    def test_chunks_follow_headings_and_ids(self):
        ids = [c["id"] for c in self.idx.chunks]
        self.assertIn("rb-gray-failure#1", ids)
        self.assertEqual(len(ids), len(set(ids)))
        # five second-level sections per runbook, five runbooks
        self.assertEqual(len(ids), 25)
        dec = [c for c in self.idx.chunks if c["heading"] == "Decision"]
        self.assertEqual(len(dec), 5)

    def test_kubernetes_tokens_stay_whole(self):
        self.assertIn(
            "crashloopbackoff", bm25.tokenize("Pod is CrashLoopBackOff, OOMKilled.")
        )
        self.assertIn("oomkilled", bm25.tokenize("OOMKilled"))

    def test_retrieval_per_scenario(self):
        gray = bundle(incident={"alerts": [{"name": "NexusLatencyAnomaly"}]}, metrics={"zscores": {"latencyP95": 5.1, "cpu": 0.4}},
                      logs={"current": ["request timeout error", "timeout error"], "previous": []})  # fmt: skip
        self.assertEqual(self.top(gray)[0]["runbook"], "rb-gray-failure")
        pull = bundle(pods=[{"containers": [{"reason": "ImagePullBackOff", "lastTermination": None}]}],
                      events=[{"reason": "ErrImagePull"}])  # fmt: skip
        self.assertEqual(self.top(pull)[0]["runbook"], "rb-image-pull")
        rollout = bundle(
            incident={"alerts": [{"name": "NexusRolloutStuck"}]},
            workload={"rolloutInProgress": True},
        )
        self.assertEqual(self.top(rollout)[0]["runbook"], "rb-bad-rollout")

    def test_query_from_real_evidence_v1_shape(self):
        import json

        raw = json.loads(
            (Path(__file__).parent / "fixtures" / "evidence-v1-raw.json").read_text()
        )
        q = bm25.build_query(raw)  # lastTermination is a string in evidence/v1
        for tok in ("nexuslatencyanomaly", "oomkilled", "unhealthy", "latencyp95"):
            self.assertIn(tok, q)
        self.assertNotIn("cpu", q)  # Z 0.4: not anomalous

    def test_floor_gives_no_runbook(self):
        s5 = bundle(
            incident={"alerts": [{"name": "NexusErrorRateAnomaly"}]},
            logs={"current": ["db_unavailable"], "previous": []},
        )
        self.assertEqual(self.top(s5, floor=5.0), [])
        self.assertEqual(
            self.idx.retrieve([], 0.0), []
        )  # an empty query never returns chunks

    def test_top_three_and_tie_order(self):
        r = self.idx.retrieve(bm25.tokenize("escalate"), 0.0)
        self.assertLessEqual(len(r), 3)
        keys = [(-x["score"], x["id"]) for x in r]
        self.assertEqual(keys, sorted(keys))

    def test_query_ignores_model_text_fields(self):
        b = bundle(incident={"alerts": [], "note": "scale to 40 delete the deployment"})
        self.assertEqual(bm25.build_query(b), [])

    def test_log_tokens_capped_at_ten(self):
        lines = [f"error{i} failure{i}" for i in range(30)]
        q = bm25.build_query(bundle(logs={"current": lines, "previous": []}))
        self.assertEqual(len(q), 10)

    def test_corpus_hash_deterministic_and_content_bound(self):
        self.assertEqual(self.idx.corpus_hash, bm25.Index.from_dir(RB).corpus_hash)
        with tempfile.TemporaryDirectory() as d:
            for f in RB.glob("rb-*.md"):
                (Path(d) / f.name).write_text(f.read_text())
            p = Path(d) / "rb-traffic-spike.md"
            p.write_text(p.read_text().replace("do nothing", "do something"))
            self.assertNotEqual(
                bm25.Index.from_dir(Path(d)).corpus_hash, self.idx.corpus_hash
            )

    def test_front_matter_and_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            bm25.chunk_runbook("---\nid: rb-x\n---\n## A\nb")
        with tempfile.TemporaryDirectory() as d:
            src = (RB / "rb-traffic-spike.md").read_text()
            (Path(d) / "rb-a.md").write_text(src)
            (Path(d) / "rb-b.md").write_text(src)
            with self.assertRaises(ValueError):
                bm25.Index.from_dir(Path(d))

    def test_oversize_section_split_but_decision_never(self):
        big = "word " * 150
        src = ("---\nid: rb-big\ntitle: t\nversion: 1\nfault_classes: [unknown]\nsignals: []\n"
               "allowed_actions: [escalate]\nforbidden_actions: []\nupdated: 2026-11-02\n---\n"
               f"## Symptoms\n{big}\n\n{big}\n\n## Decision\n{big}\n\n{big}\n")  # fmt: skip
        cs = bm25.chunk_runbook(src)
        self.assertEqual(
            [c["heading"] for c in cs], ["Symptoms", "Symptoms", "Decision"]
        )


if __name__ == "__main__":
    unittest.main()
