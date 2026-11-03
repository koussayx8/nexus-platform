"""Runbook lint and index build (spec section 15, phases 1 to 3). Standard library only.

    python -m experiments.harness.rag lint  [--base <git rev>] [runbooks/]
    python -m experiments.harness.rag index [--out build/index] [runbooks/]

`lint` applies the four section 15 rules: a missing front-matter field, a duplicate id, an
unchanged version on changed content (against --base), and an `allowed_actions` entry outside the
Action Catalogue. It also requires the five body sections and a file name equal to the id.
`index` runs the lint's static rules, then writes index.json and manifest.json; the same input
gives byte-identical output, and the corpus hash is printed. Ingestion and BM25 are bm25.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from . import bm25
from .operator_lib import catalogue

SECTIONS = ["Symptoms", "Diagnosis", "Decision", "Action", "Escalation"]
LIST_FIELDS = ("fault_classes", "signals", "allowed_actions", "forbidden_actions")
DEFAULT_DIR = Path("runbooks")


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rag")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("lint")
    lp.add_argument("--base", help="git revision to compare versions against")
    lp.add_argument("dir", nargs="?", type=Path, default=DEFAULT_DIR)
    ip = sub.add_parser("index")
    ip.add_argument("--out", type=Path, default=Path("build/index"))
    ip.add_argument("dir", nargs="?", type=Path, default=DEFAULT_DIR)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "lint":
            errs = lint_dir(a.dir, a.base)
            for e in errs:
                print(f"error: {e}", file=sys.stderr)
            print(f"lint: {len(errs)} error(s)")
            return 1 if errs else 0
        m = build_index(a.dir, a.out)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"index: {m['chunk_count']} chunks, corpus_hash {m['corpus_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
