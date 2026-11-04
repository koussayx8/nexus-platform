"""Approval validation for an Incident in AwaitingApproval (spec §7, §9 stage 8, §16A).

Pure functions: no I/O, no clock of their own. The result is advisory. The authoritative copies
are K5 (who may approve, the proposal-hash binding, the expiry ceiling) and K3 (the request is
admitted only while an Incident pins it); this check fails early with a reason code and keeps the
operator from sending a request the Trusted Enforcement Plane would refuse.

  pending   no decision yet; keep waiting
  rejected  an Approver rejected the proposal                       -> Rejected / approval_rejected
  invalid   the approval does not pin what the operator holds       -> Blocked  / approval_rejected
  expired   expiresAt passed, or approvalTTL ran out with no answer -> Expired  / approval_expired
  valid     pinned values equal the proposal and the approval is unexpired
"""

import dataclasses
import datetime

from .model import parse_time

SKEW_S = 5  # K5 stamps approvedAt and tests the 15-minute ceiling at slightly different instants


@dataclasses.dataclass(frozen=True)
class Check:
    state: str  # pending | rejected | invalid | expired | valid
    detail: str = ""

    @property
    def reason(self) -> str | None:
        return {
            "rejected": "approval_rejected",
            "invalid": "approval_rejected",
            "expired": "approval_expired",
        }.get(self.state)

    @property
    def phase(self) -> str | None:
        return {"rejected": "Rejected", "invalid": "Blocked", "expired": "Expired"}.get(
            self.state
        )


def _time(value) -> datetime.datetime | None:
    try:
        return parse_time(value) if isinstance(value, str) else None
    except ValueError:
        return None


def waited_out(incident: dict, at: datetime.datetime, ttl_s: int) -> bool:
    """True if the Incident has waited approvalTTL in AwaitingApproval with no decision."""
    approval = (incident.get("spec") or {}).get("approval") or {}
    if approval.get("decision"):
        return False
    entered = _time(
        ((incident.get("status") or {}).get("timestamps") or {}).get("awaitingapproval")
    )
    return entered is not None and (at - entered).total_seconds() > ttl_s


def check(incident: dict, doc: dict, at: datetime.datetime, ttl_s: int) -> Check:
    """Judge spec.approval against status.proposal and the stored proposal/v1 document."""
    approval = (incident.get("spec") or {}).get("approval") or {}
    decision = approval.get("decision")
    if not decision:
        if waited_out(incident, at, ttl_s):
            return Check("expired", f"no decision within approvalTTL {ttl_s}s")
        return Check("pending")
    if (
        decision == "rejected"
    ):  # rejecting needs no binding: it can only withdraw authority
        return Check("rejected", str(approval.get("reason") or ""))
    if decision != "approved":
        return Check("invalid", f"unknown decision {decision!r}")
    held = ((incident.get("status") or {}).get("proposal") or {}).get("hash")
    if not held or approval.get("proposalHash") != held:
        return Check("invalid", "approval.proposalHash is not the proposal's hash")
    action = doc["action"]
    if approval.get("actionId") != action["id"]:
        return Check("invalid", "approval.actionId is not the proposal's action")
    if (approval.get("parameters") or {}) != (action.get("parameters") or {}):
        return Check("invalid", "approval.parameters are not the proposal's parameters")
    approved_at = _time(approval.get("approvedAt"))
    if not approval.get("approvedBy") or approved_at is None:
        return Check("invalid", "approvedBy or approvedAt was not stamped")
    expires = _time(approval.get("expiresAt"))
    if expires is None:
        return Check("invalid", "approval.expiresAt is missing")
    if (expires - approved_at).total_seconds() > ttl_s + SKEW_S:
        return Check("invalid", f"approval lasts longer than approvalTTL {ttl_s}s")
    if at >= expires:
        return Check("expired", f"expired at {approval['expiresAt']}")
    return Check("valid")
