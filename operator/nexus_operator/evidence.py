"""The Evidence Collector (spec §4, §16): build the evidence/v1.2 bundle for one Incident.

evidence/v1.2 (ADR-044) is evidence/v1.1 plus three read-only additions, all untrusted and capped:
`workload.podSpec` (a short summary of the current pod template: image, ports, probes, resources,
env var NAMES), `workload.rolloutDiff` (what changed from the previous ReplicaSet's template, in
the same terms; null with no previous revision), and `cluster.activeAlerts` (the other firing
Nexus alerts at collection time: alert name and target only). Env var values, commands and
arguments are never read into it; nothing comes from Git or commit messages (blinding, ADR-022).

evidence/v1.1 (ADR-039) is evidence/v1 plus one Z-score, `metrics.zscores.inflight`, the in-flight
request count against its 15 min baseline: under a hang requests stop completing, so the p95 of the
completed ones can stay flat while in-flight requests pile up. Nothing else changes.

Read-only: pods, Deployment, ReplicaSets, events, logs, the Argo CD Application and Prometheus.
The collector returns the RAW bundle and the raw pre-fault baseline; the caller redacts before
anything is hashed, stored or handed on (redact.py, Q1). Limits are the spec's: events from the
last 15 minutes, at most 50; at most 200 log lines per pod, written in the same last 15 minutes,
for at most 3 pods; metric windows of
30 minutes (the recording rules' own 15 min baseline plus the 3 min lag, ADR-024).

Any value the cluster or Prometheus cannot give is null, never invented; R1 treats a missing
Z-score as no match. The baseline (ADR-024: recorded at detection, compared raw by the Verifier)
is the mean and standard deviation of the 15 min baseline recording rules of the four signals
restart_v2 can require (BASELINES), null when unavailable. It goes to status.evidence, not into
the bundle: evidence/v1.1 is unchanged.
"""

import datetime
import json

from .gate import ready_replicas, rollout_in_progress, selector_of
from .kube import TRANSPORT_ERRORS, ApiError, KubeApi
from .model import iso, parse_time

EVENT_WINDOW = datetime.timedelta(minutes=15)
# Logs share the events' window (Block 5 finding (e)): tailLines alone reached back hours on a
# quiet pod, whose access log keeps only non-2xx lines (ADR-042 amendment), so a bundle carried
# 10-hour-old db_slots_exhausted errors as if they were current.
LOG_WINDOW = EVENT_WINDOW
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
SCHEMA = "evidence/v1.2"
MAX_DIFF_ENTRIES = 20  # workload.rolloutDiff
MAX_FIELD_CHARS = 300  # one podSpec value or one diff entry
MAX_ENV_NAMES = 40  # per container
MAX_ACTIVE_ALERTS = 10  # cluster.activeAlerts
ACTIVE_ALERTS_QUERY = 'ALERTS{alertstate="firing", alertname=~"Nexus.*"}'
ERROR_RATIO = "namespace:nexus_sample_api_error_ratio:rate2m"
# status.evidence.baseline: signal (its key in ZSCORES) -> the prefix of its baseline rules
BASELINES = {
    "latencyP95": "namespace:nexus_sample_api_latency_p95",
    "inflight": "namespace:nexus_sample_api_inflight",
    "errorRatio": "namespace:nexus_sample_api_error_ratio",
    "requestRate": "namespace:nexus_sample_api_requests",
}


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


def _cut(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + " [cut]"


def _probe(p) -> str | None:
    """httpGet /ready :8000, period 5s, failures 3, delay 0s; tcp and grpc by port; exec without
    its command (a command line may carry a secret)."""
    if not isinstance(p, dict):
        return None
    if isinstance(p.get("httpGet"), dict):
        h = p["httpGet"]
        how = f"httpGet {h.get('path', '/')} :{h.get('port')}"
    elif isinstance(p.get("tcpSocket"), dict):
        how = f"tcpSocket :{p['tcpSocket'].get('port')}"
    elif isinstance(p.get("grpc"), dict):
        how = f"grpc :{p['grpc'].get('port')}"
    elif "exec" in p:
        how = "exec (command not shown)"
    else:
        how = "unknown"
    timing = ", ".join(
        f"{label} {p[key]}{unit}"
        for key, label, unit in (
            ("periodSeconds", "period", "s"),
            ("failureThreshold", "failures", ""),
            ("initialDelaySeconds", "delay", "s"),
            ("timeoutSeconds", "timeout", "s"),
        )
        if key in p
    )
    return _cut(f"{how}, {timing}" if timing else how)


def template_summary(rs: dict | None) -> list[dict] | None:
    """Per container of a ReplicaSet's pod template: name, image, ports, the three probes,
    resources and env var NAMES (sorted, capped; values are never read). None without one."""
    if not rs:
        return None
    spec = (((rs.get("spec") or {}).get("template") or {}).get("spec")) or {}
    out = []
    for c in spec.get("containers") or []:
        if not isinstance(c, dict):
            continue
        names = sorted(
            {
                e["name"]
                for e in c.get("env") or []
                if isinstance(e, dict) and isinstance(e.get("name"), str)
            }
        )
        refs = sorted(
            f"{kind}:{(src.get(kind) or {}).get('name')}"
            for src in c.get("envFrom") or []
            if isinstance(src, dict)
            for kind in ("configMapRef", "secretRef")
            if kind in src
        )
        out.append(
            {
                "name": _cut(c.get("name") or "?"),
                "image": _cut(c.get("image") or "?"),
                "ports": sorted(
                    p.get("containerPort")
                    for p in c.get("ports") or []
                    if isinstance(p, dict) and isinstance(p.get("containerPort"), int)
                ),
                "readinessProbe": _probe(c.get("readinessProbe")),
                "livenessProbe": _probe(c.get("livenessProbe")),
                "startupProbe": _probe(c.get("startupProbe")),
                "resources": _cut(c.get("resources") or {}),
                "envNames": [_cut(n) for n in names[:MAX_ENV_NAMES]],
                "envFrom": [_cut(r) for r in refs[:MAX_ENV_NAMES]],
            }
        )
    return out


def template_diff(
    previous: list[dict] | None, current: list[dict] | None
) -> list[str] | None:
    """What changed from the previous template to the current one, one short line each, at most
    MAX_DIFF_ENTRIES. None without a previous revision; [] when nothing in these terms changed."""
    if previous is None or current is None:
        return None
    before = {c["name"]: c for c in previous}
    after = {c["name"]: c for c in current}
    out: list[str] = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        if b is None:
            out.append(_cut(f"{name}: container added"))
            continue
        if a is None:
            out.append(_cut(f"{name}: container removed"))
            continue
        for key in (
            "image",
            "ports",
            "readinessProbe",
            "livenessProbe",
            "startupProbe",
            "resources",
        ):
            if b.get(key) != a.get(key):
                out.append(_cut(f"{name}.{key}: {b.get(key)} -> {a.get(key)}"))
        for key in ("envNames", "envFrom"):
            added = sorted(set(a.get(key) or []) - set(b.get(key) or []))
            removed = sorted(set(b.get(key) or []) - set(a.get(key) or []))
            if added:
                out.append(_cut(f"{name}.{key} added: {', '.join(added)}"))
            if removed:
                out.append(_cut(f"{name}.{key} removed: {', '.join(removed)}"))
    return out[:MAX_DIFF_ENTRIES]


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
        """(the raw evidence/v1.2 bundle without its hash, the baseline for status.evidence)."""
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
        # evidence/v1.2 (ADR-044): the current template in short, and what changed from the last
        summary = template_summary(current)
        workload["podSpec"] = summary
        workload["rolloutDiff"] = template_diff(
            template_summary(revisions[1]) if len(revisions) > 1 else None, summary
        )
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
                "cluster": {
                    "activeAlerts": await self._active_alerts(
                        (spec.get("source") or {}).get("alertname"), f"{ns}/{name}"
                    )
                },
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
        """current and previous: each pod's last lines of the last LOG_WINDOW (for previous, the
        terminated container's lines written in that window), appended pod after pod. sources: how
        many lines each pod gave, in that order, so a reader can split the lists per pod
        (the Reasoner's per-pod excerpt, ADR-042). Additive: evidence/v1.1 is unchanged."""
        logs: dict[str, list] = {"current": [], "previous": [], "sources": []}
        for row in pod_rows[:MAX_LOG_PODS]:
            source = {"pod": row["name"], "current": 0, "previous": 0}
            for key, previous in (("current", False), ("previous", True)):
                try:
                    text = await self.api.pod_log(
                        ns,
                        row["name"],
                        previous=previous,
                        tail=MAX_LOG_LINES,
                        since_seconds=int(LOG_WINDOW.total_seconds()),
                    )
                except (ApiError, *TRANSPORT_ERRORS):
                    text = None
                if text:
                    lines = text.splitlines()[-MAX_LOG_LINES:]
                    logs[key] += lines
                    source[key] = len(lines)
            logs["sources"].append(source)
        return logs

    async def _active_alerts(
        self, own: str | None, own_target: str
    ) -> list[dict] | None:
        """The other firing Nexus alerts in the cluster (evidence/v1.2): {alertname, target},
        sorted, at most MAX_ACTIVE_ALERTS; the Incident's own alert on its own target is left
        out. None when Prometheus is not configured or does not answer."""
        query = getattr(self.metrics, "vector", None)
        if query is None:
            return None
        try:
            series = await query(ACTIVE_ALERTS_QUERY)
        except (ApiError, *TRANSPORT_ERRORS, ValueError):
            return None
        if series is None:
            return None
        seen = set()
        for labels in series:
            if not isinstance(labels, dict):
                continue
            alert = labels.get("alertname")
            target = labels.get("nexus_target") or labels.get("namespace")
            if not isinstance(alert, str) or not isinstance(target, str):
                continue
            if (alert, target) == (own, own_target):
                continue
            seen.add((_cut(alert), _cut(target)))
        return [
            {"alertname": a, "target": t} for a, t in sorted(seen)[:MAX_ACTIVE_ALERTS]
        ]

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
        out: dict = {}
        for signal, rule in BASELINES.items():
            out[signal] = {
                "mean": await self._scalar(f"{rule}:baseline_avg15m", ns),
                "stddev": await self._scalar(f"{rule}:baseline_stddev15m", ns),
            }
        return out | {"capturedAt": iso(now)}
