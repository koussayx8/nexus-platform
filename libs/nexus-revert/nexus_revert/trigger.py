"""Which Incidents ask for a revert pull request, and the commit they name (ADR-041 "Commit
validation", input and "Where the candidate comes from").

Read from the raw Incident: the client model drops `status.proposal.recommendation`.
"""

import re
from dataclasses import dataclass
from typing import Any

from .config import Config, Target

_SHA = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_NAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True)
class Request:
    incident: dict[str, Any]
    name: str
    namespace: str
    deployment: str
    target: Target
    named_sha: str | None  # the Reasoner's candidate, untrusted; None means rule A1


def named_commit(detail: object) -> str | None:
    """The candidate when `recommendation.detail` holds exactly one 40-hex token, else None (A1)."""
    if not isinstance(detail, str):
        return None
    found = {m.lower() for m in _SHA.findall(detail)}
    return found.pop() if len(found) == 1 else None


def request_for(inc: dict[str, Any], cfg: Config) -> Request | None:
    st = inc.get("status") or {}
    proposal = st.get("proposal") or {}
    rec = proposal.get("recommendation") or {}
    target = (inc.get("spec") or {}).get("target") or {}
    name = (inc.get("metadata") or {}).get("name")
    if st.get("phase") != "Escalated" or proposal.get("actionId") != "escalate":
        return None
    if rec.get("kind") != "git_revert" or target.get("kind") != "Deployment":
        return None
    ns = target.get("namespace")
    if ns not in cfg.branches or not isinstance(name, str) or not _NAME.fullmatch(name):
        return None
    return Request(incident=inc, name=name, namespace=ns, deployment=str(target.get("name", "")),
                   target=cfg.branches[ns], named_sha=named_commit(rec.get("detail")))
