"""Message texts. Plain text only; nexusctl's `sanitize`, `untrusted_box` and `explain_text`
render every untrusted value (ADR-040 decision 11), so there is no second renderer.

Above the buttons, the fields K5 binds (target, action, parameters, hash) and the level come
first, outside the untrusted box; the Reasoner's text is inside it.

Explain is nexusctl's explain view, so it shows what `nexusctl explain` shows: the model's reasoning
excerpt (ADR-035 addendum C) and the escalation's suggestedChange and runbookGap (ADR-044), each in
an UNTRUSTED box, sanitized, instruction-like text flagged, never a decision or a button. The
message is sent with no parse_mode and is capped at 4,096 characters with a "(truncated: N of M
characters)" line (telegram.fit); the full text stays in the Flight Recorder.
"""

from nexus_client.models import Incident
from nexusctl.render.sanitize import sanitize
from nexusctl.render.views import explain_text, untrusted_box

N = 300  # characters per untrusted value


def _target(inc: Incident) -> str:
    t = inc.spec.target
    return f"{sanitize(t.namespace, 63)}/{sanitize(t.name, 253)}"


def _level(inc: Incident) -> str:
    lvl = inc.status.autonomyLevel
    return "L?" if lvl is None else f"L{int(lvl)}"


def push_text(inc: Incident) -> str:
    st, p = inc.status, inc.status.proposal
    phase = sanitize(st.phase or "unset", 30)
    lines = [
        f"NEXUS {phase} ({sanitize(st.reason or '-', 40)})",
        f"Incident  {sanitize(inc.metadata.name, 80)}",
        f"Target    {_target(inc)}   level {_level(inc)}",
    ]
    if p is not None:
        lines += [f"Action    {sanitize(p.actionId, 64)}   parameters {sanitize(p.parameters, 120)}",
                  f"Hash      {sanitize(p.hash, 80)}"]
    if st.phase == "AwaitingApproval":
        lines.append("Approve pins this action, these parameters and this hash for at most 15 minutes "
                     "(Kyverno K5).")
    else:
        x, v = st.execution, st.verification
        if x:
            lines.append(f"Execution {sanitize(x.get('result', '-'), 60)}")
        if v:
            lines.append(f"Verified  {sanitize(v.get('criteria', '-'), 80)}: "
                         f"{sanitize(v.get('result', '-'), 40)}")
    if p is not None:
        lines += [""] + untrusted_box(
            "HYPOTHESIS", [("faultClass", p.faultClass or "-"), ("hypothesis", p.hypothesis or "-")], N)
    return "\n".join(lines)


def explain(inc: Incident) -> str:
    return explain_text(inc, max_chars=N)
