"""`make results`: every Chapter 5 table and figure from exported runs, without a cluster (spec §25).

  python3 -m experiments.analysis.results --runs DIR --truth FILE --out DIR [--suite FILE] [--retrieval FILE]

Inputs are files only: the exported runs of scripts/export-results.py (experiments/runs/<run>/), the
pre-registered ground truth (JSON: {"scenarios": {S1: {fault_class, correct_action}, ...}}; its
sha256 must equal the `ground_truth_hash` of every run, otherwise the run is excluded: a change to
ground truth is a new version, spec §20), the live-containment suite results (optional) and the
retrieval evaluation (optional, experiments/retrieval/results.json).

Nothing reaches a table without passing reconciliation (spec §14):
  * a run whose files no longer match MANIFEST.json, whose ground-truth hash differs, or whose audit
    reconciliation is not clean (completeness below 1.0, an unexplained request...) does NOT COUNT for
    effectiveness; it is listed with its reason in T01, never dropped silently;
  * containment (T09) is the exception, on purpose: it reads every exported, undiscarded run with a
    reconciliation, counting or not, because an unexplained admitted request is a violation;
  * void and discarded attempts are counted per cell with their reasons (spec §20).

Output, all deterministic (no clock, sorted keys, fixed precision): T01..T14 as CSV and in tables.md,
F1..F3 as SVG, results.json (every number), INDEX.md. Statistics are spec §21's: Wilson 95 % for
proportions, the rule of three for zero events, median and IQR for times; no p-values, per scenario.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import pathlib
import sys

from ..harness import metrics
from . import svg

HERE = pathlib.Path(__file__).resolve().parent
MARK = "FIXTURE — not measured"  # on every table and figure built from fixture runs
PHASES = ("detect", "diagnose", "gate", "act", "verify")


# ---------------------------------------------------------------- loading


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_truth(path) -> tuple[dict, str]:
    p = pathlib.Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    return doc["scenarios"], sha256(p)


def load_runs(runs_dir, truth_hash: str) -> dict:
    """{counted, discarded, excluded, reconciled}: see the module docstring."""
    out = {
        "counted": [],
        "discarded": [],
        "excluded": [],
        "reconciled": [],
        "fixture_flags": {},
    }
    for d in sorted(
        p for p in pathlib.Path(runs_dir).iterdir() if (p / "MANIFEST.json").exists()
    ):
        manifest = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
        run_id = manifest["run_id"]
        out["fixture_flags"][d.name] = bool(manifest.get("fixture", False))
        bad = [
            f
            for f, h in manifest["files"].items()
            if not (d / f).exists() or sha256(d / f) != h
        ]
        if bad:
            out["excluded"].append(
                {
                    "run_id": run_id,
                    "reason": "manifest_mismatch",
                    "detail": ",".join(sorted(bad)),
                }
            )
            continue
        doc = json.loads((d / "run.json").read_text(encoding="utf-8"))
        run = doc["run"]
        item = {
            "run": run,
            "dir": d.name,
            "injection_at": doc.get("injection_at"),
            "queue_record": doc["queue_record"],
        }
        if run["discarded"]:
            out["discarded"].append(item)
            continue
        item["tables"] = json.loads((d / "tables.json").read_text(encoding="utf-8"))
        item["reconciliation"] = json.loads(
            (d / "reconciliation.json").read_text(encoding="utf-8")
        )
        item["k8sgpt"] = (
            json.loads((d / "k8sgpt.json").read_text())
            if (d / "k8sgpt.json").exists()
            else None
        )
        out["reconciled"].append(item)  # containment reads these, counting or not
        if manifest["ground_truth_hash"] != truth_hash:
            out["excluded"].append(
                {
                    "run_id": run_id,
                    "reason": "ground_truth_changed",
                    "detail": manifest["ground_truth_hash"][:12],
                }
            )
        elif not item["reconciliation"]["counts"]:
            j = item["reconciliation"]["join"]
            out["excluded"].append({"run_id": run_id, "reason": "not_reconciled",
                                    "detail": f"completeness={j['completeness']} {json.dumps(j['counts'], sort_keys=True)}"})  # fmt: skip
        else:
            out["counted"].append(item)
    return out


# ---------------------------------------------------------------- formatting


def prop(p: dict | None) -> str:
    if not p or p["n"] == 0:
        return "n/a (0/0)"
    return f"{p['k']}/{p['n']} = {p['p']:.2f} [{p['lo']:.2f}, {p['hi']:.2f}]"


def iqr(d: dict | None) -> str:
    return (
        "n/a"
        if not d
        else f"{d['median']:.0f} [{d['q1']:.0f}, {d['q3']:.0f}] (n={d['n']})"
    )


def split(key: str) -> tuple[str, str]:
    a, _, b = key.partition("/")
    return a, b


class Table:
    def __init__(self, tid, title, columns, rows, note=""):
        self.id, self.title, self.columns, self.rows, self.note = (
            tid,
            title,
            columns,
            rows,
            note,
        )

    def csv(self, marker: str = "") -> str:
        """A leading `# <marker>` line when the data is a fixture (a plain CSV reader skips `#`)."""
        buf = io.StringIO()
        if marker:
            buf.write(f"# {marker}\n")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(self.columns)
        w.writerows(self.rows)
        return buf.getvalue()

    def md(self, marker: str = "") -> str:
        head = f"### {self.id}. {self.title}\n\n" + (
            f"> **{marker}**\n\n" if marker else ""
        )
        if not self.rows:
            return head + "_No data._\n" + (f"\n{self.note}\n" if self.note else "")
        lines = [
            "| " + " | ".join(self.columns) + " |",
            "|" + "---|" * len(self.columns),
        ]
        lines += [
            "| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |"
            for r in self.rows
        ]
        return (
            head + "\n".join(lines) + "\n" + (f"\n{self.note}\n" if self.note else "")
        )


# ---------------------------------------------------------------- the analysis


def analyse(
    loaded: dict, truth: dict, suite=None, retrieval=None
) -> tuple[list[Table], dict, dict]:
    counted, reconciled = loaded["counted"], loaded["reconciled"]
    merged = {t: [] for t in ("runs", "incidents", "transitions", "evidence_bundles", "proposals", "gate_decisions", "actions", "verifications")}  # fmt: skip
    for it in counted:
        merged["runs"].append(it["run"])
        for t, rows in it["tables"].items():
            merged[t] += rows
    inj = {it["run"]["run_id"]: it["injection_at"] for it in counted}
    props, runs = metrics.flatten(merged, inj, truth)
    r: dict = {}

    # -- T01 inventory: valid, discarded and excluded per cell
    cells: dict = {}
    for it in counted:
        c = cells.setdefault(
            (it["run"]["reasoner"], it["run"]["scenario"]),
            {"valid": 0, "disc": [], "exc": []},
        )
        c["valid"] += 1
    for it in loaded["discarded"]:
        c = cells.setdefault(
            (it["run"]["reasoner"], it["run"]["scenario"]),
            {"valid": 0, "disc": [], "exc": []},
        )
        c["disc"].append(it["run"]["discard_reason"])
    by_id = {it["run"]["run_id"]: it for it in counted + loaded["reconciled"]}
    for e in loaded["excluded"]:
        it = by_id.get(e["run_id"])
        key = (it["run"]["reasoner"], it["run"]["scenario"]) if it else ("?", "?")
        cells.setdefault(key, {"valid": 0, "disc": [], "exc": []})["exc"].append(
            f"{e['reason']} ({e['detail']})"
        )
    r["inventory"] = {f"{a}/{s}": c for (a, s), c in sorted(cells.items())}
    t01 = Table("T01", "Runs per cell: valid, discarded, excluded", ["Arm", "Scenario", "Valid", "Discarded (reasons)", "Excluded (reasons)"],
                [[a, s, c["valid"], "; ".join(sorted(c["disc"])) or "-", "; ".join(sorted(c["exc"])) or "-"] for (a, s), c in sorted(cells.items())],
                "Excluded: the run does not count for effectiveness (spec §14); containment still reads it (T09). A cell stops at ten valid runs or fifteen attempts (spec §20).")  # fmt: skip

    # -- T02 terminal phase and reason code counts
    tc = metrics.terminal_counts(runs)
    r["terminal_counts"] = tc
    t02 = Table("T02", "Terminal phase and reason code, counts", ["Arm", "Scenario", "Terminal / reason", "Runs"],
                [[*split(k), pr, n] for k, d in sorted(tc.items()) for pr, n in d.items()])  # fmt: skip

    # -- T03 decision quality per cell
    da, asa = (
        metrics.diagnosis_accuracy(props, truth),
        metrics.action_selection_accuracy(props, truth),
    )
    r["diagnosis_accuracy"], r["action_selection_accuracy"] = da, asa
    t03 = Table("T03", "Diagnosis accuracy and action selection, per arm and scenario (Wilson 95 %)",
                ["Arm", "Scenario", "Diagnosis (non-abstaining)", "Action selection"],
                [[*split(k), prop(da.get(k)), prop(asa[k])] for k in sorted(asa)],
                "Per scenario, never one aggregate (spec §21). S5 is the generalisation test: R1's action is right, its diagnosis `unknown`.")  # fmt: skip

    # -- T04 proposal quality per arm
    val, ab = metrics.proposal_validity(props), metrics.abstain_rate(props)
    fap, ce, man = (
        metrics.false_action_proposed(props),
        metrics.correct_escalation_rate(props, truth),
        metrics.manipulation_rate(props),
    )
    r.update(
        proposal_validity=val,
        abstain_rate=ab,
        false_action_proposed=fap,
        correct_escalation=ce,
        manipulation=man,
    )
    t04 = Table("T04", "Proposal quality, per arm", ["Arm", "Validity", "Abstain", "False action proposed (S3-S6)", "Correct escalation (S3, S5)", "Manipulation S6", "Same action on S4"],
                [[a, prop(val[a]), prop(ab[a]), prop(fap.get(a)), prop(ce.get(a)), prop(man[a]["S6"]), prop(man[a]["S4_same_action"])] for a in sorted(val)])  # fmt: skip

    # -- T05 outcomes
    rs, cp, fae = (
        metrics.remediation_success(runs),
        metrics.compensation_rate(runs),
        metrics.false_action_executed(runs),
    )
    r.update(remediation_success=rs, compensation_rate=cp, false_action_executed=fae)
    t05 = Table("T05", "Outcomes: remediation success, compensation, false action executed",
                ["Arm", "Scenario", "Remediation success", "Compensation rate"],
                [[*split(k), prop(rs[k]), prop(cp[k])] for k in sorted(rs)])  # fmt: skip
    t05b = Table("T05b", "False action executed, per arm (S3-S6, Flight Recorder side)", ["Arm", "False action executed"],
                 [[a, prop(fae[a])] for a in sorted(fae)],
                 "The gap to T04's proposed rate is what autonomy levels and the Safety Gate remove (spec §21). The API Audit Log side is T09.")  # fmt: skip

    # -- T06 fallback
    fb = metrics.fallback_rate(runs)
    r["fallback_rate"] = fb
    t06 = Table("T06", "Rules Engine fallback (fallbackUsed), per arm", ["Arm", "Scenario", "Runs with fallback"],
                [[*(split(k) if "/" in k else (k, "all")), prop(fb[k])] for k in sorted(fb, key=lambda k: (split(k)[0], "/" in k, k))],
                "Fallback runs count against the arm that failed (spec §17): they stay in its denominators.")  # fmt: skip

    # -- T07 MTTR, T08 detection
    mt, dl = metrics.mttr_decomposition(runs), metrics.detection_latency(runs)
    r["mttr"], r["detection_latency"] = mt, dl
    t07 = Table("T07", "MTTR and its decomposition: median [IQR] seconds", ["Arm", "Scenario", "MTTR", *[p.capitalize() for p in PHASES]],
                [[*split(k), iqr(v["mttr"]), *[iqr(v[p]) for p in PHASES]] for k, v in sorted(mt.items()) if v["mttr"]],
                "Resolved runs only. Median and IQR, never mean and standard deviation on ten skewed values (spec §21).")  # fmt: skip
    t08 = Table("T08", "Detection latency: median [IQR] seconds", ["Arm", "Scenario", "Injection to Incident"],
                [[*split(k), iqr(v)] for k, v in sorted(dl.items()) if v], "Floor of about 60-90 s by design: scrape, `for: 1m`, 10 s poll (spec §21).")  # fmt: skip

    # -- T09 containment, T10 reconciliation
    pairs = [(it["run"]["reasoner"], it["reconciliation"]) for it in reconciled]
    ct = metrics.containment_from_reconciliations(pairs)
    r["containment"] = ct
    t09 = Table("T09", "Containment violations (API Audit Log, re-checked against the envelope)",
                ["Arm", "Runs", "Operator mutation events", "Admitted, envelope-checked", "Violations", "Unverified", "Per-request rate, 95 % upper bound"],
                [[a, c["runs"], c["operator_mutation_events"], c["admitted_checked"], c["violations"], c["unverified"],
                  f"< {c['rate_upper_95']:.3f} (3/n)" if c["rate_upper_95"] is not None else "n/a"] for a, c in sorted(ct.items())],
                "Must be 0. Zero events bound the rate, they do not prove safety (rule of three, spec §21). Unverified: the record cannot show it (an L2 approval, a missing body).")  # fmt: skip
    rec_rows = []
    for it in sorted(reconciled, key=lambda i: i["run"]["run_id"]):
        j, rc = it["reconciliation"]["join"], it["reconciliation"]
        rec_rows.append([it["run"]["run_id"], it["run"]["reasoner"], j["operator_mutation_events"],
                         "-" if j["completeness"] is None else f"{j['completeness']:.2f}", "yes" if rc["counts"] else "NO", rc["containment_violations"]])  # fmt: skip
    t10 = Table("T10", "Reconciliation per run", ["Run", "Arm", "Operator mutation events", "Completeness", "Counts", "Violations"], rec_rows,
                "Completeness = matched events / operator mutation events; it must be 1.0 for a run to count (spec §14).")  # fmt: skip

    # -- T11 no-fault windows
    nf = metrics.pre_check_windows(
        [{**it["queue_record"], "reasoner": it["run"]["reasoner"]} for it in counted]
    )
    r["no_fault"] = nf
    t11 = Table("T11", "No-fault windows: alerts and Incidents with nothing injected",
                ["Arm", "Windows", "With an Incident", "With a Nexus alert", "Alert unknown"],
                [[a, v["windows"], prop(v["with_incident"]), prop(v["with_alert"]), v["alerts_unknown"]] for a, v in sorted(nf.items())],
                "'Quiet when healthy' is a stated limitation (re-plan O5); a window whose alert query got no answer is unknown, never clean.")  # fmt: skip

    # -- T12 retrieval, T13 suite, T14 K8sGPT
    rm = (retrieval or {}).get("metrics")
    r["retrieval"] = rm
    t12 = Table("T12", "Retrieval quality (BM25 over the runbooks)", ["Metric", "Value"],
                [[k, f"{v:.4g}" if isinstance(v, float) else v] for k, v in sorted((rm or {}).items())],
                "From experiments/retrieval/results.json (`make eval-retrieval`).")  # fmt: skip
    cov = metrics.policy_coverage(suite["cells"]) if suite else None
    bla = metrics.blocking_layer_attribution(suite["attempts"]) if suite else {}
    r["policy_coverage"], r["blocking_layers"] = cov, bla
    t13 = Table("T13", "Policy coverage and blocking-layer attribution (containment suite)", ["Measure", "Setting", "Value"],
                ([["False allow (forbidden cells)", "-", prop(cov["false_allow"])], ["False deny (permitted cells)", "-", prop(cov["false_deny"])]] if cov else [])
                + [[f"Blocked at {layer}", f"advisoryChecks {adv}", prop(p)] for adv, layers in sorted(bla.items()) for layer, p in layers.items()],
                "False allow must be 0; false deny shows the cost of safety. Cells are reported as cells, never as a rate over repeats (spec §21).")  # fmt: skip
    kg = metrics.k8sgpt_accuracy(
        [
            {
                "scenario": it["run"]["scenario"],
                "fault_class": it["k8sgpt"]["fault_class"],
            }
            for it in counted
            if it["k8sgpt"]
        ],
        truth,
    )
    r["k8sgpt_accuracy"] = kg
    t14 = Table("T14", "K8sGPT diagnosis accuracy, per scenario", ["Scenario", "Correct fault class"], [[s, prop(kg[s])] for s in sorted(kg)],
                "Rubric-scored fault class (the rubric is part of the pre-registered ground truth).")  # fmt: skip

    return (
        [t01, t02, t03, t04, t05, t05b, t06, t07, t08, t09, t10, t11, t12, t13, t14],
        r,
        {"mt": mt, "asa": asa, "tc": tc},
    )


# ---------------------------------------------------------------- figures


def figures(parts: dict, banner: str = "") -> dict[str, str]:
    mt, asa, tc = parts["mt"], parts["asa"], parts["tc"]
    f1 = svg.stacked_bars(
        "F1. MTTR decomposition, median seconds per phase (resolved runs)",
        [(k, [v[p]["median"] if v[p] else 0.0 for p in PHASES]) for k, v in sorted(mt.items()) if v["mttr"]],
        [p.capitalize() for p in PHASES], " s", banner,
    )  # fmt: skip
    f2 = svg.interval_plot(
        "F2. Action-selection accuracy with Wilson 95 % interval",
        [(k, v["p"], v["lo"], v["hi"]) for k, v in sorted(asa.items())],
        banner,
    )  # fmt: skip
    phases = sorted({pr.split("/")[0] for d in tc.values() for pr in d})
    f3 = svg.stacked_bars(
        "F3. Terminal phase, runs per cell",
        [(k, [float(sum(n for pr, n in d.items() if pr.split("/")[0] == ph)) for ph in phases]) for k, d in sorted(tc.items())],
        phases, "", banner,
    )  # fmt: skip
    return {"F1": f1, "F2": f2, "F3": f3}


def build(runs_dir, truth_path, out_dir, suite=None, retrieval=None) -> dict:
    truth, truth_hash = load_truth(truth_path)
    loaded = load_runs(runs_dir, truth_hash)
    suite_doc = json.loads(pathlib.Path(suite).read_text()) if suite else None
    retr = (
        json.loads(pathlib.Path(retrieval).read_text())
        if retrieval and pathlib.Path(retrieval).exists()
        else None
    )
    flags = set(loaded["fixture_flags"].values())
    if len(flags) > 1:
        raise ValueError(
            "fixture and measured runs are mixed in one directory: refusing to build"
        )
    fixture = flags == {True}
    mark = MARK if fixture else ""
    tables, raw, parts = analyse(loaded, truth, suite_doc, retr)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for t in tables:
        (out / f"{t.id}.csv").write_text(t.csv(mark), encoding="utf-8")
    (out / "tables.md").write_text(
        "# Chapter 5 tables\n\n"
        + (f"> **{mark}**\n\n" if mark else "")
        + "\n".join(t.md(mark) for t in tables),
        encoding="utf-8",
    )
    figs = figures(parts, mark)
    for fid, text in figs.items():
        (out / f"{fid}.svg").write_text(text, encoding="utf-8")
    summary = {
        "fixture": fixture,
        "ground_truth_hash": truth_hash,
        "runs": {"counted": len(loaded["counted"]), "discarded": len(loaded["discarded"]), "excluded": len(loaded["excluded"])},
        "excluded": loaded["excluded"],
        "metrics": raw,
    }  # fmt: skip
    (out / "results.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "INDEX.md").write_text(
        "# Results\n\n"
        + (f"**{mark}**\n\n" if mark else "")
        + "Generated by `make results` from exported runs; ground truth "
        f"`{truth_hash[:12]}`. {summary['runs']['counted']} runs counted, {summary['runs']['discarded']} discarded, "
        f"{summary['runs']['excluded']} excluded (see T01).\n\n"
        + "".join(f"- [{t.id}. {t.title}]({t.id}.csv)\n" for t in tables)
        + "".join(f"- [{f}]({f}.svg)\n" for f in figs),
        encoding="utf-8",
    )
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--suite")
    ap.add_argument("--retrieval")
    a = ap.parse_args(argv)
    try:
        s = build(a.runs, a.truth, a.out, a.suite, a.retrieval)
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"out": a.out, "runs": s["runs"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
