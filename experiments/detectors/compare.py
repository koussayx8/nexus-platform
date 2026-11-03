"""The offline detector comparison (spec section 21, G14): the deployed Z-score logic against an
Isolation Forest, on the metric windows of the exported runs.

  python3 -m experiments.detectors.compare --runs DIR --truth FILE --out DIR [--seed N]

Needs scikit-learn (analysis-only, pinned with hashes: experiments/detectors/requirements.txt).

Inputs: exported runs (scripts/export-results.py with --metrics), counted runs only (the manifest,
ground-truth hash and reconciliation gates of `make results` apply: nothing here bypasses them), and
the ground-truth file those runs were exported against.

Labelling, per run (the pre-registration, G16, fixes it): the NO-FAULT window is started_at to the
injection (the runner's pre-check window: nothing injected); the FAULT window is the injection to
the end of the fault, which is `Resolved` when the run reached it (the action removed the cause) and
the end of the run otherwise (the reset removes it). Metric points before started_at are history, used
for the Z-score baseline (it needs 18 minutes) and as forest training data in training runs.

Temporal split (spec section 21: calibrate on early baseline days, test on later ones): runs are
ordered by start; the earlier half are TRAINING runs, the later half TEST runs. The forest is fitted
on the pre-injection points of the training runs only. Both detectors are scored on the TEST runs only,
the same windows, so neither is flattered by data it learned from. The Z-score needs no fitting: its
baseline is the 15 minutes before each point, which is exactly why a slow drift can fool a forest but not it.

Per scenario and detector: detection delay (median and IQR over the detected fault windows), false
positives in the no-fault windows (windows with a false alert, and distinct false alerts), and
window-level precision, recall and F1 (TP / (TP + FP), TP / (TP + FN); a window is a unit, an alert
is firing if it persisted for `for` = 60 s). Proportions with Wilson 95 % intervals, times as median and
IQR, no p-values (spec section 21). A scenario the five signals cannot see (S3: a readiness failure
that the kube-state-metrics alerts catch) is reported as undetected, not hidden.

Deterministic: fixed seed, one job, sorted output, rounded numbers. Fixture runs stamp every output
"FIXTURE — not measured".
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from ..analysis import results, svg
from ..harness import metrics
from ..harness.stats import median_iqr
from . import iforest, zscore

DETECTORS = ("zscore", "iforest")
LABEL = {"zscore": "Z-score (deployed rules)", "iforest": "Isolation Forest"}


# ---------------------------------------------------------------- loading


class Run:
    def __init__(self, item: dict, doc: dict):
        r = item["run"]
        self.run_id, self.scenario, self.reasoner = (
            r["run_id"],
            r["scenario"],
            r["reasoner"],
        )
        self.started = metrics.ts(r["started_at"]).timestamp()
        self.ended = metrics.ts(r["ended_at"]).timestamp()
        self.injection = metrics.ts(item["injection_at"]).timestamp()
        resolved = [
            t["at"]
            for t in item["tables"]["transitions"]
            if t["to_phase"] == "Resolved"
        ]
        self.fault_end = (
            metrics.ts(min(resolved)).timestamp() if resolved else self.ended
        )
        self.series = {n: zscore.Series(doc["signals"][n]) for n in zscore.SIGNALS}
        self.times = sorted(self.series["cpu"].at)  # the 30 s grid

    def rows(self, times):
        """Feature rows (the five raw values) for the times at which all five exist."""
        out = [
            (t, [self.series[n].at[t] for n in iforest.SIGNALS])
            for t in times
            if all(t in self.series[n].at for n in iforest.SIGNALS)
        ]
        return [t for t, _ in out], [r for _, r in out]


def load(runs_dir, truth_path):
    _, truth_hash = results.load_truth(truth_path)
    loaded = results.load_runs(runs_dir, truth_hash)
    flags = set(loaded["fixture_flags"].values())
    if len(flags) > 1:
        raise ValueError(
            "fixture and measured runs are mixed in one directory: refusing to run"
        )
    runs, skipped = [], []
    for it in loaded["counted"]:
        path = pathlib.Path(runs_dir) / it["dir"] / "metrics.json"
        if not path.exists():
            skipped.append({"run_id": it["run"]["run_id"], "reason": "no_metrics"})
            continue
        runs.append(Run(it, json.loads(path.read_text(encoding="utf-8"))))
    runs.sort(key=lambda r: (r.started, r.run_id))
    return runs, skipped, loaded, truth_hash, flags == {True}


# ---------------------------------------------------------------- the two detectors


def zscore_firing(run: Run) -> dict[float, bool]:
    return zscore.any_firing(run.series, run.times)


def forest_firing(run: Run, forest: iforest.Detector) -> dict[float, bool]:
    times, rows = run.rows(run.times)
    flags = dict(zip(times, forest.flags(rows), strict=True)) if rows else {}
    return iforest.persist({t: flags.get(t, False) for t in run.times})


def evaluate(run: Run, firing: dict[float, bool]) -> dict:
    """One run, one detector: delay in the fault window, alerts in the no-fault window."""
    neg = [t for t in run.times if run.started <= t < run.injection]
    pos = [t for t in run.times if run.injection <= t <= run.fault_end]
    first = next((t for t in pos if firing.get(t)), None)
    episodes = sum(
        1
        for i, t in enumerate(neg)
        if firing.get(t) and not (i > 0 and firing.get(neg[i - 1]))
    )
    return {
        "detected": first is not None,
        "delay_s": None if first is None else first - run.injection,
        "false_positive": any(firing.get(t) for t in neg),
        "false_episodes": episodes,
        "positive_points": len(pos),
        "no_fault_points": len(neg),
    }


# ---------------------------------------------------------------- the comparison


def compare(runs: list[Run], seed: int = iforest.PARAMS["random_state"]) -> dict:
    if len(runs) < 2:
        raise ValueError(
            "need at least two runs with metric windows for a temporal split"
        )
    split = len(runs) // 2
    train, test = runs[:split], runs[split:]
    rows = []
    for r in train:
        _, x = r.rows(
            [t for t in r.times if t < r.injection]
        )  # history and the no-fault window
        rows += x
    forest = iforest.Detector({"random_state": seed}).fit(rows)
    per_run = {}
    for r in test:
        per_run[r.run_id] = {
            "zscore": evaluate(r, zscore_firing(r)),
            "iforest": evaluate(r, forest_firing(r, forest)),
        }
    scen: dict = {}
    for s in sorted({r.scenario for r in test}):
        sr = [r for r in test if r.scenario == s]
        scen[s] = {}
        for d in DETECTORS:
            ev = [per_run[r.run_id][d] for r in sr]
            tp = sum(e["detected"] for e in ev)
            fp = sum(e["false_positive"] for e in ev)
            prec, rec = metrics.proportion(tp, tp + fp), metrics.proportion(tp, len(ev))
            p, q = prec["p"], rec["p"]
            scen[s][d] = {
                "fault_windows": len(ev),
                "detected": metrics.proportion(tp, len(ev)),
                "delay_s": median_iqr(
                    [e["delay_s"] for e in ev if e["delay_s"] is not None]
                ),
                "no_fault_windows": len(ev),
                "false_positive_windows": fp,
                "false_alerts": sum(e["false_episodes"] for e in ev),
                "precision": prec,
                "recall": rec,
                "f1": None
                if p is None or q is None or p + q == 0
                else 2 * p * q / (p + q),
            }
    sweep = {}
    for q in (
        iforest.SWEEP
    ):  # sensitivity to the threshold: the forest refitted, the same test runs
        f = (
            forest
            if q == iforest.THRESHOLD_QUANTILE
            else iforest.Detector({"random_state": seed}, q).fit(rows)
        )
        for s in scen:
            ev = [evaluate(r, forest_firing(r, f)) for r in test if r.scenario == s]
            sweep.setdefault(s, {})[str(q)] = {
                "detected": sum(e["detected"] for e in ev),
                "fault_windows": len(ev),
                "false_positive_windows": sum(e["false_positive"] for e in ev),
            }
    return {
        "sensitivity": sweep,
        "split": {
            "train_runs": [r.run_id for r in train],
            "test_runs": [r.run_id for r in test],
        },
        "forest": {
            "params": {**iforest.PARAMS, "random_state": seed},
            "threshold_quantile": iforest.THRESHOLD_QUANTILE,
            "threshold": round(forest.threshold, 6),
            "training_points": len(rows),
        },
        "zscore": {
            "threshold": zscore.THRESHOLD,
            "for_s": zscore.FOR_S,
            "epsilon": zscore.EPSILON,
            "baseline_window_s": zscore.BASELINE_WINDOW_S,
            "baseline_offset_s": zscore.BASELINE_OFFSET_S,
            "min_samples": zscore.MIN_SAMPLES,
        },
        "per_scenario": scen,
        "per_run": per_run,
    }


# ---------------------------------------------------------------- output


def _d(v):
    return "n/a" if v is None else f"{v:.2f}"


def tables(res: dict) -> list[results.Table]:
    d1 = []
    for s, by in sorted(res["per_scenario"].items()):
        for d in DETECTORS:
            m = by[d]
            d1.append([s, LABEL[d], results.prop(m["detected"]), results.iqr(m["delay_s"]), m["no_fault_windows"],
                       f"{m['false_positive_windows']} ({m['false_alerts']} alerts)", results.prop(m["precision"]),
                       results.prop(m["recall"]), _d(m["f1"])])  # fmt: skip
    t1 = results.Table(
        "D1", "Detectors per scenario: detection delay, false positives, precision and recall (test runs)",
        ["Scenario", "Detector", "Fault windows detected", "Detection delay, s", "No-fault windows", "False-positive windows", "Precision", "Recall", "F1"],
        d1, "Window level: a fault window is a true positive if the detector fires in it, a no-fault window a false positive if it fires in it. "
            "Delay from the injection, median [IQR] over detected windows. A scenario the five signals cannot show (S3) is undetected here and caught by the kube-state-metrics alerts.")  # fmt: skip
    f, z = res["forest"], res["zscore"]
    t2 = results.Table(
        "D2", "Parameters and split",
        ["Detector", "Parameter", "Value"],
        [["Z-score", k, json.dumps(v, sort_keys=True)] for k, v in sorted(z.items())]
        + [["Isolation Forest", k, json.dumps(v, sort_keys=True)] for k, v in sorted({**f["params"], "threshold_quantile": f["threshold_quantile"],
                                                                                       "score_threshold": f["threshold"], "training_points": f["training_points"]}.items())]
        + [["Split", "training runs", len(res["split"]["train_runs"])], ["Split", "test runs", len(res["split"]["test_runs"])]],
        "Isolation Forest parameters are PROPOSED; G16 freezes them. The forest is fitted on the pre-injection points of the training runs only.")  # fmt: skip
    d3 = []
    for rid, by in sorted(res["per_run"].items()):
        d3.append([rid] + [x for d in DETECTORS for x in (by[d]["detected"], "-" if by[d]["delay_s"] is None else f"{by[d]['delay_s']:.0f}", by[d]["false_episodes"])])  # fmt: skip
    t3 = results.Table(
        "D3",
        "Per test run",
        [
            "Run",
            "Z detected",
            "Z delay s",
            "Z false alerts",
            "Forest detected",
            "Forest delay s",
            "Forest false alerts",
        ],
        d3,
    )
    d4 = [[s, q, f"{v['detected']}/{v['fault_windows']}", v["false_positive_windows"]]
          for s, by in sorted(res["sensitivity"].items()) for q, v in sorted(by.items(), key=lambda kv: float(kv[0]))]  # fmt: skip
    t4 = results.Table("D4", "Isolation Forest, sensitivity to the score-threshold quantile (test runs)",
                       ["Scenario", "Quantile of baseline scores", "Fault windows detected", "False-positive windows"], d4,
                       "Information for the G16 calibration, NOT a way to choose the value: it uses the test labels. The proposed value is in D2.")  # fmt: skip
    return [t1, t2, t3, t4]


def figure(res: dict, banner: str) -> str:
    rows = []
    for s, by in sorted(res["per_scenario"].items()):
        for d in DETECTORS:
            r = by[d]["recall"]
            rows.append((f"{s} {LABEL[d].split(' (')[0]}", r["p"], r["lo"], r["hi"]))
    return svg.interval_plot(
        "F4. Recall per scenario and detector, Wilson 95 % interval (test runs)",
        rows,
        banner,
    )


def build(runs_dir, truth_path, out_dir, seed=iforest.PARAMS["random_state"]) -> dict:
    runs, skipped, _, truth_hash, fixture = load(runs_dir, truth_path)
    res = compare(runs, seed)
    res.update(
        ground_truth_hash=truth_hash,
        fixture=fixture,
        skipped=skipped,
        runs_considered=len(runs),
    )
    mark = results.MARK if fixture else ""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tabs = tables(res)
    for t in tabs:
        (out / f"{t.id}.csv").write_text(t.csv(mark), encoding="utf-8")
    (out / "detectors.md").write_text(
        "# Detector comparison\n\n"
        + (f"> **{mark}**\n\n" if mark else "")
        + "\n".join(t.md(mark) for t in tabs),
        encoding="utf-8",
    )
    (out / "F4.svg").write_text(figure(res, mark), encoding="utf-8")
    (out / "detectors.json").write_text(
        json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=iforest.PARAMS["random_state"])
    a = ap.parse_args(argv)
    try:
        res = build(a.runs, a.truth, a.out, a.seed)
    except iforest.MissingDependency as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "out": a.out,
                "test_runs": len(res["split"]["test_runs"]),
                "fixture": res["fixture"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
