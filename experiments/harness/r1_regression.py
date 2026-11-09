"""R1 regression for ADR-039: the rules module at a base commit against the current one.

    python -m experiments.harness.r1_regression build --prom DIR --fr DIR [--out CORPUS]
    python -m experiments.harness.r1_regression run [--base SHA] [--corpus CORPUS] [--out-dir DIR]
    python -m experiments.harness.r1_regression check [--out-dir DIR]

`build` turns the read-only captures into the committed corpus: the Prometheus series of
2026-11-07 read on 2026-11-09 (`query`/`query_range`, step 30 s, one JSON file per window and
series, named `<window>.<series>.json`) and the Flight Recorder export of the eight nexus-prod
bundles of 2026-11-07 (`bundles.json`), with the in-flight Z at each bundle's collectedAt
(`inflight-at-collected.tsv`) and the proposal each Incident CR recorded (`recorded-crs.tsv`).

`run` classifies every row with the base rules module (read with `git show <base>:...`) and with
the current one (operator/nexus_operator/rules.py through operator_lib), writes result.json and
result.md, and exits 1 when a row outside S1 changes, or when the base does not reproduce a
recorded Flight Recorder proposal (then the tool, not the rule, is wrong).

`check` (the CI test) needs no git history: it re-runs only the current rules on the committed
corpus and compares them with the committed `new` column.

Rows: the 30 labelled retrieval queries (evidence/v1, no in-flight Z); an S4 sweep; every 30 s
of the clean and S1 windows, two named points; the eight Flight Recorder bundles. Standard
library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import types
from pathlib import Path

from .operator_lib import rules

REPO = Path(__file__).resolve().parents[2]
RULES = "operator/nexus_operator/rules.py"
QUERIES = REPO / "experiments/retrieval/queries.json"
OUT_DIR = REPO / "experiments/regression/adr-039"
BASE = "9b66f0c876807c1c1e3013bd444570305166e626"  # dev before ADR-039 (PR #188)

ZSCORES = {
    "cpu": "namespace:nexus_sample_api_cpu:zscore15m",
    "latencyP95": "namespace:nexus_sample_api_latency_p95:zscore15m",
    "errorRatio": "namespace:nexus_sample_api_error_ratio:zscore15m",
    "requestRate": "namespace:nexus_sample_api_requests:zscore15m",
    "inflight": "namespace:nexus_sample_api_inflight:zscore15m",
}
ERROR_RATIO = "namespace:nexus_sample_api_error_ratio:rate2m"
INFLIGHT_SUM = "namespace:nexus_sample_api_inflight:sum"
KSM = {
    "desired": "kube_deployment_spec_replicas",
    "ready": "kube_deployment_status_replicas_ready",
    "updated": "kube_deployment_status_replicas_updated",
    "replicas": "kube_deployment_status_replicas",
    "available": "kube_deployment_status_replicas_available",
    "generation": "kube_deployment_metadata_generation",
    "observed": "kube_deployment_status_observed_generation",
}
# (window, namespace, scenario, what it is), as captured on 2026-11-09
WINDOWS = [
    ("point-dev", "nexus-dev", "no-fault", "single-scrape in-flight burst"),
    ("point-prod", "nexus-prod", "S1", "the S1 Flight Recorder bundle time"),
    ("clean-dev-a", "nexus-dev", "no-fault", "clean 07:40-08:20Z"),
    ("clean-dev-b", "nexus-dev", "no-fault", "clean 08:47-09:50Z"),
    ("clean-prod", "nexus-prod", "no-fault", "clean 07:40-09:50Z"),
    ("s1-dev", "nexus-dev", "S1", "S1 08:21:39-08:24:16Z"),
    ("s1-prod", "nexus-prod", "S1", "S1 09:51:14-09:56:34Z"),
]
S1_INCIDENT = "5fa4f476"  # the nexus-prod S1 Incident of 2026-11-07
FR_NOTES = {
    "ce453920": "warm-up, before the clean window; no fault injected",
    "33bcaaa7": "warm-up, before the clean window; no fault injected",
    "5fa4f476": "S1 (handler hang)",
    "ec0cf29c": "no fault injected (after nexus-prod was cleared at 09:57Z)",
    "fc3f0c3d": "no fault injected (after nexus-prod was cleared at 09:57Z)",
    "49fe844f": "no fault injected (after nexus-prod was cleared at 09:57Z)",
    "4afcad4a": "no fault injected (after nexus-prod was cleared at 09:57Z)",
    "63984170": "no fault injected (after nexus-prod was cleared at 09:57Z)",
}
S4_SWEEP = [(cpu, inf) for cpu in (0.5, 1.0, 1.2) for inf in (0.0, 2.0, 3.5, 5.0, 10.0)]


# ---- build ---------------------------------------------------------------------------------
def _num(v: str) -> float | None:
    x = float(v)
    return x if math.isfinite(x) else None


def _series(prom: Path, window: str, name: str) -> dict[float, float | None]:
    doc = json.loads((prom / f"{window}.{name}.json").read_text())
    result = doc["data"]["result"]
    if not result:
        return {}
    (r,) = result
    pairs = r.get("values") or [r["value"]]
    return {float(t): _num(v) for t, v in pairs}


def _iso(t: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _workload(k: dict[str, float | None]) -> dict:
    as_int = {n: (int(v) if v is not None else None) for n, v in k.items()}
    d, u = as_int["desired"], as_int["updated"]
    known = all(v is not None for v in as_int.values())
    rollout = known and (
        as_int["observed"] < as_int["generation"]
        or u < d
        or as_int["replicas"] > u
        or as_int["available"] < u
    )  # gate.rollout_in_progress on the kube-state-metrics series
    return {
        "desired": d,
        "ready": as_int["ready"],
        "updated": u,
        "rolloutInProgress": rollout,
    }


def _bundle(ns: str, zs: dict, err, workload: dict, pods: list | None = None) -> dict:
    return {
        "schema": "evidence/v1.1",
        "incident": {
            "target": {"namespace": ns, "kind": "Deployment", "name": "sample-api"}
        },
        "workload": workload,
        "pods": pods or [],
        "metrics": {"window": "30m", "zscores": zs, "errorRatio": err},
    }


def build(prom: Path, fr: Path) -> dict:
    rows = []
    for window, ns, scen, what in WINDOWS:
        z = {k: _series(prom, window, q) for k, q in ZSCORES.items()}
        err = _series(prom, window, ERROR_RATIO)
        inflight_sum = _series(prom, window, INFLIGHT_SUM)
        ksm = {k: _series(prom, window, q) for k, q in KSM.items()}
        for t in sorted(ksm["desired"]):
            rows.append(
                {
                    "scen": scen,
                    "source": f"prometheus {window}",
                    "label": f"{ns} {_iso(t)} ({what})",
                    "inflightSum": inflight_sum.get(t),
                    "bundle": _bundle(
                        ns,
                        {k: s.get(t) for k, s in z.items()},
                        err.get(t),
                        _workload({k: s.get(t) for k, s in ksm.items()}),
                    ),
                }
            )
    bundles = json.loads((fr / "bundles.json").read_text())
    inflight = {}
    for line in (fr / "inflight-at-collected.tsv").read_text().splitlines():
        f = line.split()
        inflight[f[0]] = float(
            dict(kv.split("=", 1) for kv in f[2:])["inflight:zscore15m"]
        )
    recorded = {}
    for line in (fr / "recorded-crs.tsv").read_text().splitlines():
        f = line.split("\t")
        recorded[f[0][:8]] = f"{f[5]}/{f[6]}"
    for x in bundles:
        uid = x["incident_uid"][:8]
        c = x["content"]
        zs = dict(c["metrics"]["zscores"]) | {"inflight": inflight[uid]}
        pods = [
            {
                "containers": [
                    {"state": k.get("state"), "reason": k.get("reason")}
                    for k in p.get("containers") or []
                ]
            }
            for p in c.get("pods") or []
        ]
        wl = {
            k: c["workload"].get(k)
            for k in ("desired", "ready", "updated", "rolloutInProgress")
        }
        rows.append(
            {
                "scen": "S1" if uid == S1_INCIDENT else "no-fault",
                "source": "flight recorder",
                "label": f"nexus-prod Incident {uid} detected {x['detected_at'][11:19]}Z ({FR_NOTES[uid]})",
                "collectedAt": c["collectedAt"],
                "recorded": recorded[uid],
                "bundle": _bundle(
                    "nexus-prod", zs, c["metrics"].get("errorRatio"), wl, pods
                ),
            }
        )
    return {
        "schema": "r1-regression-corpus/v1",
        "note": "Read-only captures: Prometheus (2026-11-07 data, read 2026-11-09) and the Flight "
        "Recorder export of 2026-11-09 (nexus_reader). Real bundles keep workload, container "
        "states and metrics only; the in-flight Z of a Flight Recorder bundle is Prometheus at "
        "its collectedAt.",
        "rows": rows,
    }


# ---- run -----------------------------------------------------------------------------------
def base_rules(ref: str) -> types.ModuleType:
    src = subprocess.run(
        ["git", "show", f"{ref}:{RULES}"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    mod = types.ModuleType("rules_base")
    exec(compile(src, f"{ref}:{RULES}", "exec"), mod.__dict__)  # noqa: S102 - our own file at a commit
    return mod


def cls(doc: dict) -> str:
    return f"{doc['hypothesis']['faultClass']}/{doc['action']['id']}"


def synthetic_rows() -> list[dict]:
    rows = []
    for q in json.loads(QUERIES.read_text())["queries"]:
        scen = next(
            (s for s in ("S1", "S2", "S3", "S4", "S5", "S6") if s in q["description"]),
            "other",
        )
        rows.append(
            {
                "scen": scen,
                "source": "synthetic query",
                "label": f"{q['id']} {q['description']}",
                "bundle": q["bundle"],
            }
        )
        # The retrieval bundles carry `desired` only, so R1 never reaches rules 3-5 on them; the
        # same bundle with a missing ready/updated filled as `desired` exercises rule 4 too.
        full = json.loads(json.dumps(q["bundle"]))
        wl = full.setdefault("workload", {})
        for k in ("ready", "updated"):
            wl.setdefault(k, wl.get("desired"))
        rows.append(
            {
                "scen": scen,
                "source": "synthetic query, ready filled",
                "label": f"{q['id']} {q['description']} (ready/updated = desired)",
                "bundle": full,
            }
        )
    for cpu, inf in S4_SWEEP:
        b = _bundle(
            "nexus-dev",
            {
                "cpu": cpu,
                "latencyP95": 0.3,
                "errorRatio": 0.0,
                "requestRate": 4.2,
                "inflight": inf,
            },
            0.0,
            {"desired": 2, "ready": 2, "updated": 2, "rolloutInProgress": False},
        )
        rows.append(
            {
                "scen": "S4",
                "source": "S4 sweep",
                "label": f"S4 rate Z 4.2, p95 Z 0.3, err 0, CPU Z {cpu}, in-flight Z {inf}",
                "bundle": b,
            }
        )
    return rows


def classify(rows: list[dict], new, base=None) -> list[dict]:
    out = []
    for r in rows:
        row = {k: v for k, v in r.items() if k != "bundle"}
        row["zscores"] = (r["bundle"].get("metrics") or {}).get("zscores")
        row["new"] = cls(new.propose(r["bundle"]))
        if base is not None:
            row["base"] = cls(base.propose(r["bundle"]))
        out.append(row)
    return out


def summarise(rows: list[dict]) -> dict:
    changed = [r for r in rows if r["base"] != r["new"]]
    groups: dict[tuple, int] = {}
    for r in rows:
        key = (r["scen"], r["source"], r["base"], r["new"])
        groups[key] = groups.get(key, 0) + 1
    return {
        "rows": len(rows),
        "changed": len(changed),
        "changed_outside_s1": sum(r["scen"] != "S1" for r in changed),
        "recorded_mismatches": sum(
            1 for r in rows if "recorded" in r and r["recorded"] != r["base"]
        ),
        "groups": [
            {"scen": k[0], "source": k[1], "base": k[2], "new": k[3], "count": n}
            for k, n in sorted(groups.items())
        ],
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fmt(v) -> str:
    return "null" if v is None else f"{v:.2f}"


def markdown(res: dict) -> str:
    s = res["summary"]
    out = [
        "# R1 regression for ADR-039",
        "",
        (
            f"Base rules: `{res['base']}:{RULES}` (sha256 `{res['base_rules_sha256'][:16]}`). "
            f"New rules: sha256 `{res['new_rules_sha256'][:16]}`. "
            f"Corpus sha256 `{res['corpus_sha256'][:16]}`."
        ),
        "Generated by `python -m experiments.harness.r1_regression run`; do not edit by hand.",
        "",
        (
            f"**{s['rows']} rows, {s['changed']} changed, {s['changed_outside_s1']} changed "
            f"outside S1; {s['recorded_mismatches']} recorded Flight Recorder proposals not "
            "reproduced by the base.**"
        ),
        "",
        "## By scenario and source",
        "",
        "| scenario | source | base (dev) | new (ADR-039) | rows |",
        "| --- | --- | --- | --- | --- |",
    ]
    for g in s["groups"]:
        mark = " **changed**" if g["base"] != g["new"] else ""
        out.append(
            f"| {g['scen']} | {g['source']} | {g['base']} | {g['new']}{mark} | {g['count']} |"
        )
    named = [
        r
        for r in res["rows"]
        if r["source"]
        in (
            "flight recorder",
            "S4 sweep",
            "prometheus point-dev",
            "prometheus point-prod",
        )
        or r["label"].startswith(
            ("q01 ", "q02 ", "q03 ", "q10 ", "q11 ", "q26 ", "q27 ")
        )
        or r["base"] != r["new"]
        or r["source"] == "prometheus s1-prod"
        or r["source"] == "prometheus s1-dev"
    ]
    out += [
        "",
        "## Named rows: every Flight Recorder Incident, the S4 sweep, both points, the S1 windows, every change",
        "",
        "| scenario | row | CPU Z | p95 Z | rate Z | in-flight Z | base (dev) | new (ADR-039) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in named:
        z = r["zscores"] or {}
        mark = " **changed**" if r["base"] != r["new"] else ""
        out.append(
            f"| {r['scen']} | {r['label']} | {_fmt(z.get('cpu'))} | {_fmt(z.get('latencyP95'))} | "
            f"{_fmt(z.get('requestRate'))} | {_fmt(z.get('inflight'))} | {r['base']} | {r['new']}{mark} |"
        )
    return "\n".join(out) + "\n"


def run(ref: str, corpus_path: Path, out_dir: Path) -> int:
    corpus = json.loads(corpus_path.read_text())
    rows = classify(synthetic_rows() + corpus["rows"], rules, base_rules(ref))
    base_src = subprocess.run(
        ["git", "show", f"{ref}:{RULES}"], cwd=REPO, capture_output=True, check=True
    ).stdout
    res = {
        "schema": "r1-regression-result/v1",
        "base": ref,
        "base_rules_sha256": hashlib.sha256(base_src).hexdigest(),
        "new_rules_sha256": _sha(REPO / RULES),
        "corpus_sha256": _sha(corpus_path),
        "queries_sha256": _sha(QUERIES),
        "summary": summarise(rows),
        "rows": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(
        json.dumps(res, indent=1, sort_keys=True) + "\n"
    )
    (out_dir / "result.md").write_text(markdown(res))
    s = res["summary"]
    print(
        f"rows={s['rows']} changed={s['changed']} changed_outside_s1={s['changed_outside_s1']} recorded_mismatches={s['recorded_mismatches']}"
    )
    return 1 if s["changed_outside_s1"] or s["recorded_mismatches"] else 0


def check(out_dir: Path) -> list[str]:
    """Why the committed result is not current for the current rules (empty: it is)."""
    res = json.loads((out_dir / "result.json").read_text())
    corpus_path = out_dir / "corpus.json"
    errors = []
    if res["corpus_sha256"] != _sha(corpus_path):
        errors.append("corpus.json changed since result.json was written")
    if res["queries_sha256"] != _sha(QUERIES):
        errors.append("queries.json changed since result.json was written")
    rows = classify(
        synthetic_rows() + json.loads(corpus_path.read_text())["rows"], rules
    )
    if [r["new"] for r in rows] != [r["new"] for r in res["rows"]]:
        errors.append(
            "the current rules classify the corpus differently from result.json"
        )
    if res["summary"]["changed_outside_s1"] or res["summary"]["recorded_mismatches"]:
        errors.append(
            "result.json records a change outside S1 or an unreproduced recorded proposal"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="r1_regression")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--prom", type=Path, required=True)
    b.add_argument("--fr", type=Path, required=True)
    b.add_argument("--out", type=Path, default=OUT_DIR / "corpus.json")
    r = sub.add_parser("run")
    r.add_argument("--base", default=BASE)
    r.add_argument("--corpus", type=Path, default=OUT_DIR / "corpus.json")
    r.add_argument("--out-dir", type=Path, default=OUT_DIR)
    c = sub.add_parser("check")
    c.add_argument("--out-dir", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            json.dumps(build(a.prom, a.fr), indent=1, sort_keys=True) + "\n"
        )
        print(f"wrote {a.out}")
        return 0
    if a.cmd == "run":
        return run(a.base, a.corpus, a.out_dir)
    errors = check(a.out_dir)
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
