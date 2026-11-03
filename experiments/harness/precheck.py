"""Pre-checks before a run (spec §20 "Reset and pre-checks", §25; TASKS.md M4; ADR-024). Stdlib only.

Implemented here, all of which must hold, waited for at most the cap (20 min by the spec):
  * clean baseline: for every signal in the nexus-detection rules, |Z| < 1.5 and the recorded
    `baseline_stddev15m` is within 1.5 x that signal's clean baseline (a value from the
    pre-registration), otherwise the previous fault still masks the next one. A series with no
    answer (the rule needs 27 of 30 samples, ADR-024) is NOT clean.
  * node load: CPU below 60 % and memory below 80 %.
  * no non-terminal Incident. Incidents are never deleted by the runner (rule 3): the check waits,
    inside the same cap, and when the cap runs out the batch STOPS (it does not discard and carry
    on), with the reason.
Not implemented (listed in the PR): sample-api 2/2 Ready, Kill Switch, the namespace level, Backstage
at zero, the Locust baseline, Reasoner ready, audit log being written.

No-fault data. The window from the first poll to the end of the pre-checks has nothing injected,
so everything Nexus fires inside it is a false alarm of the system under test (or of the reset).
`window` records, for every pre-check window, the Nexus alerts that were pending or firing at any
time in it (Prometheus `ALERTS` over the whole window, so an alert that fires between two polls is
still seen) and the Incidents that appeared. A query that gets no answer is recorded as null,
never as an empty list.

Prometheus is read through the API server's service proxy with
`kubectl --context C -n monitoring get --raw <path>` (read-only; no port-forward), as ramp.py does.
Live check pending: no cluster in the session that wrote this.
"""

from __future__ import annotations

import datetime
import json
import math
import time
import urllib.parse
from dataclasses import dataclass, field

PROM_PATH = "/api/v1/namespaces/monitoring/services/http:observability-kube-prometh-prometheus:9090/proxy"
CLI = "kubectl"

# signal name (as keys of the clean baseline and of the evidence bundle's zscores) -> recording rule
SIGNALS = {
    "cpu": "cpu",
    "latencyP95": "latency_p95",
    "inflight": "inflight",
    "errorRatio": "error_ratio",
    "requests": "requests",
}

DEFAULTS = {
    "namespace": "nexus-dev",
    "cap_s": 1200,  # spec §20: waiting at most 20 minutes
    "poll_s": 30,
    "z_max": 1.5,
    "stddev_factor": 1.5,
    "node_cpu_max": 0.60,
    "node_mem_max": 0.80,
}
REQUIRED = ("clean_stddev",)


class ConfigError(ValueError):
    pass


def normalise(cfg: dict | None) -> dict:
    """The pre-check configuration with the spec's values filled in. `clean_stddev` (a number per
    signal) has no default: it comes from the pre-registration (G16)."""
    if not isinstance(cfg, dict):
        raise ConfigError("precheck configuration is missing")
    out = {**DEFAULTS, **cfg}
    clean = out.get("clean_stddev")
    if not isinstance(clean, dict) or set(clean) != set(SIGNALS):
        raise ConfigError(
            f"precheck.clean_stddev needs exactly the signals {sorted(SIGNALS)}"
        )
    for name, v in clean.items():
        if (
            isinstance(v, bool)
            or not isinstance(v, int | float)
            or v < 0
            or math.isnan(v)
        ):
            raise ConfigError(f"precheck.clean_stddev.{name} must be a number >= 0")
    for key in (
        "cap_s",
        "poll_s",
        "z_max",
        "stddev_factor",
        "node_cpu_max",
        "node_mem_max",
    ):
        v = out[key]
        if isinstance(v, bool) or not isinstance(v, int | float) or v <= 0:
            raise ConfigError(f"precheck.{key} must be a positive number")
    if not isinstance(out["namespace"], str) or not out["namespace"]:
        raise ConfigError("precheck.namespace must be a non-empty string")
    return out


class Prom:
    """Instant queries through the service proxy, read with the runner's exec."""

    def __init__(self, runner, base: str = PROM_PATH):
        self.r, self.base = runner, base.rstrip("/")

    def query(self, promql: str) -> list[dict] | None:
        """The result vector, or None when there is no answer (failed call, unparsable, not success)."""
        path = f"{self.base}/api/v1/query?" + urllib.parse.urlencode(
            {"query": promql}, quote_via=urllib.parse.quote
        )
        rc, out = self.r.exec(
            [CLI, "--context", self.r.context, "-n", "monitoring", "get", "--raw", path]
        )
        if rc != 0:
            return None
        try:
            doc = json.loads(out)
        except ValueError:
            return None
        if doc.get("status") != "success":
            return None
        return doc.get("data", {}).get("result", [])

    def scalar(self, promql: str) -> float | None:
        """First sample as a float; None for no answer, an empty vector or NaN (never 0)."""
        res = self.query(promql)
        if not res:
            return None
        try:
            v = float(res[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return None if math.isnan(v) else v


def baseline_failures(prom: Prom, cfg: dict) -> list[str]:
    ns = cfg["namespace"]
    out = []
    for name, rule in SIGNALS.items():
        z = prom.scalar(
            f'namespace:nexus_sample_api_{rule}:zscore15m{{namespace="{ns}"}}'
        )
        sd = prom.scalar(
            f'namespace:nexus_sample_api_{rule}:baseline_stddev15m{{namespace="{ns}"}}'
        )
        if z is None:
            out.append(f"baseline:{name}:no_zscore")
        elif abs(z) >= cfg["z_max"]:
            out.append(f"baseline:{name}:zscore={z:.2f}")
        if sd is None:
            out.append(f"baseline:{name}:no_stddev")
        elif sd > cfg["stddev_factor"] * cfg["clean_stddev"][name]:
            out.append(
                f"baseline:{name}:stddev={sd:.4g}>{cfg['stddev_factor']}x{cfg['clean_stddev'][name]:.4g}"
            )
    return out


NODE_CPU = '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))'
NODE_MEM = "1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes)"


def node_failures(prom: Prom, cfg: dict) -> list[str]:
    out = []
    for name, q, limit in (
        ("cpu", NODE_CPU, cfg["node_cpu_max"]),
        ("memory", NODE_MEM, cfg["node_mem_max"]),
    ):
        v = prom.scalar(q)
        if v is None:
            out.append(f"node:{name}:no_answer")
        elif v >= limit:
            out.append(f"node:{name}={v:.2f}>={limit:.2f}")
    return out


def fired_alerts(prom: Prom, window_s: float) -> list[dict] | None:
    """Nexus alerts pending or firing at any time in the last window_s seconds; None when unknown."""
    secs = max(math.ceil(window_s), 1)
    res = prom.query(
        f'max by (alertname, alertstate, namespace) (max_over_time(ALERTS{{alertname=~"Nexus.*"}}[{secs}s]))'
    )
    if res is None:
        return None
    return sorted(
        ({"alertname": s["metric"].get("alertname"), "state": s["metric"].get("alertstate"),
          "namespace": s["metric"].get("namespace")} for s in res),
        key=lambda a: (a["alertname"] or "", a["state"] or ""),
    )  # fmt: skip


@dataclass
class Result:
    ok: bool
    stop_reason: str | None = (
        None  # set: the batch must stop (a non-terminal Incident outlived the cap)
    )
    failures: list[str] = field(default_factory=list)  # what still failed at the end
    polls: int = 0
    waited_s: float = 0.0
    window: dict = field(default_factory=dict)  # the no-fault record


def _iso(t: float) -> str:
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


def run_prechecks(prom: Prom, cfg: dict, incidents, before_uids: set, terminal: frozenset,
                  monotonic=time.monotonic, wall=time.time, sleep=time.sleep) -> Result:  # fmt: skip
    """Poll until every check holds or the cap runs out. `incidents()` lists the Incident objects;
    `before_uids` are the Incidents that existed before the run's reset (so a new one is a false
    alarm); `terminal` are the phases that end an Incident."""
    start_m, start_w = monotonic(), wall()
    polls = 0
    while True:
        polls += 1
        items = incidents()
        open_ = [
            i["metadata"]["uid"]
            for i in items
            if (i.get("status") or {}).get("phase") not in terminal
        ]
        failures = []
        if open_:
            failures.append(f"non_terminal_incident:{','.join(sorted(open_))}")
        failures += baseline_failures(prom, cfg)
        failures += node_failures(prom, cfg)
        waited = monotonic() - start_m
        if not failures or waited >= cfg["cap_s"]:
            break
        sleep(cfg["poll_s"])
    end_w = wall()
    new = [
        {"uid": i["metadata"]["uid"], "phase": (i.get("status") or {}).get("phase")}
        for i in items
        if i["metadata"]["uid"] not in before_uids
    ]
    window = {
        "start": _iso(start_w), "end": _iso(end_w), "seconds": end_w - start_w, "polls": polls,
        "alerts": fired_alerts(prom, end_w - start_w),
        "incidents": sorted(new, key=lambda i: i["uid"]),
    }  # fmt: skip
    # the batch stops only when the cap ran out on an open Incident; other failures discard the run
    stop = "non_terminal_incident" if failures and open_ else None
    return Result(not failures, stop, failures, polls, waited, window)
