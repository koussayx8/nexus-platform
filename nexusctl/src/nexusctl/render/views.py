"""Plain-text views: the explain view, the approval prompt and the incident table.

Trusted fields (identifiers and enum values the API server validates, admission-stamped
approval fields, the operator's gate/execution/verification verdicts) come first. Everything
derived from model output or evidence follows in labelled boxes. Every value is passed through
`sanitize` regardless, so a crafted string can never carry an escape sequence into the terminal.
"""

from datetime import UTC, datetime
from typing import Any

from nexus_client.models import Incident

from .injection import looks_like_instruction
from .sanitize import sanitize

LABEL = 13  # column width of the section labels


def _row(label: str, text: str) -> str:
    return f"{label:<{LABEL}}{text}"


def _s(value: object, n: int) -> str:
    return sanitize(value, n)


def _get(d: dict[str, Any], key: str, n: int, default: str = "-") -> str:
    v = d.get(key)
    return default if v in (None, "") else _s(v, n)


def untrusted_box(title: str, items: list[tuple[str, object]], n: int) -> list[str]:
    """A labelled box; each value sanitized and capped, instruction-like text flagged."""
    lines = [f"+-- UNTRUSTED: {title} (model or evidence text; never a decision) ----"]
    flagged = 0
    for key, value in items:
        text = _s(value, n)
        if looks_like_instruction(text):
            flagged += 1
            text = f"[!] {text}"
        lines.append(f"| {key}: {text}")
    if flagged:
        lines.append(f"| [!] instruction-like text in {flagged} field(s) (a display heuristic only)")
    lines.append("+----------")
    return lines


def _target(inc: Incident, n: int) -> str:
    t = inc.spec.target
    return f"{_s(t.namespace, n)}/{_s(t.name, n)}"


def _level(inc: Incident) -> str:
    lvl = inc.status.autonomyLevel
    return "L?" if lvl is None else f"L{int(lvl)}"


def explain_text(inc: Incident, max_chars: int = 400) -> str:
    n, st, sp = max_chars, inc.status, inc.spec
    phase = f"{_s(st.phase or 'unset', n)} ({_s(st.reason or '-', n)})"
    rows = [
        _row("INCIDENT", f"{_s(inc.metadata.name, n)}   {_target(inc, n)}   "
                         f"level {_level(inc)}   phase {phase}"),
        _row("DETECTION", f"{_s(sp.source.alertname or sp.source.type, n)}   source {sp.source.type}   "
                          f"requester {_s(sp.source.requester or '-', n)}   detected {_s(sp.detectedAt, n)}"),
    ]
    ev = st.evidence
    rows.append(_row("EVIDENCE", f"bundle {_get(ev, 'bundleHash', n)}   record {_get(ev, 'recordId', n)}   "
                                 f"redactions {_get(ev, 'redactions', n, '0')}")
                if ev else _row("EVIDENCE", "not recorded"))
    r = st.reasoner
    chunks = r.get("chunks")
    chunk_text = ", ".join(map(str, chunks)) if isinstance(chunks, list) and chunks else "-"
    rows.append(_row("RETRIEVAL", f"index {_get(r, 'indexHash', n)}   chunks {_s(chunk_text, n)}")
                if r else _row("RETRIEVAL", "not recorded"))
    g = st.gate
    rows.append(_row("SAFETY GATE", f"verdict {_get(g, 'verdict', n)}   first failing stage "
                                    f"{_get(g, 'firstFailingStage', n)}   dry run {_get(g, 'dryRun', n)}")
                if g else _row("SAFETY GATE", "not recorded"))
    a = sp.approval
    if a is None:
        rows.append(_row("APPROVAL", "none"))
    else:
        rows.append(_row("APPROVAL", f"{a.decision} by {_s(a.approvedBy or '-', n)} at "
                                     f"{_s(a.approvedAt or '-', n)}   expires {_s(a.expiresAt or '-', n)}   "
                                     f"pinned {_s(a.proposalHash or '-', n)}"))
    x = st.execution
    audit_ids = ", ".join(map(str, x.get("auditIds") or [])) or "-"
    rows.append(_row("ENFORCEMENT", f"result {_get(x, 'result', n)}   audit {_s(audit_ids, n)}")
                if x else _row("ENFORCEMENT", "no mutation attempted"))
    v = st.verification
    rows.append(_row("VERIFICATION", f"{_get(v, 'criteria', n)}: {_get(v, 'result', n)}")
                if v else _row("VERIFICATION", "not recorded"))
    rows.append(_row("RESULT", phase))
    rows.append(_row("FLIGHT REC.", "not available: the Flight Recorder reader is not part of this build"))
    rows.append(_row("AUDIT LOG", "not available: the API Audit Log reader is not part of this build"))
    p = st.proposal
    if p is not None:
        items: list[tuple[str, object]] = [
            ("action", p.actionId), ("parameters", p.parameters), ("hash", p.hash),
            ("faultClass", p.faultClass or "-"), ("abstain", p.abstain),
            ("hypothesis", p.hypothesis or "-"),
        ]
        if r:
            items += [("model", r.get("modelId", "-")), ("prompt", r.get("promptVersion", "-"))]
        rows += [""] + untrusted_box("PROPOSAL", items, n)
    return "\n".join(rows)


def approval_prompt_text(inc: Incident, *, decision: str, expires_at: str | None,
                         max_chars: int = 400) -> str:
    """What the Approver sees before confirming: the exact values that will be pinned."""
    n, p, g = max_chars, inc.status.proposal, inc.status.gate
    assert p is not None  # check_decidable ran first
    rows = [
        _row("INCIDENT", f"{_s(inc.metadata.name, n)}   {_target(inc, n)}   level {_level(inc)}   "
                         f"phase {_s(inc.status.phase or 'unset', n)}"),
        _row("DECISION", decision.upper()),
        _row("ACTION", f"{_s(p.actionId, n)}   parameters {_s(p.parameters, n)}"),
        _row("TARGET", _target(inc, n)),
        _row("PROPOSAL", f"hash {_s(p.hash, n)}"),
        _row("SAFETY GATE", f"verdict {_get(g, 'verdict', n)}   first failing stage "
                            f"{_get(g, 'firstFailingStage', n)}   dry run {_get(g, 'dryRun', n)}"),
    ]
    if expires_at:
        rows.append(_row("EXPIRES", expires_at))
    rows += [""] + untrusted_box(
        "HYPOTHESIS", [("faultClass", p.faultClass or "-"), ("hypothesis", p.hypothesis or "-")], n)
    return "\n".join(rows)


def _age(created: str | None, now: datetime) -> str:
    if not created:
        return "-"
    try:
        t = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return "-"
    s = max(0, int((now.astimezone(UTC) - t).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def table_text(incidents: list[Incident], now: datetime) -> str:
    head = ["NAME", "PHASE", "TARGET", "ACTION", "LEVEL", "REASON", "AGE"]
    rows = [head]
    for i in incidents:
        p = i.status.proposal
        rows.append([
            _s(i.metadata.name, 60), _s(i.status.phase or "-", 30), _target(i, 60),
            _s(p.actionId if p else "-", 30), _level(i), _s(i.status.reason or "-", 40),
            _age(i.metadata.creationTimestamp, now),
        ])
    widths = [max(len(r[c]) for r in rows) for c in range(len(head))]
    return "\n".join("   ".join(cell.ljust(widths[c]) for c, cell in enumerate(r)).rstrip() for r in rows)
