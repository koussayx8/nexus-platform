"""Pre-checks before a run (spec §20 "Reset and pre-checks", §25; TASKS.md M4; ADR-024). Stdlib only.

Implemented here, all of which must hold, waited for at most the cap (20 min by the spec). Every
check is READ-ONLY (get / get --raw / GET / stat); none changes anything:
  * clean baseline: for every signal in the nexus-detection rules, |Z| < 1.5 and the recorded
    `baseline_stddev15m` is within 1.5 x that signal's clean baseline (a value from the
    pre-registration), otherwise the previous fault still masks the next one. A series with no
    answer (the rule needs 27 of 30 samples, ADR-024) is NOT clean.
  * node load: CPU below 60 % and memory below 80 %.
  * no non-terminal Incident. Incidents are never deleted by the runner (rule 3): the check waits,
    inside the same cap, and when the cap runs out the batch STOPS (it does not discard and carry
    on), with the reason.
  * sample-api 2 of 2 Ready: the Deployment's desired, ready and updated replicas all equal 2.
  * Kill Switch `active`: ConfigMap nexus-killswitch, data.state; anything else, or absent, fails
    (the operator treats a missing ConfigMap as halted, spec §16).
  * the namespace level is the cell's level: label nexus.io/autonomy-level on nexus-dev (a missing
    label is L0, fail closed, as the policies read it).
  * Backstage at zero: no pod in the backstage namespace.
  * Locust at its baseline, within +-10 %: the swarm is `running` with the configured number of
    users, and sample-api's request rate in each namespace is within the tolerance of the
    configured per-namespace baseline (1 user = 1 request/s, ADR-026). The spec says only "Locust
    baseline within +-10 %": reading it as both the user count and the server-side rate is UNVERIFIED.
  * Reasoner ready with the expected index hash: GET /healthz through the API server's service
    proxy. The service (reasoner/nexus_reasoner/service.py) answers 200
    {"status": "ready", "corpusHash": "<64 hex>", "promptVersion": ..., "scoreFloor": ...}; the
    check needs `status` "ready" and `corpusHash` equal to the configured hash (a `sha256:` prefix
    on the configured value is ignored).
  * the Flight Recorder is reachable from the operator: Prometheus `nexus_operator_recorder_up`
    (1 while the operator's last recorder call or its 30 s SELECT 1 probe succeeded) is exactly 1.
    The operator blocks every mutation without the recorder (write-ahead intent row, spec §14), so
    a run started with it down would only record `recorder_unavailable`. Fail closed: no answer,
    no series, NaN, or any series other than 1 fails the pre-check.
  * the audit log is being written: the file is readable and was modified within `max_age_s`. k3s
    writes every write request to it, lease renewals included (verify-state.sh M7), so a quiet log
    means the audit chain is down. Reading the file is all this does: no probe request is sent.
Not implemented: "Hosted Model reachable for LLM arms" (needs the provider key, rule 5).

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
import os
import re
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
SECTION_DEFAULTS = {
    "sample_api": {"namespace": "nexus-dev", "deployment": "sample-api", "replicas": 2},
    "killswitch": {"namespace": "nexus-system", "configmap": "nexus-killswitch"},
    "backstage": {"namespace": "backstage"},
    "locust": {"tolerance": 0.10, "namespaces": ["nexus-dev", "nexus-prod"]},
    "reasoner": {
        "namespace": "nexus-reasoner",
        "service": "nexus-reasoner",
        "port": 8080,
    },
    "audit_log": {"path": "/var/log/nexus-audit/audit.log", "max_age_s": 60},
}
# no default: they come from the pre-registration / the calibration (G16)
SECTION_REQUIRED = {
    "locust": ("baseline_users", "baseline_rps_per_namespace"),
    "reasoner": ("expected_index_hash",),
}
LEVEL_LABEL = "nexus.io/autonomy-level"


class ConfigError(ValueError):
    pass


def _number(v, name, positive=True):
    if (
        isinstance(v, bool)
        or not isinstance(v, int | float)
        or math.isnan(v)
        or (v <= 0 if positive else v < 0)
    ):
        raise ConfigError(
            f"precheck.{name} must be a {'positive' if positive else 'non-negative'} number"
        )


def normalise(cfg: dict | None) -> dict:
    """The pre-check configuration with the spec's values filled in. `clean_stddev` (a number per
    signal), `locust.baseline_users`, `locust.baseline_rps_per_namespace` and
    `reasoner.expected_index_hash` have no default: they come from the pre-registration (G16)."""
    if not isinstance(cfg, dict):
        raise ConfigError("precheck configuration is missing")
    out = {**DEFAULTS, **cfg}
    clean = out.get("clean_stddev")
    if not isinstance(clean, dict) or set(clean) != set(SIGNALS):
        raise ConfigError(
            f"precheck.clean_stddev needs exactly the signals {sorted(SIGNALS)}"
        )
    for name, v in clean.items():
        _number(v, f"clean_stddev.{name}", positive=False)
    for key in (
        "cap_s",
        "poll_s",
        "z_max",
        "stddev_factor",
        "node_cpu_max",
        "node_mem_max",
    ):
        _number(out[key], key)
    if not isinstance(out["namespace"], str) or not out["namespace"]:
        raise ConfigError("precheck.namespace must be a non-empty string")
    for sec, defaults in SECTION_DEFAULTS.items():
        given = out.get(sec) or {}
        if not isinstance(given, dict):
            raise ConfigError(f"precheck.{sec} must be an object")
        out[sec] = {**defaults, **given}
        for k in SECTION_REQUIRED.get(sec, ()):
            if k not in out[sec]:
                raise ConfigError(f"precheck.{sec}.{k} is required (G16 sets it)")
    sa, loc, rs, au = (
        out["sample_api"],
        out["locust"],
        out["reasoner"],
        out["audit_log"],
    )
    _number(sa["replicas"], "sample_api.replicas")
    _number(loc["baseline_users"], "locust.baseline_users")
    _number(loc["baseline_rps_per_namespace"], "locust.baseline_rps_per_namespace")
    _number(loc["tolerance"], "locust.tolerance")
    # Optional Locust recovery (batch.py): the exact baseline swarm as platform/load/master.yaml autostarts it.
    if "spawn_rate" in loc:
        _number(loc["spawn_rate"], "locust.spawn_rate")
    if "classes" in loc and (
        not isinstance(loc["classes"], list)
        or not loc["classes"]
        or not all(isinstance(c, str) and c for c in loc["classes"])
    ):
        raise ConfigError(
            "precheck.locust.classes must be a non-empty list of class names"
        )
    if "recovery_attempts" in loc:
        _number(loc["recovery_attempts"], "locust.recovery_attempts", positive=False)
    if "recovery_wait_s" in loc:
        _number(loc["recovery_wait_s"], "locust.recovery_wait_s", positive=False)
    _number(rs["port"], "reasoner.port")
    _number(au["max_age_s"], "audit_log.max_age_s")
    if _bare_hash(rs["expected_index_hash"]) is None:
        raise ConfigError(
            "precheck.reasoner.expected_index_hash must be the 64-hex corpus hash (`make index`)"
        )
    for sec, key in (("sample_api", "namespace"), ("sample_api", "deployment"), ("killswitch", "namespace"),
                     ("killswitch", "configmap"), ("backstage", "namespace"), ("reasoner", "namespace"),
                     ("reasoner", "service"), ("audit_log", "path")):  # fmt: skip
        if not isinstance(out[sec][key], str) or not out[sec][key]:
            raise ConfigError(f"precheck.{sec}.{key} must be a non-empty string")
    if not loc["namespaces"] or not all(
        isinstance(n, str) and n for n in loc["namespaces"]
    ):
        raise ConfigError(
            "precheck.locust.namespaces must be a non-empty list of names"
        )
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


def _kubectl(runner, *args, ns=None) -> tuple[int, str]:
    base = [CLI, "--context", runner.context] + (["-n", ns] if ns else [])
    return runner.exec(base + list(args))


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def sample_api_failures(runner, cfg: dict) -> list[str]:
    c = cfg["sample_api"]
    rc, out = _kubectl(
        runner, "get", "deployment", c["deployment"], "-o", "json", ns=c["namespace"]
    )
    doc = _json(out) if rc == 0 else None
    if not isinstance(doc, dict):
        return ["sample_api:no_answer"]
    want = int(c["replicas"])
    st = doc.get("status") or {}
    got = (
        (doc.get("spec") or {}).get("replicas", 0) or 0,
        st.get("readyReplicas", 0) or 0,
        st.get("updatedReplicas", 0) or 0,
    )
    if got != (want, want, want):
        return [
            f"sample_api:desired/ready/updated={got[0]}/{got[1]}/{got[2]},want={want}"
        ]
    return []


def killswitch_failures(runner, cfg: dict) -> list[str]:
    c = cfg["killswitch"]
    rc, out = _kubectl(
        runner, "get", "configmap", c["configmap"], "-o", "json", ns=c["namespace"]
    )
    doc = _json(out) if rc == 0 else None
    if not isinstance(doc, dict):
        return ["killswitch:missing"]  # missing is halted (spec section 16)
    state = (doc.get("data") or {}).get("state")
    return [] if state == "active" else [f"killswitch:state={state!r}"]


def namespace_level_failures(runner, cfg: dict, level: int) -> list[str]:
    ns = cfg["namespace"]
    rc, out = _kubectl(runner, "get", "namespace", ns, "-o", "json")
    doc = _json(out) if rc == 0 else None
    if not isinstance(doc, dict):
        return ["namespace_level:no_answer"]
    got = ((doc.get("metadata") or {}).get("labels") or {}).get(
        LEVEL_LABEL, "0"
    )  # missing = L0
    return [] if str(got) == str(level) else [f"namespace_level:{got}!={level}"]


def backstage_failures(runner, cfg: dict) -> list[str]:
    rc, out = _kubectl(
        runner, "get", "pods", "-o", "json", ns=cfg["backstage"]["namespace"]
    )
    doc = _json(out) if rc == 0 else None
    if not isinstance(doc, dict):
        return ["backstage:no_answer"]
    n = len(doc.get("items") or [])
    return [] if n == 0 else [f"backstage:pods={n}"]


def request_rate(prom: Prom, namespace: str) -> float | None:
    """sample-api's request rate in a namespace as the server counts it (2 m rate); None: no answer."""
    return prom.scalar(
        f'namespace:nexus_sample_api_requests:rate2m{{namespace="{namespace}"}}'
    )


def locust_failures(runner, prom: Prom, cfg: dict) -> list[str]:
    c = cfg["locust"]
    if not runner.locust:
        return ["locust:no_url"]
    code, body = runner.http_get(f"{runner.locust.rstrip('/')}/stats/requests")
    doc = _json(body) if code == 200 else None
    if not isinstance(doc, dict):
        return ["locust:no_answer"]
    out = []
    tol = c["tolerance"]
    if doc.get("state") != "running":
        out.append(f"locust:state={doc.get('state')!r}")
    users = doc.get("user_count")
    if (
        not isinstance(users, int | float)
        or abs(users - c["baseline_users"]) > tol * c["baseline_users"]
    ):
        out.append(f"locust:users={users}!~{c['baseline_users']}")
    for ns in c[
        "namespaces"
    ]:  # the server's view: Locust's own rate lags by design (ADR-026)
        rate = request_rate(prom, ns)
        base = c["baseline_rps_per_namespace"]
        if rate is None:
            out.append(f"locust:{ns}:no_rate")
        elif abs(rate - base) > tol * base:
            out.append(f"locust:{ns}:rps={rate:.1f}!~{base}")
    return out


def _bare_hash(h) -> str | None:
    """The corpus hash without an optional `sha256:` prefix (the Reasoner returns bare hex, the
    spec's examples carry the prefix); None unless what is left is 64 lowercase hex digits."""
    if not isinstance(h, str):
        return None
    h = h.removeprefix("sha256:")
    return h if re.fullmatch(r"[0-9a-f]{64}", h) else None


def reasoner_failures(runner, cfg: dict) -> list[str]:
    """GET /healthz of the Reasoner service (reasoner/nexus_reasoner/service.py, G9), which answers
    200 {"status": "ready", "corpusHash": "<64 hex>", "promptVersion": ..., "scoreFloor": ...} once
    its index is loaded. A Pod whose index is missing never serves it, so an unreachable or non-200
    answer is "not ready"."""
    c = cfg["reasoner"]
    path = f"/api/v1/namespaces/{c['namespace']}/services/http:{c['service']}:{c['port']}/proxy/healthz"
    rc, out = _kubectl(runner, "get", "--raw", path)
    if rc != 0:
        return ["reasoner:not_ready"]
    doc = _json(out)
    if not isinstance(doc, dict):
        return ["reasoner:healthz_unparsable"]
    if doc.get("status") != "ready":
        return ["reasoner:not_ready"]
    got = _bare_hash(doc.get("corpusHash"))
    if got is None:
        return ["reasoner:no_index_hash"]
    return (
        []
        if got == _bare_hash(c["expected_index_hash"])
        else [f"reasoner:index_hash={got}"]
    )


RECORDER_UP = "nexus_operator_recorder_up"


def recorder_failures(prom: Prom) -> list[str]:
    """nexus_operator_recorder_up must be present and equal 1 on every series. A series that is
    missing (the operator is down or not scraped) is a failure, never a pass."""
    res = prom.query(RECORDER_UP)
    if res is None:
        return ["recorder:no_answer"]
    if not res:
        return ["recorder:no_series"]
    out = []
    for sample in res:
        try:
            v = float(sample["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            out.append("recorder:unparsable")
            continue
        if v != 1.0:  # also true for NaN
            out.append(f"recorder:up={v:g}")
    return out


def audit_failures(cfg: dict, stat, wall) -> list[str]:
    c = cfg["audit_log"]
    try:
        mtime = stat(c["path"])
    except OSError:
        return ["audit_log:unreadable"]
    age = wall() - mtime
    return [] if age <= c["max_age_s"] else [f"audit_log:stale={age:.0f}s"]


def _file_mtime(path: str) -> float:
    if not os.access(path, os.R_OK):
        raise PermissionError(path)
    return os.stat(path).st_mtime


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


def run_prechecks(prom: Prom, cfg: dict, incidents, before_uids: set, terminal: frozenset, level: int,
                  monotonic=time.monotonic, wall=time.time, sleep=time.sleep, stat=_file_mtime) -> Result:  # fmt: skip
    """Poll until every check holds or the cap runs out. `incidents()` lists the Incident objects;
    `before_uids` are the Incidents that existed before the run's reset (so a new one is a false
    alarm); `terminal` are the phases that end an Incident; `level` is the cell's autonomy level."""
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
        r = prom.r
        failures += sample_api_failures(r, cfg)
        failures += killswitch_failures(r, cfg)
        failures += namespace_level_failures(r, cfg, level)
        failures += backstage_failures(r, cfg)
        failures += locust_failures(r, prom, cfg)
        failures += reasoner_failures(r, cfg)
        failures += recorder_failures(prom)
        failures += audit_failures(cfg, stat, wall)
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
