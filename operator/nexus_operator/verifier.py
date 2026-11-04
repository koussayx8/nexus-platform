"""The Verifier (spec §9): repair criteria checked against the raw pre-fault baseline.

The catalogue owns the criteria. The Reasoner's expectedEffect is never read. ADR-024: an alert
that clears is not a recovery — a sustained fault enters the Z-score baseline within 3 min — so
every check compares the RAW signal with the baseline recorded when the Incident was detected
(status.evidence.baseline), never an alert or a Z-score. A missing baseline or an unanswered
query never verifies: unknown is "not met", and the criteria time out into compensation.

  baseline = {"latencyP95": {"mean": s, "stddev": s}}     (seconds; from namespace:…_p95:2m)
  within baseline  =  current p95 <= mean + 3 * max(stddev, 0.01)     (ε = 0.01 s, ADR-024)

  restart_v1  every replica Ready within 180 s; then p95 within baseline and no new container
              restarts for 60 s. (The spec's "triggering alert resolved" is not read: ADR-024.)
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
RESTART_STABLE_S = 60
SCALE_READY_S = 120
SCALE_STABLE_S = 120
CPU_FRACTION = 0.7
CPU_QUERY = "namespace:nexus_sample_api_cpu:rate2m"
P95_QUERY = "namespace:nexus_sample_api_latency_p95:2m"


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


def p95_within(baseline: dict | None, current: float | None) -> bool:
    lat = (baseline or {}).get("latencyP95") or {}
    mean, std = lat.get("mean"), lat.get("stddev")
    if (
        current is None
        or not isinstance(mean, int | float)
        or not isinstance(std, int | float)
    ):
        return False
    return current <= mean + 3 * max(std, EPSILON_S)


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
        baseline = ((incident.get("status") or {}).get("evidence") or {}).get(
            "baseline"
        )
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
        p95 = None
        if self.metrics is not None:
            try:
                p95 = await self.metrics.scalar(P95_QUERY, ns)
            except (ApiError, *TRANSPORT_ERRORS):
                p95 = None
        obs["p95"], obs["baselineP95"] = (
            p95,
            ((baseline or {}).get("latencyP95") or {}).get("mean"),
        )
        good = ready and p95_within(baseline, p95)

        if criteria_id == "restart_v1":
            hold = RESTART_STABLE_S
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
