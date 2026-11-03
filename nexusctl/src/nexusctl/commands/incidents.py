"""list and explain (spec §16A, MUST). Read-only."""

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta

import yaml
from nexus_client.errors import NexusClientError, NotFound, Unreachable
from nexus_client.incidents import IncidentApi
from nexus_client.models import Incident

from .. import exitcodes
from ..render import views

TERMINAL = {"Recorded", "Recommended", "Dismissed", "Resolved", "Escalated", "Blocked",
            "Rejected", "Expired"}
_SINCE = re.compile(r"^(\d+)([smhd])$")
_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_since(text: str) -> timedelta:
    m = _SINCE.match(text.strip())
    if not m:
        raise ValueError(f"invalid --since {text!r}: use for example 30m, 2h or 1d")
    return timedelta(**{_UNIT[m[2]]: int(m[1])})


def _dump(obj: object, output: str) -> str:
    # json.dumps and yaml.safe_dump both escape control characters, ESC included
    if output == "json":
        return json.dumps(obj, indent=2, default=str)
    return yaml.safe_dump(obj, sort_keys=False)


def _created(i: Incident) -> datetime | None:
    ts = i.metadata.creationTimestamp
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def run_list(api: IncidentApi, *, active: bool, phase: str | None, ns: str | None,
             since: str | None, limit: int, output: str, now: datetime,
             out: Callable[[str], None]) -> int:
    try:
        cutoff = now - parse_since(since) if since else None
        items = api.list()
    except Unreachable as e:
        out(f"error: cannot reach the API server: {e}")
        return exitcodes.UNREACHABLE
    except (NexusClientError, ValueError) as e:
        out(f"error: {e}")
        return exitcodes.ERROR

    def keep(i: Incident) -> bool:
        if active and (i.status.phase or "") in TERMINAL:
            return False
        if phase and i.status.phase != phase:
            return False
        if ns and i.spec.target.namespace != ns:
            return False
        created = _created(i)
        return not (cutoff and created and created < cutoff)

    shown = sorted((i for i in items if keep(i)),
                   key=lambda i: i.metadata.creationTimestamp or "", reverse=True)[:limit]
    if output in ("json", "yaml"):
        out(_dump([i.model_dump(mode="json", exclude_none=True) for i in shown], output))
    else:
        out(views.table_text(shown, now))
    return exitcodes.OK


def run_explain(api: IncidentApi, name: str, *, evidence: bool, raw: bool, output: str,
                out: Callable[[str], None], max_chars: int = 400) -> int:
    if evidence or raw:
        # The redacted bundle and the raw model output live in the Flight Recorder only.
        out("error: --evidence and --raw need the Flight Recorder, "
            "whose reader is not part of this build")
        return exitcodes.UNREACHABLE
    try:
        inc = api.get(name).incident
    except NotFound:
        out("error: incident not found")
        return exitcodes.NOT_FOUND
    except Unreachable as e:
        out(f"error: cannot reach the API server: {e}")
        return exitcodes.UNREACHABLE
    except NexusClientError as e:
        out(f"error: {e}")
        return exitcodes.ERROR
    if output in ("json", "yaml"):
        out(_dump(inc.model_dump(mode="json", exclude_none=True), output))
    else:
        out(views.explain_text(inc, max_chars))
    return exitcodes.OK
