#!/usr/bin/env python3
"""prompt/v2 characters per token, measured on a recorded call (ADR-042 amendment). Offline.

  python experiments/calibration/prompt_ratio.py BUNDLE_JSON PROMPT_TOKENS \\
      [--chunks rb-gray-failure#1,rb-gray-failure#2] [--level L3] [--corpus-hash HEX]

Rebuilds the prompt the Reasoner sent with prompt.plan(), from the stored bundle (the Flight
Recorder's bundle of that Incident, as the operator sent it) and the chunks the call retrieved
(status.reasoner.chunks; retrieval is re-run when --chunks is left out). PROMPT_TOKENS is the
provider's own count, status.reasoner.usage.promptTokens. Prints the prompt's characters (system
plus user), the ratio, the ratio rounded down to one decimal, and the trim level and estimate at
the CHARS_PER_TOKEN in prompt.py. Reads files only; nothing is sent.

The provider's count also includes its chat template, a few tokens: the ratio is a slight
under-estimate of characters per token, which errs on the cautious side.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "reasoner", REPO / "operator", REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from nexus_reasoner import prompt
from nexus_reasoner.retrieval import Retriever

from experiments.harness import bm25

CATALOGUE_VERSION = "1"  # what the operator sends (reasoner_client.CATALOGUE_VERSION)
SCORE_FLOOR = 3.1739  # the live Reasoner's scoreFloor (/healthz, 2026-11-19)


def measure(
    bundle: dict, prompt_tokens: int, chunk_ids: list[str] | None, level: str
) -> dict:
    retriever = Retriever(bm25.Index.from_dir(REPO / "runbooks"), SCORE_FLOOR)
    if chunk_ids is None:
        chunks = retriever.retrieve(bundle)
    else:
        missing = [c for c in chunk_ids if c not in retriever._by_id]
        if missing:
            raise SystemExit(f"chunks not in the index: {missing}")
        chunks = [retriever._by_id[c] for c in chunk_ids]
    p = prompt.plan(bundle, chunks, level, CATALOGUE_VERSION)
    chars = len(p.system) + len(p.user)
    ratio = chars / prompt_tokens
    return {
        "corpusHash": retriever.corpus_hash,
        "chunks": [c["id"] for c in chunks],
        "chars": chars,
        "promptTokens": prompt_tokens,
        "ratio": round(ratio, 4),
        "ratioFloor1": math.floor(ratio * 10) / 10,
        "atCharsPerToken": prompt.CHARS_PER_TOKEN,
        "trimLevel": p.level,
        "tokensEstimate": p.tokens_estimate,
        "selection": p.selection,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle", type=Path)
    ap.add_argument("prompt_tokens", type=int)
    ap.add_argument(
        "--chunks", help="comma-separated chunk ids (status.reasoner.chunks)"
    )
    ap.add_argument("--level", default="L3", choices=("L0", "L1", "L2", "L3"))
    ap.add_argument(
        "--corpus-hash", help="the call's indexHash; refused when it differs"
    )
    a = ap.parse_args(argv)
    if a.prompt_tokens <= 0:
        ap.error("PROMPT_TOKENS must be positive")
    bundle = json.loads(a.bundle.read_text())
    ids = a.chunks.split(",") if a.chunks else None
    out = measure(bundle, a.prompt_tokens, ids, a.level)
    if a.corpus_hash and a.corpus_hash.removeprefix("sha256:") != out["corpusHash"]:
        print(f"corpus hash {out['corpusHash']} is not the call's", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
