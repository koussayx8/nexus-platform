"""nexus-operator-config: the admin-owned ConfigMap the operator reads at startup (ADR-019).

Defaults exist only where the spec freezes the value: the 10 s poll and the 5 s reconcile (§4,
runtime settings) and the 20 s Detected timeout (§7). alertmanagerURL has no frozen value, so it
is required. The act-path keys are read here: advisoryChecks (on by default, §4), breakerEpoch
(Unix seconds; 0 counts from the start — ASSUMPTION: §4 gives no unit) and prometheusURL (the
Verifier's metrics source; empty means the Verifier cannot measure, so no repair verifies).
approvalTTL and reasonerEndpoint stay unread until M3 and M4. A change takes effect at the
next operator start.
"""

import dataclasses
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
    return Config(
        alertmanager_url=url,
        advisory_checks=advisory == "on",
        breaker_epoch=epoch,
        prometheus_url=prom,
        alert_poll_s=_seconds(data, "alertPollSeconds", 10),
        reconcile_s=_seconds(data, "reconcileSeconds", 5),
        detected_timeout_s=_seconds(data, "detectedTimeoutSeconds", 20),
    )
