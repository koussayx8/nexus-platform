"""Message layouts for a phone (ADR-040 decision 11, as amended on 2026-11-22).

One fact per line, a short bold label before each value, a status icon in the title. Hashes and
Incident names are shortened in pushes and lists (sha256:14f167…839f5e, inc-6695…2289); Explain
shows them in full. Times are HH:MM UTC.

What K5 binds (target, action, parameters, hash) and the level are facts, above any model text and
above the buttons: sanitized, plain, never formatted. The model's own words (fault class,
hypothesis, escalation suggestion, reasoning excerpt) are only inside a blockquote titled
"Model text: untrusted, never a decision", sanitized, with instruction-like text marked ⚠️.
"""

from datetime import UTC, datetime
from typing import Any

from nexus_client.models import Incident
from nexusctl.render.sanitize import sanitize

from .richtext import RichText

N = 300  # characters per untrusted value
N_ESCALATION = 2000  # ADR-044: suggestedChange and runbookGap
MODEL_TEXT = "Model text: untrusted, never a decision"
ICON = {"AwaitingApproval": "🟠", "Resolved": "🟢", "Escalated": "🔴", "Dismissed": "⚪", "Blocked": "⛔"}
IN_PROGRESS = frozenset({"Detected", "Diagnosing", "Proposed", "Executing", "Verifying", "Compensating"})
TITLE = {"AwaitingApproval": "Approval needed"}
PHASES = ("Detected", "Diagnosing", "Proposed", "AwaitingApproval", "Executing", "Verifying", "Compensating",
          "Recorded", "Recommended", "Dismissed", "Resolved", "Escalated", "Blocked", "Rejected", "Expired")


# -- small formatters ------------------------------------------------------------------------------

def icon(phase: str | None) -> str:
    p = phase or ""
    return ICON.get(p) or ("⏳" if p in IN_PROGRESS else "⚪")


def heading(phase: str | None) -> str:
    """The title: icon and phase, always one of the bot's own strings (the CRD's phase enum), so
    the bold over it covers no text read from the cluster."""
    p = phase or ""
    if p not in PHASES:
        return f"{icon(None)} Unknown phase"
    return f"{icon(p)} {TITLE.get(p, p)}"


def short_hash(value: object) -> str:
    """sha256:14f167…839f5e"""
    text = sanitize(value, 80)
    algo, sep, hexpart = text.partition(":")
    if sep and len(hexpart) > 14:
        return f"{algo}:{hexpart[:6]}…{hexpart[-6:]}"
    return text


def short_name(value: object, head: int = 8, tail: int = 4) -> str:
    """inc-669532275a7b73d2-1795292289 -> inc-6695…2289"""
    text = sanitize(value, 253)
    return text if len(text) <= head + tail + 2 else f"{text[:head]}…{text[-tail:]}"


def hhmm(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        return "-"
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return sanitize(raw, 30)
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).strftime("%H:%M UTC")


def target(inc: Incident) -> str:
    t = inc.spec.target
    return f"{sanitize(t.namespace, 63)}/{sanitize(t.name, 253)}"


def level(inc: Incident) -> str:
    lvl = inc.status.autonomyLevel
    return "L?" if lvl is None else f"L{int(lvl)}"


def parameters(params: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items()) or "-"


def _get(d: dict[str, Any], key: str, default: str = "-") -> object:
    v = d.get(key)
    return default if v in (None, "") else v


def detail(rt: RichText, label: str, text: object, n: int = 300) -> RichText:
    """Error or log text from outside the bot: one line in a plain blockquote."""
    with rt.quote():
        rt.bold(f"{label}:").text(" ").untrusted(text, n)
    return rt.nl()


def _flag_note(rt: RichText, before: int) -> None:
    if rt.flagged > before:
        rt.nl().text(f"⚠️ instruction-like text in {rt.flagged - before} field(s) (a display heuristic only)")


def model_text(rt: RichText, part: str, items: list[tuple[str, object, int]], *,
               expandable: bool) -> RichText:
    """The model's words, in a blockquote under the fixed title. Nothing in it is formatted except
    the bot's own title and labels."""
    before = rt.flagged
    with rt.quote(expandable=expandable):
        rt.bold(MODEL_TEXT).nl().italic(part)
        for label, value, n in items:
            rt.nl().bold(f"{label}:").text(" ").untrusted(value, n)
        _flag_note(rt, before)
    return rt.nl()


# -- the AwaitingApproval and terminal pushes ------------------------------------------------------

def push(inc: Incident) -> RichText:
    st, p = inc.status, inc.status.proposal
    rt = RichText().title(heading(st.phase))
    rt.fact("Incident", short_name(inc.metadata.name), 40)
    rt.fact("Target", target(inc), 320)
    rt.fact("Level", level(inc))
    rt.fact("Reason", st.reason or "-", 40)
    if p is not None:
        rt.fact("Action", p.actionId, 64)
        rt.fact("Parameters", parameters(p.parameters), 120)
        rt.fact("Hash", short_hash(p.hash), 40)
    if st.phase == "AwaitingApproval":
        if st.gate:
            rt.fact("Gate", _get(st.gate, "verdict"), 40)
        rt.fact("Detected", hhmm(inc.spec.detectedAt), 20)
        rt.italic("Approve pins this action, these parameters and this hash for at most 15 minutes "
                  "(Kyverno K5).").nl()
    else:
        x, v = st.execution, st.verification
        if x:
            rt.fact("Execution", _get(x, "result"), 60)
        if v:
            rt.fact("Verification",
                    f"{sanitize(_get(v, 'criteria'), 80)}: {sanitize(_get(v, 'result'), 40)}", 130)
        rt.fact("Ended", hhmm(st.timestamps.get("terminal")), 20)
    if p is not None:
        rt.nl()
        model_text(rt, "Proposal", [("Fault class", p.faultClass or "-", N),
                                    ("Hypothesis", p.hypothesis or "-", N)], expandable=False)
    return rt


# -- Explain ---------------------------------------------------------------------------------------

def _section(rt: RichText, name: str) -> RichText:
    return rt.nl().bold(name).nl()


def explain(inc: Incident) -> RichText:
    st, sp, p = inc.status, inc.spec, inc.status.proposal
    rt = RichText().title(heading(st.phase))
    rt.bold("Incident:").text(" ").ident(inc.metadata.name, 80).nl()
    rt.fact("Target", target(inc), 320)
    rt.fact("Level", level(inc))
    rt.fact("Phase", f"{sanitize(st.phase or 'unset', 30)} ({sanitize(st.reason or '-', 40)})", 80)
    if p is not None:
        rt.fact("Action", p.actionId, 64)
        rt.fact("Parameters", parameters(p.parameters), 200)
        rt.bold("Hash:").text(" ").ident(p.hash, 80).nl()

    _section(rt, "Detection")
    rt.fact("Alert", sp.source.alertname or sp.source.type, 80)
    rt.fact("Source", sp.source.type, 20)
    rt.fact("Requester", sp.source.requester or "-", 80)
    rt.fact("Detected", hhmm(sp.detectedAt), 20)

    _section(rt, "Evidence")
    ev = st.evidence
    if ev:
        rt.bold("Bundle:").text(" ").ident(_get(ev, "bundleHash"), 80).nl()
        rt.fact("Record", _get(ev, "recordId"), 80)
        rt.fact("Redactions", _get(ev, "redactions", "0"), 20)
    else:
        rt.text("Not recorded").nl()

    _section(rt, "Runbooks")
    r = st.reasoner
    if r:
        chunks = r.get("chunks")
        rt.bold("Index:").text(" ").ident(_get(r, "indexHash"), 80).nl()
        rt.fact("Chunks", ", ".join(map(str, chunks)) if isinstance(chunks, list) and chunks else "-", 300)
    else:
        rt.text("Not recorded").nl()

    _section(rt, "Gate")
    g = st.gate
    if g:
        rt.fact("Verdict", _get(g, "verdict"), 40)
        rt.fact("First failing stage", _get(g, "firstFailingStage"), 40)
        rt.fact("Dry run", _get(g, "dryRun"), 40)
    else:
        rt.text("Not recorded").nl()

    _section(rt, "Approval")
    a = sp.approval
    if a is None:
        rt.text("None").nl()
    else:
        rt.fact("Decision", a.decision, 10)
        rt.fact("By", a.approvedBy or "-", 64)
        rt.fact("At", hhmm(a.approvedAt), 20)
        rt.fact("Expires", hhmm(a.expiresAt), 20)
        rt.bold("Pinned:").text(" ").ident(a.proposalHash or "-", 80).nl()

    _section(rt, "Enforcement")
    x = st.execution
    if x:
        rt.fact("Result", _get(x, "result"), 60)
        rt.fact("Audit", ", ".join(map(str, x.get("auditIds") or [])) or "-", 300)
    else:
        rt.text("No mutation attempted").nl()

    _section(rt, "Verification")
    v = st.verification
    if v:
        rt.fact("Criteria", _get(v, "criteria"), 80)
        rt.fact("Result", _get(v, "result"), 40)
    else:
        rt.text("Not recorded").nl()

    _section(rt, "Model")
    trace = r.get("reasoningExcerpt") if r else None
    if r:
        rt.fact("Model", _get(r, "modelId"), 80)
        rt.fact("Prompt", _get(r, "promptVersion"), 40)
        if isinstance(trace, str) and trace:
            rt.fact("Reasoning", f"{sanitize(r.get('reasoningLength', len(trace)), 12)} characters; "
                                 "the full text is in the Flight Recorder", 100)
    else:
        rt.text("Not recorded").nl()

    if p is not None:
        items: list[tuple[str, object, int]] = [
            ("Fault class", p.faultClass or "-", N), ("Abstain", "yes" if p.abstain else "no", 10),
            ("Hypothesis", p.hypothesis or "-", N)]
        esc = p.escalation if isinstance(p.escalation, dict) else {}
        # ADR-044: the model's suggested change and runbook gap, for a human; never applied
        items += [(label, esc[key], N_ESCALATION)
                  for key, label in (("suggestedChange", "Suggested change (never applied)"),
                                     ("runbookGap", "Runbook gap (never applied)"))
                  if isinstance(esc.get(key), str) and esc[key]]
        rt.nl()
        model_text(rt, "Proposal", items, expandable=True)
    if isinstance(trace, str) and trace:
        # the model's thinking trace (ADR-035 addendum C): shown, never a decision
        rt.nl()
        model_text(rt, "Reasoning excerpt", [("Excerpt", trace, N)], expandable=True)
    return rt
