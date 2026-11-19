"""The button payload (ADR-040 "Button payload and the press sequence").

    <letter>:<key>:<h32>     51 bytes of Telegram's 64
    letter  a = Approve, r = Reject, e = Explain, n = Note (Block 8)
    key     the first 16 hex of the Incident's metadata.uid, dashes removed
    h32     the first 32 hex of status.proposal.hash, after "sha256:"

The payload is the bot's whole memory of a message: a restart loses nothing.
"""

import re
from dataclasses import dataclass

from nexus_client.models import Incident

PATTERN = re.compile(r"^[aren]:[0-9a-f]{16}:[0-9a-f]{32}$")
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
