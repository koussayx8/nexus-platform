"""BM25 Okapi over the runbooks (spec section 15, phases 2 to 4). Deterministic, no
dependency, no vector store. k1 = 1.5, b = 0.75.

Ingestion: strip Markdown syntax, keep Kubernetes tokens (CrashLoopBackOff, OOMKilled)
whole, chunk at second-level headings, never splitting Decision or Action sections.
Chunk id `rb-<id>#<n>`. The index carries a SHA-256 corpus hash. Retrieval returns the
top 3 above a score floor, ties broken by chunk id; no hit above the floor is `no_runbook`.

The query is built from evidence fields only, never from text a model wrote.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

K1 = 1.5
B = 0.75
TOP_K = 3
MAX_CHUNK_TOKENS = 200  # "about"; Decision and Action sections are never split.
NEVER_SPLIT = ("decision", "action")
REQUIRED_FIELDS = (
    "id", "title", "version", "fault_classes", "signals",
    "allowed_actions", "forbidden_actions", "updated",
)  # fmt: skip

# Keeps CrashLoopBackOff whole; lower-cased, so it matches the same token in a query.
_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
    ]
)


def tokenize(text: str) -> list[str]:
    return [
        t for t in (m.group(0).lower() for m in _TOKEN.finditer(text)) if t not in _STOP
    ]


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", lambda m: m.group(0).strip("`"), text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^[#>\-\*\s]+", "", text, flags=re.MULTILINE)
    return re.sub(r"[*_~|]", " ", text)


def parse_front_matter(src: str) -> tuple[dict, str]:
    """Minimal `key: value` / `key: [a, b]` front matter (YAML subset; no dependency)."""
    m = re.match(r"---\n(.*?)\n---\n(.*)", src, flags=re.DOTALL)
    if not m:
        raise ValueError("missing front matter")
    meta: dict = {}
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k.strip()] = v
    missing = [f for f in REQUIRED_FIELDS if f not in meta]
    if missing:
        raise ValueError(f"front matter missing {missing}")
    return meta, m.group(2)


def chunk_runbook(src: str) -> list[dict]:
    meta, body = parse_front_matter(src)
    sections = re.split(r"^## +", body, flags=re.MULTILINE)[1:]
    chunks = []
    for n, sec in enumerate(sections, 1):
        head, _, text = sec.partition("\n")
        head = head.strip()
        words = strip_markdown(text).split()
        # Whole section is one chunk; an oversize section is split on paragraph
        # boundaries unless it is a Decision or Action section.
        parts = [strip_markdown(text)]
        if len(words) > MAX_CHUNK_TOKENS and head.lower() not in NEVER_SPLIT:
            parts = _split_paragraphs(text)
        for p in parts:
            chunks.append(
                {
                    "id": f"rb-{meta['id'].removeprefix('rb-')}#{len(chunks) + 1}",
                    "runbook": meta["id"],
                    "version": meta["version"],
                    "fault_classes": meta["fault_classes"],
                    "heading": head,
                    "text": f"{head} {p}",
                }
            )
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    out, cur = [], []
    for para in re.split(r"\n\s*\n", text):
        para = strip_markdown(para)
        if cur and len(" ".join(cur + [para]).split()) > MAX_CHUNK_TOKENS:
            out.append(" ".join(cur))
            cur = []
        cur.append(para)
    if cur:
        out.append(" ".join(cur))
    return out


class Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = sorted(chunks, key=lambda c: c["id"])
        self.docs = [Counter(tokenize(c["text"])) for c in self.chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        # The "+1" inside the log keeps IDF positive (Lucene's BM25).
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.corpus_hash = hashlib.sha256(
            json.dumps(
                [[c["id"], c["version"], c["text"]] for c in self.chunks]
            ).encode()
        ).hexdigest()

    @classmethod
    def from_dir(cls, path: Path) -> Index:
        chunks, seen = [], set()
        for f in sorted(Path(path).glob("rb-*.md")):
            cs = chunk_runbook(f.read_text())
            if cs[0]["runbook"] in seen:
                raise ValueError(f"duplicate runbook id {cs[0]['runbook']}")
            seen.add(cs[0]["runbook"])
            chunks += cs
        return cls(chunks)

    def score(self, query_tokens: list[str]) -> list[tuple[str, float]]:
        out = []
        for c, d, ln in zip(self.chunks, self.docs, self.lens, strict=True):
            s = 0.0
            for t in set(query_tokens):
                f = d.get(t, 0)
                if not f:
                    continue
                s += (
                    self.idf[t]
                    * f
                    * (K1 + 1)
                    / (f + K1 * (1 - B + B * ln / (self.avg or 1)))
                )
            out.append((c["id"], s))
        return out

    def retrieve(self, query_tokens: list[str], floor: float) -> list[dict]:
        """Top 3 at or above the floor; descending score, ties by chunk id. [] is `no_runbook`."""
        ranked = sorted(self.score(query_tokens), key=lambda x: (-x[1], x[0]))
        by_id = {c["id"]: c for c in self.chunks}
        return [
            {"id": cid, "score": s, "runbook": by_id[cid]["runbook"]}
            for cid, s in ranked[:TOP_K]
            if s >= floor and s > 0
        ]


def build_query(bundle: dict) -> list[str]:
    """Query tokens from evidence fields only: alert names, waiting/termination reasons, event
    reasons, up to ten log error tokens by frequency, anomalous metric names, rollout state.
    No free text from a model is read."""
    q: list[str] = []
    for a in bundle.get("incident", {}).get("alerts", []):
        q += tokenize(a["name"])
    for p in bundle.get("pods", []):
        for c in p.get("containers", []):
            q += tokenize(c.get("reason") or "")
            q += tokenize(c.get("lastTermination") or "")
    for e in bundle.get("events", []):
        q += tokenize(e.get("reason") or "")
    lines = bundle.get("logs", {}).get("current", []) + bundle.get("logs", {}).get(
        "previous", []
    )
    errs = Counter(
        t
        for ln in lines
        for t in tokenize(ln)
        if re.search(r"error|fail|refused|timeout|denied|oom", t)
    )
    q += [t for t, _ in sorted(errs.items(), key=lambda x: (-x[1], x[0]))[:10]]
    for name, z in bundle.get("metrics", {}).get("zscores", {}).items():
        if abs(z) > 3:
            q += tokenize(name)
    if bundle.get("workload", {}).get("rolloutInProgress"):
        q += ["rollout"]
    return q
