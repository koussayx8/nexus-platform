"""Manual-run gate: `python -m experiments.harness.runner gate ...`. Read-only; exits 1 on any FAIL.

Run it as a module, from the repository root: the runner imports this file relatively, so
`python experiments/harness/runner.py gate` stops with an ImportError before any check.
It prints PASS or FAIL per check.

Every check is an existing precheck.py function; nothing here detects anything itself:
  locust    locust_failures: state running, users within tolerance, the namespace's rate within +-10 %
  alerts    fired_alerts: no Nexus alert pending or firing during the window (unknown counts as FAIL)
  baseline  baseline_failures: the clean-baseline z-score and stddev check
  recorder  recorder_failures: nexus_operator_recorder_up == 1
  incidents no non-terminal Incident
Every value is a required argument. --config is a batch config file; only its `precheck` section
is used, for clean_stddev (the pre-registered clean baseline), which has no CLI form.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from . import precheck
from . import runner as runner_mod
from .operator_lib import model

PROG = "python -m experiments.harness.runner gate"  # the form that works (module docstring)
_WINDOW = re.compile(r"(\d+)([smh])")


def parse_window(text: str) -> int:
    m = _WINDOW.fullmatch(text)
    secs = int(m[1]) * {"s": 1, "m": 60, "h": 3600}[m[2]] if m else 0
    if secs <= 0:
        raise ValueError(f"--window must look like 90s, 20m or 1h, not {text!r}")
    return secs


def _incident_failures(r) -> list[str]:
    rc, out = r.exec(
        [runner_mod.CLI, "--context", r.context, "-n", model.NAMESPACE]
        + ["get", model.PLURAL, "-o", "json"]
    )
    try:
        items = json.loads(out).get("items", []) if rc == 0 else None
    except ValueError:
        items = None
    if items is None:
        return ["incidents:no_answer"]
    open_ = sorted(
        i["metadata"]["uid"]
        for i in items
        if (i.get("status") or {}).get("phase") not in model.TERMINAL_PHASES
    )
    return [f"non_terminal_incident:{','.join(open_)}"] if open_ else []


def run_gate(r, cfg: dict, window_s: int) -> list[tuple[str, list[str]]]:
    """(check name, failures) per check; no failures means PASS."""
    prom = precheck.Prom(r)
    alerts = precheck.fired_alerts(prom, window_s)
    return [
        ("locust", precheck.locust_failures(r, prom, cfg)),
        (
            "alerts",
            ["alerts:no_answer"]
            if alerts is None
            else [
                f"alert:{a['alertname']}:{a['state']}:{a['namespace']}" for a in alerts
            ],
        ),
        ("baseline", precheck.baseline_failures(prom, cfg)),
        ("recorder", precheck.recorder_failures(prom)),
        ("incidents", _incident_failures(r)),
    ]


def build_config(raw: dict, namespace: str, users: int, rate: float) -> dict:
    pre = precheck.normalise(raw.get("precheck") if isinstance(raw, dict) else None)
    pre["namespace"] = namespace
    pre["locust"] = {
        **pre["locust"],
        "baseline_users": users,
        "baseline_rps_per_namespace": rate,
        "namespaces": [namespace],
    }
    precheck.normalise(pre)  # re-validate the overrides
    return pre


def main(argv=None, runner_factory=runner_mod.Runner, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n")[0])
    ap.add_argument("--context", required=True)
    ap.add_argument("--namespace", required=True, choices=runner_mod.INJECT_NAMESPACES)
    ap.add_argument("--locust", required=True, help="Locust base URL")
    ap.add_argument("--locust-users", required=True, type=int)
    ap.add_argument(
        "--locust-rate", required=True, type=float, help="requests/s per namespace"
    )
    ap.add_argument("--window", required=True, help="no-alert window, e.g. 20m")
    ap.add_argument(
        "--config",
        required=True,
        help="batch config JSON; its precheck section gives clean_stddev",
    )
    a = ap.parse_args(argv)
    try:
        window_s = parse_window(a.window)
        with open(a.config) as f:
            cfg = build_config(json.load(f), a.namespace, a.locust_users, a.locust_rate)
    except (ValueError, OSError, precheck.ConfigError) as e:
        ap.error(str(e))
    results = run_gate(runner_factory(a.context, locust=a.locust), cfg, window_s)
    for name, failures in results:
        print(
            f"{'FAIL' if failures else 'PASS'} {name}"
            + "".join(f"\n  {f}" for f in failures),
            file=out,
        )
    return 1 if any(f for _, f in results) else 0
