"""Approval pinning (spec §16A "What you see is what you approve"; §11 K5).

Pure functions: no I/O, an injectable clock. The approval written pins exactly the action,
parameters and proposal hash the Approver was shown, with an expiry K5 bounds at 15 minutes.
Admission (K5, CRD C2) stays the authority; these functions only make a correct request.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import StaleProposal, WrongPhase
from .models import Incident

MAX_TTL = timedelta(minutes=15)  # K5: an expiry more than 15 minutes ahead is rejected
# The API server's clock may run slightly behind the client's. An expiry of exactly now + 15m can
# land a few seconds past K5's bound, so the client keeps this much inside it.
CLOCK_MARGIN = timedelta(seconds=15)
AWAITING = "AwaitingApproval"

_TTL = re.compile(r"^(\d+)(s|m)$")


def parse_ttl(text: str) -> timedelta:
    """'15m' or '90s'. Raises ValueError for anything else, zero, or more than MAX_TTL."""
    m = _TTL.match(text.strip())
    if not m:
        raise ValueError(f"invalid ttl {text!r}: use for example 10m or 90s")
    ttl = timedelta(**{"seconds" if m[2] == "s" else "minutes": int(m[1])})
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")
    if ttl > MAX_TTL:
        raise ValueError("ttl may not exceed 15m (Kyverno K5 enforces the same bound)")
    return ttl


def rfc3339(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_decidable(inc: Incident) -> None:
    """Phase AwaitingApproval, a proposal present, no decision recorded yet."""
    if inc.status.phase != AWAITING:
        raise WrongPhase(f"phase is {inc.status.phase or 'unset'}, not {AWAITING}")
    if inc.status.proposal is None:
        raise WrongPhase("the Incident has no proposal")
    if inc.spec.approval is not None:
        raise WrongPhase(f"already decided: {inc.spec.approval.decision}")


def check_same_proposal(shown: Incident, now: Incident) -> None:
    """The Incident re-read just before the write must carry the proposal that was shown."""
    if shown.status.proposal is None or now.status.proposal is None:
        raise StaleProposal("the proposal disappeared since it was displayed")
    if shown.status.proposal.hash != now.status.proposal.hash:
        raise StaleProposal(
            f"the proposal hash changed since it was displayed "
            f"({shown.status.proposal.hash} -> {now.status.proposal.hash})"
        )


def build_approval(
    inc: Incident, *, ttl: timedelta, now: datetime, decision: str, reason: str | None = None
) -> dict[str, Any]:
    """The spec.approval to write. Never includes approvedBy or approvedAt (K5 stamps them)."""
    p = inc.status.proposal
    if p is None:
        raise WrongPhase("the Incident has no proposal")
    if decision == "rejected":
        out: dict[str, Any] = {"decision": "rejected", "proposalHash": p.hash}
        if reason:
            out["reason"] = reason
        return out
    if decision != "approved":
        raise ValueError(f"unknown decision {decision!r}")
    expires = now + min(ttl, MAX_TTL) - CLOCK_MARGIN
    out = {
        "decision": "approved",
        "actionId": p.actionId,
        "parameters": dict(p.parameters),
        "proposalHash": p.hash,
        "expiresAt": rfc3339(expires),
    }
    if reason:
        out["reason"] = reason
    return out
