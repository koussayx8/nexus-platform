"""Message layouts for a phone (ADR-040 decision 11, as amended on 2026-11-22).

One fact per line, a short bold label before each value, a status icon in the title. Hashes and
Incident names are shortened in pushes and lists (sha256:14f167…839f5e, inc-6695…2289); Explain
shows them in full. Times are HH:MM UTC.

What K5 binds (target, action, parameters, hash) and the level are facts, above any model text and
above the buttons: sanitized, plain, never formatted. The model's own words (fault class,
hypothesis, escalation suggestion, reasoning excerpt) are only inside a blockquote titled
"Model text: untrusted, never a decision", sanitized, with instruction-like text marked ⚠️.

Links (2026-11-22) are the bot's own: `Links` builds every URL from the configured repository and
ref. Only a value that fully matches a strict pattern becomes link text (`prompt/v3`, a runbook chunk
id `rb-<name>#<n>`); a 40-hex commit SHA found in the model's text gets a separate trusted line
outside the quote, its first 7 hex linked, and the model's text itself is never made clickable.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus_client.models import Incident
from nexusctl.render.sanitize import sanitize

from .config import DEFAULT_REPO_REF, DEFAULT_REPO_URL
from .payload import COMMIT, EXPLAIN, INCIDENT, REASONING, RUNBOOKS, encode, incident_data, pods_data
from .richtext import MAX_TEXT, RichText, defang, units

N = 300  # characters per untrusted value
N_ESCALATION = 2000  # ADR-044: suggestedChange and runbookGap
MODEL_TEXT = "Model text: untrusted, never a decision"
ICON = {"AwaitingApproval": "🟠", "Resolved": "🟢", "Escalated": "🔴", "Dismissed": "⚪", "Blocked": "⛔"}
IN_PROGRESS = frozenset({"Detected", "Diagnosing", "Proposed", "Executing", "Verifying", "Compensating"})
TITLE = {"AwaitingApproval": "Approval needed"}
PROMPT = re.compile(r"^prompt/v[0-9]+$")
CHUNK = re.compile(r"^(rb-[a-z0-9-]+)#[0-9]+$")
SHA = re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{40}(?![0-9A-Fa-f])")
MAX_SHAS = 3
COMMIT_LINE = "Commit named by the model (not checked): "
PROMPT_PATH = "reasoner/nexus_reasoner/prompt.py"
Rows = list[list[tuple[str, str]]]  # rows of (label, callback_data)


@dataclass(frozen=True)
class Links:
    """Every URL the bot shows, built from its configuration only (repoUrl, repoRef)."""

    repo: str = DEFAULT_REPO_URL
    ref: str = DEFAULT_REPO_REF

    def prompt(self) -> str:
        return f"{self.repo}/blob/{self.ref}/{PROMPT_PATH}"

    def runbook(self, name: str) -> str:
        return f"{self.repo}/blob/{self.ref}/runbooks/{name}.md"

    def commit(self, sha: str) -> str:
        return f"{self.repo}/commit/{sha}"


DEFAULT_LINKS = Links()
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

def push(inc: Incident, links: Links = DEFAULT_LINKS) -> RichText:
    """The AwaitingApproval push is fixed (tests/test_golden_approval.py): no link, no commit line."""
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
        if st.phase != "AwaitingApproval":
            commit_lines(rt, inc, links)
    return rt


# -- Explain ---------------------------------------------------------------------------------------

def _section(rt: RichText, name: str) -> RichText:
    return rt.nl().bold(name).nl()


def explain(inc: Incident, links: Links = DEFAULT_LINKS) -> RichText:
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
        rt.bold("Chunks:").text(" ")
        chunk_list(rt, chunks, links, ", ")
        rt.nl()
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
        rt.bold("Prompt:").text(" ")
        prompt_value(rt, _get(r, "promptVersion"), links)
        rt.nl()
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
        commit_lines(rt, inc, links)
    if isinstance(trace, str) and trace:
        # the model's thinking trace (ADR-035 addendum C): shown, never a decision
        rt.nl()
        model_text(rt, "Reasoning excerpt", [("Excerpt", trace, N)], expandable=True)
    return rt


# -- links (2026-11-22) ----------------------------------------------------------------------------

def prompt_value(rt: RichText, value: object, links: Links) -> RichText:
    text = sanitize(value, 40)
    return rt.link(text, links.prompt()) if PROMPT.fullmatch(text) else rt.value(text, 40)


def chunk_list(rt: RichText, chunks: object, links: Links, sep: str, n: int = 300) -> RichText:
    """Runbook chunk ids; each one matching rb-<name>#<n> links to runbooks/<name>.md at the ref."""
    items = [sanitize(c, 80) for c in chunks] if isinstance(chunks, list) else []
    if not items:
        return rt.text("-")
    used = 0
    for i, c in enumerate(items):
        if used + len(c) > n:
            return rt.text(f"{sep}…" if i else "…")
        if i:
            rt.text(sep)
        m = CHUNK.fullmatch(c)
        if m:
            rt.link(c, links.runbook(m[1]))
        else:
            rt.value(c, 80)
        used += len(c) + len(sep)
    return rt


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def commit_shas(inc: Incident) -> list[tuple[str, str]]:
    """(where, sha): each distinct 40-hex token in the proposal's hypothesis and parameters and the
    escalation's suggestion, gap and recommendation, at most three. The model named it; nothing
    checked that it exists or is related."""
    p = inc.status.proposal
    if p is None:
        return []
    esc = p.escalation if isinstance(p.escalation, dict) else {}
    fields = [("Hypothesis", p.hypothesis), ("Parameters", p.parameters),
              ("Suggested change", esc.get("suggestedChange")), ("Runbook gap", esc.get("runbookGap")),
              ("Recommendation", esc.get("recommendation"))]
    out: list[tuple[str, str]] = []
    for where, value in fields:
        for text in _strings(value):
            for sha in SHA.findall(text):
                if sha not in {s for _w, s in out}:
                    out.append((where, sha))
    return out[:MAX_SHAS]


def commit_lines(rt: RichText, inc: Incident, links: Links) -> RichText:
    """The trusted line, outside the quote: only the 40-hex token becomes a link."""
    for _where, sha in commit_shas(inc):
        rt.text(COMMIT_LINE).link(sha[:7], links.commit(sha)).nl()
    return rt


# -- buttons on result pushes and views (never on AwaitingApproval) -------------------------------

def _excerpt(inc: Incident) -> str | None:
    trace = inc.status.reasoner.get("reasoningExcerpt")
    return trace if isinstance(trace, str) and trace else None


def _chunks(inc: Incident) -> list[Any]:
    chunks = inc.status.reasoner.get("chunks")
    return chunks if isinstance(chunks, list) else []


def incident_rows(inc: Incident, namespaces: tuple[str, ...], *, explain: bool) -> Rows:
    """Explain, 🧠 Reasoning / 📦 Pods, 📚 Runbooks, 🔗 Commit, where each applies."""
    if inc.status.phase == "AwaitingApproval" and explain:
        raise ValueError("the AwaitingApproval push keeps its own three buttons")
    first: list[tuple[str, str | None]] = []
    if explain:
        first.append(("Explain", encode(EXPLAIN, inc) or incident_data(inc, INCIDENT)))
    if _excerpt(inc):
        first.append(("🧠 Reasoning", incident_data(inc, REASONING)))
    second: list[tuple[str, str | None]] = []
    ns = inc.spec.target.namespace
    if ns in namespaces:
        second.append(("📦 Pods", pods_data(ns)))
    if _chunks(inc):
        second.append(("📚 Runbooks", incident_data(inc, RUNBOOKS)))
    if commit_shas(inc):
        second.append(("🔗 Commit", incident_data(inc, COMMIT)))
    rows = [[(label, data) for label, data in row if data] for row in (first, second)]
    return [row for row in rows if row]


def back_row(inc: Incident) -> Rows:
    data = incident_data(inc, INCIDENT)
    return [[("Explain", data)]] if data else []


# -- the 🧠 Reasoning, 📚 Runbooks and 🔗 Commit views -----------------------------------------------

REASONING_BUDGET = MAX_TEXT - 600  # UTF-16 units of excerpt per message; the rest is labels
MAX_EXCERPT = 200_000


def split_units(text: str, budget: int) -> list[str]:
    """Pieces of at most `budget` UTF-16 units, cut after a space where one is near the end. A cut
    between code points never splits a surrogate pair."""
    out, rest = [], text
    while units(rest) > budget:
        cut, used = 0, 0
        for ch in rest:
            w = 2 if ord(ch) > 0xFFFF else 1
            if used + w > budget:
                break
            used += w
            cut += 1
        space = rest.rfind(" ", 0, cut)
        if space > cut // 2:
            cut = space + 1
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest or not out:
        out.append(rest)
    return out


def reasoning(inc: Incident, budget: int = REASONING_BUDGET) -> list[RichText]:
    """The reasoning excerpt, untrusted, in expandable blockquotes; one message per piece, each
    under 4,096 units. Today the operator stores 300 characters; a longer excerpt is split."""
    r = inc.status.reasoner
    trace = _excerpt(inc)
    if trace is None:
        rt = RichText().title("🧠 Reasoning")
        rt.fact("Incident", short_name(inc.metadata.name), 40)
        return [rt.text("No reasoning excerpt recorded.")]
    # sanitize and defang the whole excerpt once, so a cut cannot leave half a URL undefanged
    pieces = split_units(defang(sanitize(trace, MAX_EXCERPT)), budget)
    total = r.get("reasoningLength", len(trace))
    out = []
    for i, piece in enumerate(pieces, start=1):
        part = f" ({i} of {len(pieces)})" if len(pieces) > 1 else ""
        rt = RichText().title(f"🧠 Reasoning{part}")
        rt.fact("Incident", short_name(inc.metadata.name), 40)
        rt.fact("Model", _get(r, "modelId"), 80)
        before = rt.flagged
        with rt.quote(expandable=True):
            rt.bold(MODEL_TEXT).nl().untrusted(piece, len(piece) + 1)
            _flag_note(rt, before)
        rt.nl()
        if i == len(pieces):
            rt.text(f"{sanitize(total, 12)} characters in total; the full text is in the Flight Recorder")
        out.append(rt)
    return out


def runbooks(inc: Incident, links: Links = DEFAULT_LINKS) -> RichText:
    r = inc.status.reasoner
    rt = RichText().title("📚 Runbooks")
    rt.fact("Incident", short_name(inc.metadata.name), 40)
    if not r:
        return rt.text("Not recorded")
    rt.bold("Index:").text(" ").ident(_get(r, "indexHash"), 80).nl()
    rt.bold("Prompt:").text(" ")
    prompt_value(rt, _get(r, "promptVersion"), links).nl()
    rt.bold("Chunks the Reasoner retrieved:").nl()
    chunks = _chunks(inc)
    if not chunks:
        return rt.text("-")
    for c in chunks[:20]:
        rt.text("• ")
        chunk_list(rt, [c], links, "", 80).nl()
    rt.italic(f"Links open the runbook at {sanitize(links.ref, 100)}.")
    return rt


def commit(inc: Incident, links: Links = DEFAULT_LINKS) -> RichText:
    rt = RichText().title("🔗 Commit")
    rt.fact("Incident", short_name(inc.metadata.name), 40)
    shas = commit_shas(inc)
    if not shas:
        return rt.text("The model named no commit.")
    for where, sha in shas:
        rt.text(COMMIT_LINE).link(sha[:7], links.commit(sha)).nl()
        rt.fact("Named in", where, 40)
    return rt.italic("NEXUS did not check that this commit exists or relates to the Incident.")


# -- /pods as a table (2026-11-22) -----------------------------------------------------------------

TABLE_WIDTH = 36  # characters per line, for a phone


def _clip(text: str, width: int, keep_tail: bool) -> str:
    if len(text) <= width:
        return text
    return "…" + text[-(width - 1):] if keep_tail else text[: width - 1] + "…"


def table(header: list[str], rows: list[list[str]], *, shrink: dict[int, bool],
          width: int = TABLE_WIDTH) -> list[str]:
    """kubectl-style columns, one space apart. Cells are sanitized here. While a line is wider than
    `width`, the widest column in `shrink` (index -> keep its tail) loses a character, down to its
    header's width."""
    cells = [[sanitize(c, 80) for c in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(header)]
    while sum(widths) + len(widths) - 1 > width:
        room = [i for i in shrink if widths[i] > len(header[i])]
        if not room:
            break
        widths[max(room, key=lambda i: widths[i])] -= 1
    out = []
    for row in [header] + cells:
        line = " ".join(_clip(c, widths[i], shrink.get(i, False)).ljust(widths[i]) for i, c in enumerate(row))
        out.append(line.rstrip())
    return out


def fit_line(value: object, width: int = TABLE_WIDTH) -> str:
    return _clip(sanitize(value, 253), width, False)


# -- the Kill Switch and the level (2026-11-22) ----------------------------------------------------

def killswitch_change(before: str, after: str, now: datetime) -> RichText:
    """K4 allows a mutation only while the state is "active"; anything else denies."""
    halted = after != "active"
    rt = RichText().title("🛑 Kill Switch halted" if halted else "🟢 Kill Switch active")
    rt.fact("Was", before, 30)
    rt.fact("Now", after, 30)
    rt.fact("Seen", now.astimezone(UTC).strftime("%H:%M UTC"), 20)
    if halted:
        return rt.italic("Kyverno K4 denies every NEXUS mutation, compensation included, until the state "
                         "is active.")
    return rt.italic("Kyverno K4 no longer denies; every other rule still applies.")


def level_change(namespace: str, before: str, after: str, now: datetime) -> RichText:
    rt = RichText().title("🎚 Autonomy level changed")
    rt.fact("Namespace", namespace, 63)
    rt.fact("Level", f"{sanitize(before, 12)} → {sanitize(after, 12)}", 30)
    return rt.fact("Seen", now.astimezone(UTC).strftime("%H:%M UTC"), 20)


def level_text(raw: str | None) -> str:
    return "unset" if raw is None else (f"L{raw}" if raw.isdigit() else raw)
