#!/usr/bin/env python3
"""G13: export one run to experiments/runs/<run>/ (spec §14, §25). Offline: files in, a directory out.

  export-results.py --queue STATE_DIR --run-id ID --tables FILE --audit FILE --injection TS \\
      --ground-truth FILE --git-commit SHA [--k8sgpt FILE] [--out experiments/runs]

Inputs (the runner produces them on the host; this script never touches the cluster):
  --queue / --run-id   the batch queue (scripts: experiments.harness.batch) and the attempt to export
  --tables FILE        the Flight Recorder rows of the run's window, one JSON object
                       {table: [row, ...]} for incidents, transitions, evidence_bundles, proposals,
                       gate_decisions, actions, verifications. On the host:
                         psql "$FR_URL" -At -c "SELECT json_build_object('incidents',
                           (SELECT coalesce(json_agg(t), '[]') FROM incidents t WHERE detected_at
                            BETWEEN '<start>' AND '<end>'), ...)" > tables.json
                       Rows outside the run's window are dropped here; `run_id` is attached to
                       the incidents inside it.
  --audit FILE         the API audit log (/var/log/nexus-audit/audit.log, JSON lines); only the
                       operator's events inside the run window are kept (the extract)
  --fixture            mark the run as a FIXTURE (invented data): `make results` then stamps every table
                       and figure "FIXTURE — not measured" and refuses to mix it with measured runs
  --injection TS       when the fault took effect (ArgoCD applied the revision), RFC 3339
  --ground-truth FILE  hashed into the run row: the pre-registered file (spec §20)

Writes, then reconciles (scripts/reconcile-audit.py) and seals:
  run.json           the `runs` row (spec §14) + the queue record + the cell + the injection time
  tables.json       the run's Flight Recorder rows            (valid runs)
  audit.jsonl.gz    the audit extract, gzip with mtime 0       (valid runs)
  reconciliation.json, audit_links.jsonl                       (valid runs)
  k8sgpt.json       the K8sGPT diagnosis, if given
  MANIFEST.json     sha256 of every file above, written last: `make results` refuses a run whose
                    files no longer match it

A void or discarded attempt exports run.json only (discarded=true with its reason): every discard is
counted and reported with its reason (spec §20). A run directory is never overwritten.
Exit 0: exported. 1: exported, but the run does not reconcile (it will not count). 2: refused.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from experiments.harness import batch

SCHEMA = "run-export/v1"
DEPENDENT = (
    "transitions",
    "evidence_bundles",
    "proposals",
    "gate_decisions",
    "actions",
    "verifications",
)
TABLES = ("incidents", *DEPENDENT)


class ExportError(Exception):
    pass


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), HERE / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def slug(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", run_id)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: pathlib.Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def run_row(rec: dict, cell: dict, git_commit: str, gt_hash: str) -> dict:
    """The `runs` row of spec §14 (05-schema.sql), from the queue record and the cell."""
    void_or_discard = rec["status"] in ("void", "discarded")
    return {
        "run_id": rec["run_id"],
        "scenario": cell["scenario"],
        "reasoner": cell["reasoner"],
        "level": int(cell["level"]),
        "repetition": rec["attempt"],
        "git_commit": git_commit,
        "ground_truth_hash": gt_hash,
        "config_snapshot": {"cell": cell, "approval_ref": rec.get("approval_ref")},
        "advisory_checks": rec.get("advisory_checks", "on") != "off",
        "started_at": rec["started_at"],
        "ended_at": rec["ended_at"],
        "discarded": void_or_discard,
        "discard_reason": (rec.get("void_reason") or rec.get("discard_reason"))
        if void_or_discard
        else None,
    }


def export(
    *,
    queue_dir,
    run_id,
    injection,
    ground_truth,
    git_commit,
    out_dir,
    tables=None,
    audit=None,
    k8sgpt=None,
    fixture=False,
):
    aj = _load("audit-join")
    queue = batch.Queue.load(queue_dir)
    rec = next((r for r in queue.runs if r["run_id"] == run_id), None)
    if rec is None:
        raise ExportError(f"no run {run_id!r} in the queue")
    if rec["status"] == "running":
        raise ExportError(f"{run_id} is still running")
    cell = queue.cell(rec["cell"])
    gt = pathlib.Path(ground_truth)
    run = run_row(rec, cell, git_commit, sha256(gt))
    dest = pathlib.Path(out_dir) / slug(run_id)
    if dest.exists():
        raise ExportError(f"{dest} exists: an exported run is never overwritten")
    dest.mkdir(parents=True)
    files = ["run.json"]
    _dump(
        dest / "run.json",
        {
            "schema": SCHEMA,
            "run": run,
            "queue_record": rec,
            "injection_at": injection,
            "fixture": fixture,
        },
    )

    if not run["discarded"]:
        if tables is None or audit is None:
            raise ExportError("a valid run needs --tables and --audit")
        since, until = aj.ts(run["started_at"]), aj.ts(run["ended_at"])
        all_rows = json.loads(pathlib.Path(tables).read_text(encoding="utf-8"))
        incidents = [
            {**i, "run_id": run_id}
            for i in all_rows.get("incidents", [])
            if since <= aj.ts(i["detected_at"]) <= until
        ]
        if not incidents:
            raise ExportError(f"no Incident was detected inside the window of {run_id}")
        uids = {i["incident_uid"] for i in incidents}
        out_tables = {"incidents": incidents}
        for t in DEPENDENT:
            out_tables[t] = [
                r for r in all_rows.get(t, []) if r["incident_uid"] in uids
            ]
        _dump(dest / "tables.json", out_tables)
        events = [
            e
            for e in aj.read_jsonl(str(audit))
            if (e.get("user") or {}).get("username") == aj.OPERATOR
            and since <= aj.ts(e["stageTimestamp"]) <= until
        ]
        with gzip.GzipFile(
            dest / "audit.jsonl.gz", "wb", mtime=0
        ) as f:  # mtime 0: the bytes are reproducible
            f.write(
                "".join(json.dumps(e, sort_keys=True) + "\n" for e in events).encode()
            )
        files += ["tables.json", "audit.jsonl.gz"]
        result = _load("reconcile-audit").reconcile(run, out_tables, events)
        _load("reconcile-audit").write(dest, result)
        files += ["reconciliation.json", "audit_links.jsonl"]
    if k8sgpt:
        _dump(
            dest / "k8sgpt.json",
            json.loads(pathlib.Path(k8sgpt).read_text(encoding="utf-8")),
        )
        files.append("k8sgpt.json")
    _dump(
        dest / "MANIFEST.json",
        {"schema": SCHEMA, "run_id": run_id, "fixture": fixture, "ground_truth_hash": run["ground_truth_hash"],
         "git_commit": git_commit, "files": {f: sha256(dest / f) for f in sorted(files)}},
    )  # fmt: skip
    return dest


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--queue", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--tables")
    p.add_argument("--audit")
    p.add_argument("--injection", required=True)
    p.add_argument("--ground-truth", required=True)
    p.add_argument("--git-commit", required=True)
    p.add_argument("--k8sgpt")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="mark the run as a fixture: every table and figure built from it says so",
    )
    p.add_argument("--out", default="experiments/runs")
    a = p.parse_args(argv)
    try:
        dest = export(queue_dir=a.queue, run_id=a.run_id, injection=a.injection, ground_truth=a.ground_truth,
                      git_commit=a.git_commit, out_dir=a.out, tables=a.tables, audit=a.audit, k8sgpt=a.k8sgpt, fixture=a.fixture)  # fmt: skip
    except (ExportError, batch.Refused, OSError, ValueError, KeyError) as e:
        print(f"REFUSED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    rec_path = dest / "reconciliation.json"
    counts = json.loads(rec_path.read_text())["counts"] if rec_path.exists() else None
    print(json.dumps({"exported": str(dest), "reconciled": counts}))
    return 0 if counts in (True, None) else 1


if __name__ == "__main__":
    sys.exit(main())
