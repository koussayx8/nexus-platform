"""approve and reject (spec §16A, MUST; D22).

What you see is what you approve: the values shown are the values pinned. Nothing is written
without the Approver typing the target name; the Incident is re-read just before the write, and
a changed proposal hash aborts with exit 4. The write carries the Incident's resourceVersion, so
a change between that re-read and the write is a 409, also exit 4. Admission (RBAC, K5, C2)
decides; nexusctl only prints what it answered.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from nexus_client import approval as ap
from nexus_client.errors import (
    Conflict,
    Denied,
    NexusClientError,
    NotFound,
    StaleProposal,
    Unreachable,
    WrongPhase,
)
from nexus_client.incidents import IncidentApi
from nexus_client.models import Incident

from .. import exitcodes
from ..render import views
from ..render.sanitize import sanitize

VERB = {"approved": "approve", "rejected": "reject"}


def run_decide(api: IncidentApi, name: str, *, decision: str, ttl: timedelta, reason: str | None,
               prompt: Callable[[str], str], now: Callable[[], datetime],
               out: Callable[[str], None], max_chars: int = 400) -> int:
    try:
        shown = api.get(name).incident
        ap.check_decidable(shown)
        pending = ap.build_approval(shown, ttl=ttl, now=now(), decision=decision, reason=reason)
        out(views.approval_prompt_text(shown, decision=decision,
                                       expires_at=pending.get("expiresAt"), max_chars=max_chars))
        target = shown.spec.target.name
        typed = prompt(f"Type the target name ({sanitize(target)}) to {VERB[decision]}: ")
        if typed.strip() != target:
            out("aborted: the typed name does not match the target; nothing was written")
            return exitcodes.ERROR
        latest = api.get(name).incident
        ap.check_same_proposal(shown, latest)
        ap.check_decidable(latest)
        body = ap.build_approval(latest, ttl=ttl, now=now(), decision=decision, reason=reason)
        reply = api.patch_approval(name, body, latest.metadata.resourceVersion or "")
    except NotFound:
        out("error: incident not found")
        return exitcodes.NOT_FOUND
    except StaleProposal as e:
        out(f"error: {e}; nothing was written")
        return exitcodes.STALE
    except Conflict as e:
        out(f"error: the Incident changed while you were deciding ({sanitize(e)}); "
            "nothing was written")
        return exitcodes.STALE
    except WrongPhase as e:
        out(f"error: {e}")
        return exitcodes.WRONG_PHASE
    except Denied as e:
        audit = f" (Audit-Id {sanitize(e.audit_id)})" if e.audit_id else ""
        out(f"denied{audit}: {sanitize(e, max_chars)}")
        return exitcodes.DENIED
    except Unreachable as e:
        out(f"error: cannot reach the API server: {sanitize(e)}")
        return exitcodes.UNREACHABLE
    except NexusClientError as e:
        out(f"error: {sanitize(e)}")
        return exitcodes.ERROR
    return _report(reply.incident, reply.audit_id, out)


def _report(inc: Incident, audit_id: str | None, out: Callable[[str], None]) -> int:
    a = inc.spec.approval
    if a is None:
        out(f"error: the write returned no approval (Audit-Id {sanitize(audit_id or '-')})")
        return exitcodes.ERROR
    out(f"{a.decision} {sanitize(inc.metadata.name)}: stamped by {sanitize(a.approvedBy or '-')} "
        f"at {sanitize(a.approvedAt or '-')}; pinned {sanitize(a.proposalHash or '-')}; "
        f"Audit-Id {sanitize(audit_id or '-')}")
    return exitcodes.OK
