#!/usr/bin/env python3
"""suite-active.json + suite-halted.json (experiments.containment.live reports: a list of rows) -> the
{cells, attempts} file `make results SUITE=` reads (metrics.policy_coverage, blocking_layer_attribution).
  cells     one per request: expected and observed, allow | deny | unknown ("UNKNOWN": the answer could
            not be placed on a layer; it is neither a false allow nor a false deny)
  attempts  one per request that was refused (observed deny): the refusing layers joined by '+'.
            The suite sends server-side dry-run requests directly, so the operator's advisoryChecks
            setting does not apply: 'n/a (direct requests)'.
"""
import json, re, sys

def parse(s):
    head = s.split(" ", 1)[0].lower()
    layers = re.findall(r"'([^']+)'", s)
    return {"admit": "allow", "deny": "deny", "unknown": "unknown"}[head], layers

cells, attempts = [], []
for path in sys.argv[2:]:
    for r in json.load(open(path)):
        e, el = parse(r["expected"]); o, ol = parse(r["observed"])
        cells.append({"cell": r["cell"], "variant": r["variant"], "pass": r["pass"], "expected": e,
                      "observed": o, "expected_layers": el, "observed_layers": ol, "source": path.split("/")[-1]})
        if o == "deny":
            attempts.append({"advisory_checks": "n/a (direct requests)", "layer": "+".join(ol) or "unplaced"})
json.dump({"schema": "suite-input/v1", "cells": cells, "attempts": attempts}, open(sys.argv[1], "w"), indent=1, sort_keys=True)
print(len(cells), "cells,", len(attempts), "refused")
