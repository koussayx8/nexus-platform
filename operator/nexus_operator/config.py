"""nexus-operator-config: the admin-owned ConfigMap the operator reads at startup (ADR-019).

Defaults exist only where the spec freezes the value: the 10 s poll and the 5 s reconcile (§4,
runtime settings) and the 20 s Detected timeout (§7). alertmanagerURL has no frozen value, so it
is required. The M2 keys (advisoryChecks, approvalTTL, breakerEpoch, reasonerEndpoint) are read
by M2; M1b ignores them. A change takes effect at the next operator start.
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
    return Config(
        alertmanager_url=url,
        alert_poll_s=_seconds(data, "alertPollSeconds", 10),
        reconcile_s=_seconds(data, "reconcileSeconds", 5),
        detected_timeout_s=_seconds(data, "detectedTimeoutSeconds", 20),
    )
