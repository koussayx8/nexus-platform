"""The Evidence Collector (spec §4, §16): build the evidence/v1.1 bundle for one Incident.

evidence/v1.1 (ADR-039) is evidence/v1 plus one Z-score, `metrics.zscores.inflight`, the in-flight
request count against its 15 min baseline: under a hang requests stop completing, so the p95 of the
completed ones can stay flat while in-flight requests pile up. Nothing else changes.

Read-only: pods, Deployment, ReplicaSets, events, logs, the Argo CD Application and Prometheus.
The collector returns the RAW bundle and the raw pre-fault baseline; the caller redacts before
anything is hashed, stored or handed on (redact.py, Q1). Limits are the spec's: events from the
last 15 minutes, at most 50; at most 200 log lines per pod for at most 3 pods; metric windows of
30 minutes (the recording rules' own 15 min baseline plus the 3 min lag, ADR-024).

Any value the cluster or Prometheus cannot give is null, never invented; R1 treats a missing
Z-score as no match. The baseline (ADR-024: recorded at detection, compared raw by the Verifier)
is the mean and standard deviation of the p95 latency recording rule, null when unavailable.
"""

import datetime

from .gate import ready_replicas, rollout_in_progress, selector_of
from .kube import TRANSPORT_ERRORS, ApiError, KubeApi
from .model import iso, parse_time

EVENT_WINDOW = datetime.timedelta(minutes=15)
MAX_EVENTS = 50
MAX_LOG_LINES = 200
MAX_LOG_PODS = 3

ZSCORES = {
    "cpu": "namespace:nexus_sample_api_cpu:zscore15m",
    "latencyP95": "namespace:nexus_sample_api_latency_p95:zscore15m",
    "errorRatio": "namespace:nexus_sample_api_error_ratio:zscore15m",
    "requestRate": "namespace:nexus_sample_api_requests:zscore15m",
    "inflight": "namespace:nexus_sample_api_inflight:zscore15m",  # evidence/v1.1 (ADR-039)
}
SCHEMA = "evidence/v1.1"
ERROR_RATIO = "namespace:nexus_sample_api_error_ratio:rate2m"
P95_MEAN = "namespace:nexus_sample_api_latency_p95:baseline_avg15m"
P95_STDDEV = "namespace:nexus_sample_api_latency_p95:baseline_stddev15m"


class CollectionError(Exception):
    """The target could not be read at all; the Diagnosing step retries until its timeout."""


def application_name(namespace: str) -> str:
    return f"sample-api-{namespace.removeprefix('nexus-')}"


def _digest(image: str | None) -> str | None:
    return image.split("@", 1)[1] if image and "@" in image else None


def _container_state(status: dict) -> dict:
    state = status.get("state") or {}
    kind = next(iter(state), None) or "unknown"
    last = (status.get("lastState") or {}).get("terminated")
    return {
        "state": kind,
        "reason": (state.get(kind) or {}).get("reason") if kind != "unknown" else None,
        "lastTermination": (last or {}).get("reason"),
    }


class EvidenceCollector:
    def __init__(self, api: KubeApi, metrics, clock) -> None:
        self.api = api
        self.metrics = (
            metrics  # a verifier.Prometheus, or None when prometheusURL is unset
        )
        self.clock = clock

    async def _scalar(self, query: str, namespace: str) -> float | None:
        if self.metrics is None:
            return None
        try:
            return await self.metrics.scalar(query, namespace)
        except (ApiError, *TRANSPORT_ERRORS):
            return None

    async def collect(self, incident: dict) -> tuple[dict, dict]:
        """(the raw evidence/v1.1 bundle without its hash, the baseline for status.evidence)."""
        spec = incident["spec"]
        target = spec["target"]
        ns, name = target["namespace"], target["name"]
        now = self.clock()
        deployment = await self.api.get_deployment(ns, name)
        if deployment is None:
            raise CollectionError(f"Deployment {ns}/{name} not found")
        selector = selector_of(deployment)
        pods = await self.api.list_pods(ns, selector)
        replica_sets = await self.api.list_replicasets(ns, selector)
        events = await self.api.list_events(ns)
        spec_d, status_d = deployment.get("spec") or {}, deployment.get("status") or {}

        revisions = sorted(
            replica_sets,
            key=lambda r: int(
                (
                    (r["metadata"].get("annotations") or {}).get(
                        "deployment.kubernetes.io/revision"
                    )
                )
                or 0
            ),
            reverse=True,
        )
        current = revisions[0] if revisions else None
        image = None
        if current:
            cs = (
                ((current.get("spec") or {}).get("template") or {}).get("spec") or {}
            ).get("containers") or [{}]
            image = cs[0].get("image")
        workload = {
            "desired": spec_d.get("replicas", 1),
            "ready": ready_replicas(deployment),
            "updated": status_d.get("updatedReplicas", 0) or 0,
            "rolloutInProgress": rollout_in_progress(deployment),
            "currentRevision": {
                "replicaSet": current["metadata"]["name"] if current else None,
                "imageDigest": _digest(image),
            },
            "previousRevisions": [r["metadata"]["name"] for r in revisions[1:4]],
        }
        pod_rows = []
        for p in pods:
            st = p.get("status") or {}
            statuses = st.get("containerStatuses") or []
            pod_rows.append(
                {
                    "name": p["metadata"]["name"],
                    "ready": any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in st.get("conditions") or []
                    ),
                    "restarts": sum(c.get("restartCount", 0) for c in statuses),
                    "containers": [_container_state(c) for c in statuses],
                }
            )
        pod_rows.sort(key=lambda r: r["name"])
        return (
            {
                "schema": SCHEMA,
                "incident": {
                    "uid": incident["metadata"]["uid"],
                    "target": {"namespace": ns, "kind": target["kind"], "name": name},
                    "alerts": [
                        {
                            "name": (spec.get("source") or {}).get("alertname"),
                            "startedAt": spec.get("detectedAt"),
                        }
                    ],
                    "note": None,
                },
                "workload": workload,
                "pods": pod_rows,
                "events": self._events(events, name, now),
                "logs": await self._logs(ns, pod_rows),
                "metrics": await self._metrics(ns),
                "gitops": await self._gitops(ns),
                "collectedAt": iso(now),
            },
            await self._baseline(ns, now),
        )

    @staticmethod
    def _events(items: list[dict], name: str, now: datetime.datetime) -> list[dict]:
        out = []
        for e in items:
            if not ((e.get("involvedObject") or {}).get("name") or "").startswith(name):
                continue
            stamp = (
                e.get("lastTimestamp") or e.get("eventTime") or e.get("firstTimestamp")
            )
            try:
                seen = parse_time(stamp) if stamp else None
            except ValueError:
                seen = None
            if seen is None or now - seen > EVENT_WINDOW:
                continue
            out.append(
                {
                    "reason": e.get("reason"),
                    "type": e.get("type"),
                    "count": e.get("count", 1),
                    "lastSeen": iso(seen),
                }
            )
        out.sort(key=lambda e: e["lastSeen"], reverse=True)
        return out[:MAX_EVENTS]

    async def _logs(self, ns: str, pod_rows: list[dict]) -> dict:
        logs: dict[str, list[str]] = {"current": [], "previous": []}
        for row in pod_rows[:MAX_LOG_PODS]:
            for key, previous in (("current", False), ("previous", True)):
                try:
                    text = await self.api.pod_log(
                        ns, row["name"], previous=previous, tail=MAX_LOG_LINES
                    )
                except (ApiError, *TRANSPORT_ERRORS):
                    text = None
                if text:
                    logs[key] += text.splitlines()[-MAX_LOG_LINES:]
        return logs

    async def _metrics(self, ns: str) -> dict:
        zscores = {k: await self._scalar(q, ns) for k, q in ZSCORES.items()}
        return {
            "window": "30m",
            "zscores": zscores,
            "errorRatio": await self._scalar(ERROR_RATIO, ns),
        }

    async def _gitops(self, ns: str) -> dict | None:
        app = application_name(ns)
        try:
            body = await self.api.get_application(app)
        except (ApiError, *TRANSPORT_ERRORS):
            return None
        if body is None:
            return None
        st = body.get("status") or {}
        sync = st.get("sync") or {}
        return {
            "application": app,
            "sync": sync.get("status"),
            "health": (st.get("health") or {}).get("status"),
            "revision": sync.get("revision"),
            "lastCommit": {"sha": sync.get("revision"), "message": None},
        }

    async def _baseline(self, ns: str, now: datetime.datetime) -> dict:
        mean = await self._scalar(P95_MEAN, ns)
        std = await self._scalar(P95_STDDEV, ns)
        return {
            "latencyP95": {"mean": mean, "stddev": std},
            "capturedAt": iso(now),
        }
