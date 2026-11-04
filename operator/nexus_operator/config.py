"""nexus-operator-config: the admin-owned ConfigMap the operator reads at startup (ADR-019).

Defaults exist only where the spec freezes the value: the 10 s poll and the 5 s reconcile (§4,
runtime settings) and the 20 s Detected timeout (§7). alertmanagerURL has no frozen value, so it
is required. The act-path keys are read here: advisoryChecks (on by default, §4), breakerEpoch
(Unix seconds; 0 counts from the start — ASSUMPTION: §4 gives no unit) and prometheusURL (the
Verifier's metrics source; empty means the Verifier cannot measure, so no repair verifies).
The Writer's keys (recorderHost, recorderPort, recorderDatabase, recorderUser,
recorderPasswordFile; ADR-031) say where the Flight Recorder DB is and which mounted file holds the
nexus_writer password; the password is never a ConfigMap value. metricsPort is the /metrics port.
The Reasoner Client reads reasonerEndpoint (empty: off), reasonerMode, reasonerModel and
reasonerEffort. approvalTTL stays unread until M3. A change takes effect at the
next operator start.
"""

import dataclasses
import re
import urllib.parse

CONFIGMAP = "nexus-operator-config"


class ConfigError(ValueError):
    """The ConfigMap is missing or holds a value the operator cannot use."""


@dataclasses.dataclass(frozen=True)
class Config:
    alertmanager_url: str
    alert_poll_s: int = 10
    reconcile_s: int = 5
    detected_timeout_s: int = 20
    advisory_checks: bool = True
    breaker_epoch: int = 0
    prometheus_url: str = ""
    recorder_host: str = (
        ""  # empty: no Writer is configured, so every mutation is blocked
    )
    recorder_port: int = 5432
    recorder_database: str = "nexus_recorder"
    recorder_user: str = "nexus_writer"
    recorder_password_file: str = "/var/run/nexus/flight-recorder-writer/password"
    metrics_port: int = 9102
    reasoner_endpoint: str = (
        ""  # empty: the Reasoner Client is off, Diagnosing is R1 only
    )
    reasoner_mode: str = "llm"  # rules | llm | adversarial (the Reasoner's own modes)
    reasoner_model: str = ""
    reasoner_effort: str | None = None


def _seconds(data: dict, key: str, default: int) -> int:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ConfigError(f"{key} is not an integer: {raw!r}") from None
    if not 1 <= value <= 3600:
        raise ConfigError(f"{key} is out of range [1, 3600]: {value}")
    return value


def _port(data: dict, key: str, default: int) -> int:
    raw = data.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ConfigError(f"{key} is not an integer: {raw!r}") from None
    if not 1 <= value <= 65535:
        raise ConfigError(f"{key} is out of range [1, 65535]: {value}")
    return value


def parse(configmap: dict | None) -> Config:
    if configmap is None:
        raise ConfigError(f"ConfigMap {CONFIGMAP} not found")
    data = configmap.get("data") or {}
    url = str(data.get("alertmanagerURL") or "").strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"alertmanagerURL is not an http(s) URL: {url!r}")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ConfigError("alertmanagerURL carries a query, fragment or credentials")
    advisory = str(data.get("advisoryChecks") or "on").strip().lower()
    if advisory not in ("on", "off"):
        raise ConfigError(f"advisoryChecks is not on or off: {advisory!r}")
    try:
        epoch = int(str(data.get("breakerEpoch") or "0").strip())
    except ValueError:
        raise ConfigError(
            f"breakerEpoch is not an integer: {data.get('breakerEpoch')!r}"
        ) from None
    if epoch < 0:
        raise ConfigError(f"breakerEpoch is negative: {epoch}")
    prom = str(data.get("prometheusURL") or "").strip().rstrip("/")
    if prom:
        pp = urllib.parse.urlsplit(prom)
        if pp.scheme not in ("http", "https") or not pp.hostname or pp.username:
            raise ConfigError(f"prometheusURL is not an http(s) URL: {prom!r}")
    host = str(data.get("recorderHost") or "").strip()
    if host and not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise ConfigError(f"recorderHost is not a host name: {host!r}")
    port = _port(data, "recorderPort", 5432)
    pw_file = str(data.get("recorderPasswordFile") or "").strip()
    if pw_file and not pw_file.startswith("/"):
        raise ConfigError(f"recorderPasswordFile is not an absolute path: {pw_file!r}")
    reasoner = str(data.get("reasonerEndpoint") or "").strip().rstrip("/")
    if reasoner:
        rp = urllib.parse.urlsplit(reasoner)
        if (
            rp.scheme not in ("http", "https")
            or not rp.hostname
            or rp.username
            or rp.query
        ):
            raise ConfigError(f"reasonerEndpoint is not an http(s) URL: {reasoner!r}")
    mode = str(data.get("reasonerMode") or "llm").strip()
    if mode not in ("rules", "llm", "adversarial"):
        raise ConfigError(f"reasonerMode is not rules, llm or adversarial: {mode!r}")
    model = str(data.get("reasonerModel") or "").strip()
    if reasoner and mode == "llm" and not model:
        raise ConfigError("reasonerModel is required for reasonerMode llm")
    effort = str(data.get("reasonerEffort") or "").strip() or None
    if effort not in (None, "low", "medium", "high"):
        raise ConfigError(f"reasonerEffort is not low, medium or high: {effort!r}")
    return Config(
        alertmanager_url=url,
        reasoner_endpoint=reasoner,
        reasoner_mode=mode,
        reasoner_model=model,
        reasoner_effort=effort,
        recorder_host=host,
        recorder_port=port,
        recorder_database=str(data.get("recorderDatabase") or "nexus_recorder").strip(),
        recorder_user=str(data.get("recorderUser") or "nexus_writer").strip(),
        recorder_password_file=pw_file or Config.recorder_password_file,
        metrics_port=_port(data, "metricsPort", 9102),
        advisory_checks=advisory == "on",
        breaker_epoch=epoch,
        prometheus_url=prom,
        alert_poll_s=_seconds(data, "alertPollSeconds", 10),
        reconcile_s=_seconds(data, "reconcileSeconds", 5),
        detected_timeout_s=_seconds(data, "detectedTimeoutSeconds", 20),
    )
