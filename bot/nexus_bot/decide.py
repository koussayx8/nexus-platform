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

from . import views
from .auth import Sender
from .kube import ApproverFactory, Reader
from .payload import APPROVE, EXPLAIN, REJECT, Press, hash_prefix, uid_key
from .richtext import RichText

DECISION = {APPROVE: "approved", REJECT: "rejected"}
NOTHING = "Nothing was written."


@dataclass(frozen=True)
class Outcome:
    message: RichText
    remove_buttons: bool = False
    wrote: bool = False

    @property
    def text(self) -> str:
        return self.message.build().text


def _say(title: str, inc: Incident | None = None, *, why: str | None = None,
         error: object = None, audit: object = None, nothing: bool = True) -> RichText:
    """A short reply: icon and title, the Incident, one sentence, the error in a blockquote."""
    rt = RichText().title(title)
    if inc is not None:
        rt.fact("Incident", views.short_name(inc.metadata.name), 40)
    if audit:
        rt.fact("Audit-Id", audit, 64)
    if why:
        rt.text(why).nl()
    if error is not None:
        views.detail(rt, "Detail", error, 400)
    if nothing:
        rt.italic(NOTHING)
    return rt


def _find(reader: Reader, key: str) -> Incident | None:
    matches = [i for i in reader.list_incidents() if uid_key(i.metadata.uid) == key]
    return matches[0] if len(matches) == 1 else None


def handle_press(press: Press, sender: Sender, *, reader: Reader, approver_for: ApproverFactory,
                 group: str, now: Callable[[], datetime]) -> Outcome:
    try:
        inc = _find(reader, press.key)
    except NexusClientError as e:
        return Outcome(_say("🔴 Cannot read Incidents", error=e))
    if inc is None:
        return Outcome(_say("⚪ Incident not found",
                            why="No Incident, or more than one, matches this button."))

    if press.action == EXPLAIN:
        return Outcome(views.explain(inc))
    if press.action not in DECISION:
        return Outcome(_say("⚪ Note is not available in this build", inc))

    try:
        ap.check_decidable(inc)
    except WrongPhase as e:
        return Outcome(_say("⚪ No longer awaiting approval", inc, error=e), remove_buttons=True)
    assert inc.status.proposal is not None  # check_decidable
    if hash_prefix(inc.status.proposal.hash) != press.h32:
        return Outcome(_say("⚪ Proposal changed", inc, why="The proposal changed since this message."),
                       remove_buttons=True)

    decision = DECISION[press.action]
    body = ap.build_approval(inc, ttl=ap.MAX_TTL, now=now(), decision=decision,
                             reason=f"telegram:{sender.user_id}")
    try:
        reply = approver_for(sender.approver, group).patch_approval(
            inc.metadata.name, body, inc.metadata.resourceVersion or "")
    except Denied as e:
        return Outcome(_say("⛔ Denied", inc, error=e, audit=e.audit_id))
    except Conflict:
        return Outcome(_say("⚪ Changed while you were deciding", inc,
                            why="The Incident changed between the read and the write."), remove_buttons=True)
    except NotFound:
        return Outcome(_say("⚪ Incident gone", inc), remove_buttons=True)
    except Unreachable as e:
        return Outcome(_say("🔴 API server unreachable", inc, error=e))
    except NexusClientError as e:
        return Outcome(_say("🔴 Error", inc, error=e, nothing=False))

    try:  # step 8: report what the cluster stored, read back as the bot itself
        stored = reader.get_incident(inc.metadata.name)
    except NexusClientError:
        stored = reply.incident
    a = stored.spec.approval
    if a is None:
        return Outcome(_say("🔴 The write returned no approval", inc, audit=reply.audit_id or "-",
                            nothing=False), remove_buttons=True, wrote=True)
    title = {"approved": "⏳ Approved", "rejected": "⚪ Rejected"}.get(a.decision, "⚪ Decided")
    rt = RichText().title(title)
    rt.fact("Incident", views.short_name(inc.metadata.name), 40)
    rt.fact("Decision", a.decision, 10)
    rt.fact("By", a.approvedBy or "-", 64)
    rt.fact("At", views.hhmm(a.approvedAt), 20)
    if a.expiresAt:
        rt.fact("Expires", views.hhmm(a.expiresAt), 20)
    rt.fact("Pinned", views.short_hash(a.proposalHash or "-"), 40)
    rt.fact("Audit-Id", reply.audit_id or "-", 64)
    return Outcome(rt, remove_buttons=True, wrote=True)
