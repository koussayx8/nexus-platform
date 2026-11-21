"""The pull request title and body (ADR-041 "Pull request title and body").

Every field is an enum, a number, a hash, a CRD-validated name or a timestamp, except the original
commit subject, which is repository content: sanitised, stripped of @, # and backticks, cut at 72
characters, and put in a code span. The Reasoner's free text (hypothesis, detail) is never used.
"""

import re
from typing import Any

from nexusctl.render.sanitize import sanitize

from .config import Config
from .revert import Prepared
from .trigger import Request

REASONER_LINE = "the Reasoner named this commit; NEXUS checked it"
A1_LINE = "rule A1 (ADR-041): the newest commit touching {overlay}. The Reasoner did NOT name it"
_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_WORD = re.compile(r"[A-Za-z0-9_./:+-]{1,80}")


def _field(value: object, rx: re.Pattern[str] = _WORD) -> str:
    text = sanitize(value, 80)
    return text if rx.fullmatch(text) else "-"


def _number(value: object) -> str:
    return f"{value:.2f}" if isinstance(value, int | float) and not isinstance(value, bool) else "-"


def subject_span(subject: str) -> str:
    text = re.sub(r"[@#`]", "", sanitize(subject, 200))[:72].strip()
    return f"`{text}`" if text else "-"


def title(p: Prepared, req: Request) -> str:
    return f"revert({req.target.overlay}): revert {p.sha[:12]} for {req.name}"


def body(p: Prepared, req: Request, cfg: Config) -> str:
    st: dict[str, Any] = req.incident.get("status") or {}
    prop, rsn, ev = st.get("proposal") or {}, st.get("reasoner") or {}, st.get("evidence") or {}
    spec = req.incident.get("spec") or {}
    chosen = REASONER_LINE if p.chosen_by_reasoner else A1_LINE.format(overlay=req.target.overlay)
    rows = [
        "NEXUS opened this pull request. NEXUS did not merge it and cannot.",
        "",
        "```",
        f"Incident:        {req.name}            (namespace nexus-system)",
        f"Target:          {_field(req.namespace)}/{_field(req.deployment)}",
        f"Detected at:     {_field(spec.get('detectedAt'))}",
        f"Fault class:     {_field(prop.get('faultClass'))}            "
        f"confidence {_number(prop.get('confidence'))}",
        f"Reasoner:        {_field(rsn.get('mode'))} / {_field(rsn.get('modelId'))}",
        f"Evidence bundle: {_field(ev.get('bundleHash'), _HASH)}",
        f"Proposal:        {_field(prop.get('hash'), _HASH)}",
        "",
        f"Reverts:         {p.sha}",
        "```",
        f"Subject:         {subject_span(p.subject)}",
        "```",
        f"Chosen by:       {chosen}",
        f"Tracked branch:  {p.base}          (Argo CD application {req.target.application})",
        "```",
        "",
        "The diff is a mechanical `git revert` of that one commit. Review the diff, not this text.",
    ]
    return "\n".join(rows)
