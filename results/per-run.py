#!/usr/bin/env python3
"""Per-run table of the M4 pilot, from the exported runs (experiments/runs/*/run.json and tables.json).
  python3 results/per-run.py experiments/runs > results/per-run.csv
One row per valid run: outcome against the ground truth and the phase times of docs/plans/m4-metrics.md.
Phase times are seconds from the Flight Recorder (metrics.flatten, as `make results` T07/T08); the phases
after Executing exist only when the Incident got that far. Offline; reads files only."""
import csv, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from experiments.harness.metrics import flatten  # noqa: E402

COLS = ["run", "arm", "scenario", "terminal", "reason", "outcome", "proposal_class", "admitted_mutations",
        "followed", "detect_s", "poll_s", "intake_s", "diagnose_s", "gate_s", "act_s", "verify_s", "mttr_s",
        "rate_end_rps", "fallback"]


def r(x):
    return "" if x is None else round(x, 1)


def main(runs_dir):
    truth = json.loads((ROOT / "experiments" / "ground-truth.json").read_text())
    rows = []
    for d in sorted(pathlib.Path(runs_dir).iterdir()):
        doc = json.loads((d / "run.json").read_text())
        if doc["run"]["discarded"] or not (d / "tables.json").exists():
            continue
        tables, sc, run = json.loads((d / "tables.json").read_text()), doc["score"], doc["run"]
        _, fr = flatten({"runs": [run], **tables}, {run["run_id"]: doc["injection_at"]}, truth["scenarios"])
        t = fr[0]
        classes = sc["proposal_classes"]
        muts = [m["class"] for m in sc["mutations"] if not m["before_injection"]]
        if sc["wrong_action"]:
            outcome = "wrong action"  # admitted, in the envelope, not accepted; what followed is in `followed`
        elif truth["scenarios"][run["scenario"]]["correct_action"]["action"] in truth["envelope"]["mutating"]:
            if muts and set(muts) == {"acceptable"}:
                outcome = "right action, fixed" if sc["resolved"] else "right action, fix missed"
            else:
                outcome = "other"
        elif not muts and sc["terminal"] == sc["expected_terminal"]:  # nothing to fix by a mutation (S5)
            outcome = "right action (escalated)" if set(classes) == {"acceptable"} else "escalated, recommendation not accepted"
        else:
            outcome = "other"
        if sc["fallback"]:
            outcome += f" (arm's proposal rejected, rules fallback acted: {sc['fallback']['cause']}-caused)"
        rows.append({
            "run": run["run_id"], "arm": run["reasoner"], "scenario": run["scenario"],
            "terminal": sc["terminal"], "reason": t["reason"], "outcome": outcome,
            "proposal_class": "+".join(classes), "admitted_mutations": len(sc["mutations"]),
            "followed": sc["followed"] or "", "detect_s": r(t["detect_s"]), "poll_s": r(t["poll_s"]),
            "intake_s": r(t["intake_s"]), "diagnose_s": r(t["diagnose_s"]), "gate_s": r(t["gate_s"]),
            "act_s": r(t["act_s"]), "verify_s": r(t["verify_s"]), "mttr_s": r(t["mttr_s"]),
            "rate_end_rps": r(sc["rate_end"]), "fallback": (sc["fallback"] or {}).get("cause", ""),
        })
    w = csv.DictWriter(sys.stdout, COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)


if __name__ == "__main__":
    main(sys.argv[1])
