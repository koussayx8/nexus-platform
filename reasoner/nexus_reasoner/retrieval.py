"""Query builder and BM25 retriever over the baked index (spec section 15, phases 3 and 4).

bm25.py is the harness's, unchanged: one implementation for offline evaluation and the service.
The index artifact is loaded once and its corpus hash verified against its chunks; /healthz
returns that hash and every proposal's metadata records it.
"""

from __future__ import annotations

import json
from pathlib import Path

from experiments.harness import bm25


class Retriever:
    def __init__(self, index: bm25.Index, floor: float):
        self.index = index
        self.floor = floor
        self.corpus_hash = index.corpus_hash
        self._by_id = {c["id"]: c for c in index.chunks}

    @classmethod
    def load(cls, path: str, floor: float) -> Retriever:
        art = json.loads(Path(path).read_text())
        return cls(bm25.Index.from_artifact(art), floor)

    def retrieve(self, bundle: dict) -> list[dict]:
        """Top 3 chunks (full records) at or above the floor; [] is `no_runbook`."""
        hits = self.index.retrieve(bm25.build_query(bundle), self.floor)
        return [{**self._by_id[h["id"]], "score": h["score"]} for h in hits]
