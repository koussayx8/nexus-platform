"""Runbook lint and index build (spec section 15, phases 1 to 3). Standard library only.

    python -m experiments.harness.rag lint  [--base <git rev>] [runbooks/]
    python -m experiments.harness.rag index [--out build/index] [runbooks/]
    python -m experiments.harness.rag eval  [--write experiments/retrieval/results.json]

`lint` applies the four section 15 rules: a missing front-matter field, a duplicate id, an
unchanged version on changed content (against --base), and an `allowed_actions` entry outside the
Action Catalogue. It also requires the five body sections and a file name equal to the id.
`eval` scores retrieval on the labelled queries (spec section 15, "Retrieval evaluation").
`index` runs the lint's static rules, then writes index.json and manifest.json; the same input
gives byte-identical output, and the corpus hash is printed. Ingestion and BM25 are bm25.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

from . import bm25
from .operator_lib import catalogue

SECTIONS = ["Symptoms", "Diagnosis", "Decision", "Action", "Escalation"]
LIST_FIELDS = ("fault_classes", "signals", "allowed_actions", "forbidden_actions")
DEFAULT_DIR = Path("runbooks")
QUERIES = Path("experiments/retrieval/queries.json")
RESULTS = Path("experiments/retrieval/results.json")
NO_RUNBOOK = "no_runbook"
S4_QUERY, S6_QUERY = "q10", "q12"  # S6 is S4 plus injected log text


def lint_text(name: str, src: str) -> tuple[dict | None, list[str]]:
    """Static rules for one file: (front matter or None, errors)."""
    try:
        meta, body = bm25.parse_front_matter(src)
    except ValueError as e:
        return None, [f"{name}: {e}"]
    errs = []
    if meta["id"] + ".md" != name:
        errs.append(f"{name}: id {meta['id']!r} does not match the file name")
    if not re.fullmatch(r"[1-9][0-9]*", meta["version"]):
        errs.append(f"{name}: version {meta['version']!r} is not a positive integer")
    for f in LIST_FIELDS:
        if not isinstance(meta[f], list):
            errs.append(f"{name}: {f} must be a [list]")
    fcs = meta["fault_classes"] if isinstance(meta["fault_classes"], list) else []
    if not fcs:
        errs.append(f"{name}: fault_classes is empty")
    for fc in fcs:
        if fc not in catalogue.FAULT_CLASSES:
            errs.append(f"{name}: fault class {fc!r} is not in the proposal enum")
    allowed = meta["allowed_actions"]
    for a in allowed if isinstance(allowed, list) else []:
        if a not in catalogue.CATALOGUE:
            errs.append(f"{name}: allowed action {a!r} is not in the Action Catalogue")
    if isinstance(allowed, list) and not allowed:
        errs.append(f"{name}: allowed_actions is empty")
    heads = re.findall(r"^## +(.+?)\s*$", body, flags=re.MULTILINE)
    if heads != SECTIONS:
        errs.append(f"{name}: sections are {heads}, expected {SECTIONS}")
    return meta, errs


def _git_show(base: str, path: Path) -> str | None:
    r = subprocess.run(
        ["git", "show", f"{base}:{path.as_posix()}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout if r.returncode == 0 else None


def lint_dir(directory: Path, base: str | None = None) -> list[str]:
    errs: list[str] = []
    files = sorted(directory.glob("rb-*.md"))
    if not files:
        return [f"{directory}: no rb-*.md files"]
    ids: dict[str, str] = {}
    for f in files:
        src = f.read_text()
        meta, e = lint_text(f.name, src)
        errs += e
        if meta is None:
            continue
        if meta["id"] in ids:
            errs.append(f"{f.name}: duplicate id {meta['id']} (also {ids[meta['id']]})")
        ids[meta["id"]] = f.name
        if base:
            old = _git_show(base, f)
            if old is not None and old != src:
                om, _ = lint_text(f.name, old)
                if om is not None and om["version"] == meta["version"]:
                    errs.append(
                        f"{f.name}: content changed since {base} but version is still {meta['version']}"
                    )
    return errs


def build_index(directory: Path, out: Path) -> dict:
    errs = lint_dir(directory)
    if errs:
        raise ValueError("lint failed:\n" + "\n".join(errs))
    idx = bm25.Index.from_dir(directory)
    out.mkdir(parents=True, exist_ok=True)
    dump = {"indent": 1, "sort_keys": True, "ensure_ascii": False}
    (out / "index.json").write_text(json.dumps(idx.artifact(), **dump) + "\n")
    (out / "manifest.json").write_text(json.dumps(idx.manifest(), **dump) + "\n")
    return idx.manifest()


def _rank(idx: bm25.Index, bundle: dict) -> list[dict]:
    """Every chunk, best first, ties by chunk id; no floor."""
    return idx.retrieve(bm25.build_query(bundle), floor=0.0)


def _full_ranking(idx: bm25.Index, bundle: dict) -> list[tuple[str, float]]:
    by_id = {c["id"]: c["runbook"] for c in idx.chunks}
    ranked = sorted(idx.score(bm25.build_query(bundle)), key=lambda x: (-x[1], x[0]))
    return [(by_id[cid], sc) for cid, sc in ranked]


def evaluate(idx: bm25.Index, queries: list[dict]) -> dict:
    """Recall@1, Recall@3 and MRR on in-corpus queries, false-retrieval rate on out-of-corpus
    queries, and a proposed floor. A query is correct at rank k when one of the first k chunks
    belongs to the labelled runbook. MRR uses the first such chunk over the whole ranking.
    The floor sweep tries 0 and the midpoints between observed top-1 scores; the proposal maximises
    Recall@1-at-floor + (1 - false-retrieval-rate-at-floor). It is a proposal: the owner freezes it."""
    rows = []
    for q in queries:
        ranking = _full_ranking(idx, q["bundle"])
        ranks = [i for i, (rb, _) in enumerate(ranking, 1) if rb == q["label"]]
        first = ranks[0] if ranks else None
        rows.append(
            {
                "id": q["id"],
                "label": q["label"],
                "top1_runbook": ranking[0][0] if ranking else None,
                "top1_score": round(ranking[0][1], 4) if ranking else 0.0,
                "top3": [rb for rb, _ in ranking[:3]],
                "first_correct_rank": first,
            }
        )
    inc = [r for r in rows if r["label"] != NO_RUNBOOK]
    ooc = [r for r in rows if r["label"] == NO_RUNBOOK]
    n = len(inc)
    metrics = {
        "in_corpus_queries": n,
        "out_of_corpus_queries": len(ooc),
        "recall_at_1": sum(r["first_correct_rank"] == 1 for r in inc) / n,
        "recall_at_3": sum((r["first_correct_rank"] or 99) <= 3 for r in inc) / n,
        "mrr": sum(1 / r["first_correct_rank"] for r in inc if r["first_correct_rank"])
        / n,
        "false_retrieval_rate_no_floor": sum(r["top1_score"] > 0 for r in ooc)
        / len(ooc),
    }

    def decide(floor: float, r: dict) -> bool:
        return r["top1_score"] >= floor and r["top1_score"] > 0

    scores = sorted({r["top1_score"] for r in rows})
    cands = [0.0, *((a + b) / 2 for a, b in pairwise(scores)), scores[-1] + 1]
    sweep = []
    for f in cands:
        f = round(f, 4)
        r1 = sum(decide(f, r) and r["first_correct_rank"] == 1 for r in inc) / n
        frr = sum(decide(f, r) for r in ooc) / len(ooc)
        sweep.append(
            {
                "floor": f,
                "recall_at_1": round(r1, 4),
                "false_retrieval_rate": round(frr, 4),
                "balanced": round(r1 + (1 - frr), 4),
            }
        )
    # Class-balanced: 24 in-corpus against 6 out-of-corpus queries, so plain accuracy would
    # favour never abstaining. The lowest floor wins a tie.
    best = max(sweep, key=lambda x: (x["balanced"], -x["floor"]))
    metrics["proposed_floor"] = best["floor"]
    metrics["recall_at_1_at_floor"] = best["recall_at_1"]
    metrics["false_retrieval_rate_at_floor"] = best["false_retrieval_rate"]
    by = {r["id"]: r for r in rows}
    s4, s6 = by.get(S4_QUERY), by.get(S6_QUERY)
    inj = None
    if s4 and s6:
        inj = {
            "s4_query": S4_QUERY,
            "s6_query": S6_QUERY,
            "s4_top3": s4["top3"],
            "s6_top3": s6["top3"],
            "top3_changed": s4["top3"] != s6["top3"],
            "top1_changed": s4["top1_runbook"] != s6["top1_runbook"],
        }
    return {"metrics": metrics, "floor_sweep": sweep, "s6_vs_s4": inj, "queries": rows}


def run_eval(write: Path | None) -> dict:
    qraw = QUERIES.read_bytes()
    qs = json.loads(qraw)["queries"]
    idx = bm25.Index.from_dir(DEFAULT_DIR)
    res = {
        "schema": "retrieval-results/v1",
        "corpus_hash": idx.corpus_hash,
        "queries_sha256": hashlib.sha256(qraw).hexdigest(),
        "k1": bm25.K1,
        "b": bm25.B,
        **evaluate(idx, qs),
    }
    if write:
        write.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rag")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("lint")
    lp.add_argument("--base", help="git revision to compare versions against")
    lp.add_argument("dir", nargs="?", type=Path, default=DEFAULT_DIR)
    ip = sub.add_parser("index")
    ip.add_argument("--out", type=Path, default=Path("build/index"))
    ip.add_argument("dir", nargs="?", type=Path, default=DEFAULT_DIR)
    ep = sub.add_parser("eval")
    ep.add_argument("--write", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "lint":
            errs = lint_dir(a.dir, a.base)
            for e in errs:
                print(f"error: {e}", file=sys.stderr)
            print(f"lint: {len(errs)} error(s)")
            return 1 if errs else 0
        if a.cmd == "eval":
            res = run_eval(a.write)
            print(json.dumps({**res["metrics"], "s6_vs_s4": res["s6_vs_s4"]}, indent=1))
            return 0
        m = build_index(a.dir, a.out)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"index: {m['chunk_count']} chunks, corpus_hash {m['corpus_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
