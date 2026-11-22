"""The button payload (ADR-040 "Button payload and the press sequence").

    <letter>:<key>:<h32>     51 bytes of Telegram's 64
    letter  a = Approve, r = Reject, e = Explain, n = Note (Block 8)
    key     the first 16 hex of the Incident's metadata.uid, dashes removed
    h32     the first 32 hex of status.proposal.hash, after "sha256:"

The payload is the bot's whole memory of a message: a restart loses nothing.

Two read-only buttons (2026-11-22), handled by the same sender check as a press and answered with a
Reader only:

    p:<namespace>   /pods for nexus-dev or nexus-prod (the only two values)
    i:<key>         /incident for the Incident whose UID starts with key (16 hex, as above)
"""

import re
from dataclasses import dataclass

from nexus_client.models import Incident

PATTERN = re.compile(r"^[aren]:[0-9a-f]{16}:[0-9a-f]{32}$")
VIEW = re.compile(r"^(?:p:(nexus-dev|nexus-prod)|i:([0-9a-f]{16}))$")
PODS, INCIDENT = "pods", "incident"
APPROVE, REJECT, EXPLAIN, NOTE = "a", "r", "e", "n"
_HASH = re.compile(r"^sha256:([0-9a-f]{64})$")


@dataclass(frozen=True)
class Press:
    action: str
    key: str
    h32: str


def uid_key(uid: str | None) -> str | None:
    text = (uid or "").replace("-", "").lower()
    return text[:16] if re.fullmatch(r"[0-9a-f]{32}", text) else None


def hash_prefix(proposal_hash: str | None) -> str | None:
    m = _HASH.fullmatch(proposal_hash or "")
    return m[1][:32] if m else None


def encode(action: str, inc: Incident) -> str | None:
    """None when the Incident has no well-formed UID or proposal hash: then no button is offered."""
    key = uid_key(inc.metadata.uid)
    h32 = hash_prefix(inc.status.proposal.hash if inc.status.proposal else None)
    if key is None or h32 is None:
        return None
    data = f"{action}:{key}:{h32}"
    return data if PATTERN.fullmatch(data) else None


def decode(data: object) -> Press | None:
    if not isinstance(data, str) or not PATTERN.fullmatch(data):
        return None
    action, key, h32 = data.split(":")
    return Press(action, key, h32)


@dataclass(frozen=True)
class View:
    kind: str  # PODS or INCIDENT
    arg: str  # a namespace, or a 16-hex UID key


def pods_data(namespace: str) -> str | None:
    data = f"p:{namespace}"
    return data if VIEW.fullmatch(data) else None


def incident_data(inc: Incident) -> str | None:
    key = uid_key(inc.metadata.uid)
    return f"i:{key}" if key is not None else None


def decode_view(data: object) -> View | None:
    m = VIEW.fullmatch(data) if isinstance(data, str) else None
    if m is None:
        return None
    return View(PODS, m[1]) if m[1] else View(INCIDENT, m[2])
