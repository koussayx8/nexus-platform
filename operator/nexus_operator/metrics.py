"""Operator metrics (U3, G3): counters and gauges in the Prometheus text format, no new dependency.

Served by aiohttp on metricsPort (default 9102) at /metrics; the ServiceMonitor is
platform/observability/monitors/nexus-operator-servicemonitor.yaml. Names (proposed in ADR-034,
prefix `nexus_operator_`; labels are reason codes and ids from the spec, never free text):

  nexus_operator_incidents_total{phase,reason}          terminal outcomes: the §7 reason-code counts
  nexus_operator_gate_decisions_total{stage,verdict}    Safety Gate stages 1-7, pass or fail
  nexus_operator_actions_total{action_id,result}        executed requests: admitted, denied, error,
                                                        recorder_unavailable
  nexus_operator_recorder_errors_total                  Flight Recorder calls that failed
  nexus_operator_recorder_up                            1 while the last recorder call succeeded
  nexus_operator_redactions_total{class}                values scrubbed from evidence bundles
  nexus_operator_diagnose_seconds_{sum,count}           time from Diagnosing start to Proposed
  nexus_operator_loop_age_seconds{loop}                 seconds since the loop's last finished tick
  nexus_operator_alertmanager_errors                    Alert Poller errors since start (gauge)
  nexus_operator_killswitch_halted                      1 when the kill switch is not active
"""

import asyncio
import threading
from collections.abc import Callable

from aiohttp import web


def _fmt(name: str, labels: dict[str, str], value: float) -> str:
    if labels:
        body = ",".join(
            f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
            for k, v in sorted(labels.items())
        )
        return f"{name}{{{body}}} {value:g}"
    return f"{name} {value:g}"


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, float]] = {}
        self._help: dict[str, str] = {}
        self._gauges: dict[str, Callable[[], dict[tuple, float]]] = {}

    def counter(self, name: str, help_text: str) -> None:
        self._counters.setdefault(name, {})
        self._help[name] = help_text

    def inc(self, name: str, amount: float = 1, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            series = self._counters[name]
            series[key] = series.get(key, 0.0) + amount

    def gauge(
        self, name: str, help_text: str, read: Callable[[], dict[tuple, float]]
    ) -> None:
        """`read` returns {labels-as-sorted-tuple: value}; it runs at every scrape."""
        self._gauges[name] = read
        self._help[name] = help_text

    def value(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters[name].get(tuple(sorted(labels.items())), 0.0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = {n: dict(s) for n, s in self._counters.items()}
        for name in sorted(counters):
            lines += [f"# HELP {name} {self._help[name]}", f"# TYPE {name} counter"]
            for key, v in sorted(counters[name].items()):
                lines.append(_fmt(name, dict(key), v))
        for name in sorted(self._gauges):
            lines += [f"# HELP {name} {self._help[name]}", f"# TYPE {name} gauge"]
            try:
                series = self._gauges[name]()
            except Exception:  # noqa: BLE001 - a broken gauge must not break the scrape
                series = {}
            for key, v in sorted(series.items()):
                lines.append(_fmt(name, dict(key), v))
        return "\n".join(lines) + "\n"


METRICS = Registry()
INCIDENTS = "nexus_operator_incidents_total"
GATE = "nexus_operator_gate_decisions_total"
ACTIONS = "nexus_operator_actions_total"
RECORDER_ERRORS = "nexus_operator_recorder_errors_total"
REDACTIONS = "nexus_operator_redactions_total"
DIAGNOSE_SUM = "nexus_operator_diagnose_seconds_sum"
DIAGNOSE_COUNT = "nexus_operator_diagnose_seconds_count"
for _name, _help in (
    (INCIDENTS, "Incidents that reached a terminal phase, by phase and reason code."),
    (GATE, "Safety Gate stage decisions, by stage and verdict."),
    (ACTIONS, "Mutating requests the Executor sent, by action id and result."),
    (RECORDER_ERRORS, "Flight Recorder calls that failed."),
    (REDACTIONS, "Values scrubbed from evidence bundles, by class."),
    (DIAGNOSE_SUM, "Seconds from Diagnosing to Proposed, summed."),
    (DIAGNOSE_COUNT, "Incidents that went from Diagnosing to Proposed."),
):
    METRICS.counter(_name, _help)


async def serve(port: int, registry: Registry = METRICS) -> web.AppRunner:
    """Start /metrics on `port`; the caller keeps the runner and cleans it up."""

    async def handler(_request: web.Request) -> web.Response:
        return web.Response(
            text=await asyncio.to_thread(registry.render),
            content_type="text/plain",
            charset="utf-8",
        )

    app = web.Application()
    app.router.add_get("/metrics", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner
