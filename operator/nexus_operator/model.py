"""Names, phases and time helpers shared by the poller and the reconciler (spec §7, §8)."""

import datetime
import re

GROUP, VERSION, PLURAL = "nexus.io", "v1alpha1", "incidents"
INCIDENTS = (GROUP, VERSION, PLURAL)
NAMESPACE = "nexus-system"

LEVEL_LABEL = "nexus.io/autonomy-level"
FINGERPRINT_LABEL = "nexus.io/fingerprint"
SMOKE_LABEL = "nexus.io/smoke"

# CRD Validation C3: the only namespaces an Incident may target.
TARGET_NAMESPACES = frozenset({"nexus-dev", "nexus-prod", "nexus-data"})

# §7: every phase that ends an Incident. A terminal Incident is never written again.
TERMINAL_PHASES = frozenset(
    {
        "Recorded",
        "Recommended",
        "Dismissed",
        "Resolved",
        "Escalated",
        "Blocked",
        "Rejected",
        "Expired",
    }
)

_RFC3339 = re.compile(
    r"(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(?:\.(\d+))?([Zz]|[+-]\d{2}:\d{2})"
)


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_time(value: str) -> datetime.datetime:
    """An RFC 3339 timestamp as an aware UTC datetime, fractions cut to microseconds.

    Alertmanager sends startsAt with up to nanoseconds; Python keeps microseconds. Raises
    ValueError on anything else.
    """
    m = _RFC3339.fullmatch(value or "")
    if not m:
        raise ValueError(f"not an RFC 3339 timestamp: {value!r}")
    date, clock, frac, tz = m.groups()
    frac = (frac or "")[:6].ljust(6, "0")
    tz = "+00:00" if tz in ("Z", "z") else tz
    parsed = datetime.datetime.fromisoformat(f"{date}T{clock}.{frac}{tz}")
    return parsed.astimezone(datetime.timezone.utc)


def iso(t: datetime.datetime) -> str:
    """Seconds precision, as Kubernetes writes creationTimestamp."""
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_terminal(incident: dict) -> bool:
    return (incident.get("status") or {}).get("phase") in TERMINAL_PHASES
