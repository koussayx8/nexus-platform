"""The Verifier (spec §9): repair criteria checked against the raw pre-fault baseline.

The catalogue owns the criteria. The Reasoner's expectedEffect is never read. ADR-024: an alert
that clears is not a recovery — a sustained fault enters the Z-score baseline within 3 min — so
every check compares the RAW signal with the baseline recorded when the Incident was detected
(status.evidence.baseline), never an alert or a Z-score. A missing baseline or an unanswered
query never verifies: unknown is "not met", and the criteria time out into compensation.

  baseline = {signal: {"mean": m, "stddev": s}} for latencyP95, inflight, errorRatio, requestRate
  within baseline  =  current <= mean + 3 * max(stddev, ε), with ε the signal's own floor in the
                      Z-score rules (ADR-024): p95 0.01 s, in-flight 1, error ratio 0.01,
                      request rate 1/s. The request rate is two-sided (a drop is requests that
                      do not complete, a rise is a spike that goes on): also >= mean - 3 * max(…).

  restart_v2  every replica Ready; then, for 60 s, no new container restart and every required
              signal within baseline; all within 300 s. Required: p95 always, and each signal
              whose Z-score in the evidence bundle was anomalous (Z > 3; |Z| > 3 for the
              request rate) or missing: status.evidence.anomalous, written with the baseline.
              When that list is absent every signal is required. (ADR-039 addendum 2026-11-15;
              it replaces restart_v1, which read p95 only. The spec's "triggering alert
              resolved" is not read: ADR-024.)
  scale_v1    new replicas Ready within 120 s; then CPU per pod below 70 % of the CPU limit and
              p95 within baseline for 2 consecutive minutes; all within 240 s.

State lives in status.verification and is level-triggered: each call takes the stored state and
the current observations and returns the next state, so a restart of the operator resumes it.
"""

import dataclasses
import datetime
import math

import aiohttp

from .gate import live_pods, pod_ready, rollout_in_progress, selector_of
from .kube import TRANSPORT_ERRORS, ApiError
from .model import iso, parse_time

EPSILON_S = 0.01
Z_LIMIT = 3
RESTART_STABLE_S = 60
SCALE_READY_S = 120
SCALE_STABLE_S = 120
CPU_FRACTION = 0.7
CPU_QUERY = "namespace:nexus_sample_api_cpu:rate2m"
P95_QUERY = "namespace:nexus_sample_api_latency_p95:2m"
# signal -> (its raw recording rule, ε, two-sided); the keys are evidence.BASELINES' keys
SIGNALS: dict[str, tuple[str, float, bool]] = {
    "latencyP95": (P95_QUERY, EPSILON_S, False),
    "inflight": ("namespace:nexus_sample_api_inflight:sum", 1.0, False),
    "errorRatio": ("namespace:nexus_sample_api_error_ratio:rate2m", 0.01, False),
    "requestRate": ("namespace:nexus_sample_api_requests:rate2m", 1.0, True),
}


class Prometheus:
    """Instant queries against the Prometheus HTTP API, read-only."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))

    async def scalar(self, query: str, namespace: str) -> float | None:
        """The value of `query{namespace=…}`, or None for no data."""
        async with self._session.get(
            f"{self.url}/api/v1/query",
            params={"query": f'{query}{{namespace="{namespace}"}}'},
        ) as r:
            body = await r.json()
        result = (body.get("data") or {}).get("result") or []
        if body.get("status") != "success" or len(result) != 1:
            return None
        value = float(result[0]["value"][1])
        return None if math.isnan(value) else value

    async def close(self) -> None:
        await self._session.close()


@dataclasses.dataclass
class Verdict:
    result: str  # met | pending | not_met
    state: dict  # the next status.verification
    observations: dict


def cpu_limit_cores(deployment: dict) -> float | None:
    spec = ((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}
    for c in spec.get("containers") or []:
        raw = ((c.get("resources") or {}).get("limits") or {}).get("cpu")
        if raw is None:
            continue
        raw = str(raw)
        try:
            return float(raw[:-1]) / 1000 if raw.endswith("m") else float(raw)
        except ValueError:
            return None
    return None


def _number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def within(baseline: dict | None, signal: str, current: float | None) -> bool:
    """Is the raw value within its pre-fault baseline? Anything unknown is False."""
    _, epsilon, two_sided = SIGNALS[signal]
    recorded = (baseline or {}).get(signal)
    recorded = recorded if isinstance(recorded, dict) else {}
    mean, std = recorded.get("mean"), recorded.get("stddev")
    if not (_number(current) and _number(mean) and _number(std)):
        return False
    band = Z_LIMIT * max(std, epsilon)
    return current <= mean + band and (not two_sided or current >= mean - band)


def p95_within(baseline: dict | None, current: float | None) -> bool:
    return within(baseline, "latencyP95", current)


def anomalous(bundle: dict) -> list[str]:
    """The signals whose Z-score in the evidence bundle was anomalous, or is missing (unknown
    counts as anomalous): what restart_v2 must see back within baseline, besides p95."""
    zscores = (bundle.get("metrics") or {}).get("zscores")
    zscores = zscores if isinstance(zscores, dict) else {}
    out = []
    for signal, (_, _, two_sided) in SIGNALS.items():
        z = zscores.get(signal)
        if not _number(z) or z > Z_LIMIT or (two_sided and z < -Z_LIMIT):
            out.append(signal)
    return out


def required_signals(evidence: dict | None) -> list[str]:
    """p95 and the signals recorded as anomalous; all four when the record is absent."""
    recorded = (evidence or {}).get("anomalous")
    if not isinstance(recorded, list):
        return list(SIGNALS)
    return [s for s in SIGNALS if s == "latencyP95" or s in recorded]


def _restarts(pods: list[dict]) -> int:
    return sum(
        c.get("restartCount", 0)
        for p in pods
        for c in (p.get("status") or {}).get("containerStatuses") or []
    )


class Verifier:
    def __init__(self, api, metrics) -> None:
        self.api = api
        self.metrics = metrics  # a Prometheus, or None when prometheusURL is unset

    async def _raw(self, query: str, namespace: str) -> float | None:
        if self.metrics is None:
            return None
        try:
            return await self.metrics.scalar(query, namespace)
        except (ApiError, *TRANSPORT_ERRORS):
            return None

    async def check(
        self,
        criteria_id: str,
        incident: dict,
        state: dict,
        now: datetime.datetime,
        timeout_s: int,
        wanted_replicas: int | None,
    ) -> Verdict:
        target = incident["spec"]["target"]
        ns, name = target["namespace"], target["name"]
        evidence = (incident.get("status") or {}).get("evidence") or {}
        baseline = evidence.get("baseline")
        started = parse_time(state["startedAt"])
        elapsed = (now - started).total_seconds()
        next_state = dict(state)
        obs: dict = {"elapsedS": round(elapsed)}

        deployment = await self.api.get_deployment(ns, name)
        pods = (
            live_pods(await self.api.list_pods(ns, selector_of(deployment)))
            if deployment
            else []
        )
        want = wanted_replicas or ((deployment or {}).get("spec") or {}).get(
            "replicas", 0
        )
        ready = deployment is not None and (
            (deployment.get("status") or {}).get("readyReplicas", 0) == want
            and len(pods) == want
            and all(pod_ready(p) for p in pods)
            and not rollout_in_progress(deployment)
        )
        obs["allReady"] = ready
        signals = (
            required_signals(evidence)
            if criteria_id == "restart_v2"
            else ["latencyP95"]
        )
        values = {s: await self._raw(SIGNALS[s][0], ns) for s in signals}
        met = {s: within(baseline, s, values[s]) for s in signals}
        obs["p95"], obs["baselineP95"] = (
            values["latencyP95"],
            ((baseline or {}).get("latencyP95") or {}).get("mean"),
        )
        good = ready and all(met.values())

        if criteria_id == "restart_v2":
            hold = RESTART_STABLE_S
            obs["signals"] = {
                s: {"value": values[s], "within": met[s]} for s in signals
            }
            restarts = _restarts(pods)
            obs["restarts"] = restarts
            if not good or restarts != state.get("restarts", restarts):
                next_state["goodSince"] = None
                next_state["restarts"] = restarts
            else:
                next_state["goodSince"] = state.get("goodSince") or iso(now)
                next_state["restarts"] = restarts
        else:  # scale_v1
            hold = SCALE_STABLE_S
            limit = cpu_limit_cores(deployment) if deployment else None
            cpu = None
            if self.metrics is not None and want:
                try:
                    total = await self.metrics.scalar(CPU_QUERY, ns)
                    cpu = None if total is None else total / want
                except (ApiError, *TRANSPORT_ERRORS):
                    cpu = None
            obs["cpuPerPod"], obs["cpuLimit"] = cpu, limit
            cpu_ok = (
                cpu is not None and limit is not None and cpu < CPU_FRACTION * limit
            )
            if ready and not state.get("readyAt"):
                next_state["readyAt"] = iso(now)
            if good and cpu_ok:
                next_state["goodSince"] = state.get("goodSince") or iso(now)
            else:
                next_state["goodSince"] = None
            if not ready and elapsed > SCALE_READY_S and not state.get("readyAt"):
                return Verdict(
                    "not_met", next_state, obs | {"why": "replicas not Ready in 120 s"}
                )

        since = next_state.get("goodSince")
        if since and (now - parse_time(since)).total_seconds() >= hold:
            next_state["result"] = "met"
            return Verdict("met", next_state, obs)
        if elapsed >= timeout_s:
            next_state["result"] = "not_met"
            return Verdict("not_met", next_state, obs)
        next_state["result"] = "pending"
        return Verdict("pending", next_state, obs)
