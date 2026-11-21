"""Approve, Reject and Explain presses (ADR-040 decision 7 and the press sequence, steps 3-8).

The bot keeps no table of what it showed. On a press it re-reads the live Incident as itself,
requires phase AwaitingApproval, no decision yet and a stored hash that starts with the 32 hex
in the payload, then writes the full hash with the resourceVersion it just read, as the
allowlisted approver. K5 stamps approvedBy and approvedAt and refuses anything else; C2 makes
the write once-only. A stale or repeated press writes nothing.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from nexus_client import approval as ap
from nexus_client.errors import Conflict, Denied, NexusClientError, NotFound, Unreachable, WrongPhase
from nexus_client.models import Incident
from nexusctl.render.sanitize import sanitize

from . import views
from .auth import Sender
from .kube import ApproverFactory, Reader
from .payload import APPROVE, EXPLAIN, REJECT, Press, hash_prefix, uid_key

DECISION = {APPROVE: "approved", REJECT: "rejected"}


@dataclass(frozen=True)
class Outcome:
    text: str
    remove_buttons: bool = False
    wrote: bool = False


def _find(reader: Reader, key: str) -> Incident | None:
    matches = [i for i in reader.list_incidents() if uid_key(i.metadata.uid) == key]
    return matches[0] if len(matches) == 1 else None


def handle_press(press: Press, sender: Sender, *, reader: Reader, approver_for: ApproverFactory,
                 group: str, now: Callable[[], datetime]) -> Outcome:
    try:
        inc = _find(reader, press.key)
    except NexusClientError as e:
        return Outcome(f"error: cannot read Incidents ({sanitize(e, 200)}); nothing was written")
    if inc is None:
        return Outcome("Incident not found (or not unique); nothing was written")
    name = sanitize(inc.metadata.name, 80)

    if press.action == EXPLAIN:
        return Outcome(views.explain(inc))
    if press.action not in DECISION:
        return Outcome("Note is not available in this build; nothing was written")

    try:
        ap.check_decidable(inc)
    except WrongPhase as e:
        return Outcome(f"{name}: no longer awaiting approval ({sanitize(e, 120)}); nothing was written",
                       remove_buttons=True)
    assert inc.status.proposal is not None  # check_decidable
    if hash_prefix(inc.status.proposal.hash) != press.h32:
        return Outcome(f"{name}: the proposal changed since this message; nothing was written",
                       remove_buttons=True)

    decision = DECISION[press.action]
    body = ap.build_approval(inc, ttl=ap.MAX_TTL, now=now(), decision=decision,
                             reason=f"telegram:{sender.user_id}")
    try:
        reply = approver_for(sender.approver, group).patch_approval(
            inc.metadata.name, body, inc.metadata.resourceVersion or "")
    except Denied as e:
        audit = f" (Audit-Id {sanitize(e.audit_id, 64)})" if e.audit_id else ""
        return Outcome(f"{name}: denied{audit}: {sanitize(e, 400)}")
    except Conflict:
        return Outcome(f"{name}: the Incident changed while you were deciding; nothing was written",
                       remove_buttons=True)
    except NotFound:
        return Outcome(f"{name}: the Incident is gone; nothing was written", remove_buttons=True)
    except Unreachable as e:
        return Outcome(f"{name}: cannot reach the API server ({sanitize(e, 200)}); nothing was written")
    except NexusClientError as e:
        return Outcome(f"{name}: error: {sanitize(e, 300)}")

    try:  # step 8: report what the cluster stored, read back as the bot itself
        stored = reader.get_incident(inc.metadata.name)
    except NexusClientError:
        stored = reply.incident
    a = stored.spec.approval
    if a is None:
        return Outcome(f"{name}: the write returned no approval (Audit-Id {sanitize(reply.audit_id or '-')})",
                       remove_buttons=True, wrote=True)
    expiry = f"; expires {sanitize(a.expiresAt, 30)}" if a.expiresAt else ""
    return Outcome(f"{name}: {sanitize(a.decision, 10)} by {sanitize(a.approvedBy or '-', 64)} at "
                   f"{sanitize(a.approvedAt or '-', 30)}{expiry}; "
                   f"pinned {sanitize(a.proposalHash or '-', 80)}; "
                   f"Audit-Id {sanitize(reply.audit_id or '-', 64)}",
                   remove_buttons=True, wrote=True)
