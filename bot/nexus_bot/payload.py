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

More read-only buttons (2026-11-22, display changes), the same rules, at most 18 bytes:

    t:<namespace>   /pods as a table, in place (Refresh, or the Table toggle)
    k:<namespace>   /pods as cards, in place (Refresh, or the Cards toggle)
    m:<key>         🧠 Reasoning: the reasoning excerpt
    b:<key>         📚 Runbooks: the runbook chunks the Reasoner retrieved
    c:<key>         🔗 Commit: a commit the model named, unchecked
    l:<f>:<page>    /incident list, in place; f = a(ll), o(pen), d (nexus-dev), p (nexus-prod)
    v:pods, v:inc   /pods and /incident with no argument, as a new message (the /status buttons)

None of these reaches decide.py: they are answered with a Reader only.
"""

import re
from dataclasses import dataclass

from nexus_client.models import Incident

PATTERN = re.compile(r"^[aren]:[0-9a-f]{16}:[0-9a-f]{32}$")
VIEW = re.compile(r"^(?:([ptk]):(nexus-dev|nexus-prod)|([imbc]):([0-9a-f]{16})|l:([aodp]):([0-9]{1,2})"
                  r"|v:(pods|inc))$")
PODS, TABLE, CARDS = "pods", "table", "cards"
INCIDENT, REASONING, RUNBOOKS, COMMIT = "incident", "reasoning", "runbooks", "commit"
LIST, PICK = "list", "pick"
_KINDS = {"p": PODS, "t": TABLE, "k": CARDS, "i": INCIDENT, "m": REASONING, "b": RUNBOOKS, "c": COMMIT}
_LETTER = {kind: letter for letter, kind in _KINDS.items()}
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
    kind: str  # one of PODS, TABLE, CARDS, INCIDENT, REASONING, RUNBOOKS, COMMIT, LIST, PICK
    arg: str  # a namespace, a 16-hex UID key, a list filter letter, or "pods" / "inc"
    page: int = 0  # LIST only


def pods_data(namespace: str, kind: str = PODS) -> str | None:
    data = f"{_LETTER.get(kind, '?')}:{namespace}"
    return data if VIEW.fullmatch(data) else None


def incident_data(inc: Incident, kind: str = INCIDENT) -> str | None:
    key = uid_key(inc.metadata.uid)
    data = f"{_LETTER.get(kind, '?')}:{key}"
    return data if key is not None and VIEW.fullmatch(data) else None


def list_data(filt: str, page: int) -> str | None:
    data = f"l:{filt}:{page}"
    return data if VIEW.fullmatch(data) else None


def decode_view(data: object) -> View | None:
    m = VIEW.fullmatch(data) if isinstance(data, str) else None
    if m is None:
        return None
    if m[1]:
        return View(_KINDS[m[1]], m[2])
    if m[3]:
        return View(_KINDS[m[3]], m[4])
    if m[5]:
        return View(LIST, m[5], int(m[6]))
    return View(PICK, m[7])
