"""Read-only commands (D21; ADR-040 decision 8). Fixed grammar, fixed namespaces, no writes:
these functions are given a Reader only.

`/pods` with no argument offers the two namespaces as buttons; `/incident` with no argument lists
the Incidents, five per page, with filters. Those buttons (`payload.View`) lead back here, to the same
read-only functions. Paging, filters, the Table/Cards toggle and Refresh edit their message in place
(`Reply.edit`); every other button sends a new message.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from nexus_client.errors import NexusClientError, NotFound
from nexus_client.models import Incident

from . import views
from .kube import Deploy, Pod, Reader
from .payload import (
    CARDS,
    COMMIT,
    INCIDENT,
    LIST,
    PICK,
    PODS,
    REASONING,
    RUNBOOKS,
    TABLE,
    View,
    incident_data,
    list_data,
    pods_data,
    uid_key,
)
from .richtext import RichText
from .views import DEFAULT_LINKS, Links

NAME = re.compile(r"^inc-[a-z0-9-]{1,59}$")
TERMINAL = frozenset({"Recorded", "Recommended", "Dismissed", "Resolved", "Escalated", "Blocked",
                      "Rejected", "Expired"})
RECENT = 5  # Incidents per page
MAX_PAGE = 99  # the payload's page is one or two digits
FILTERS = (("a", "All"), ("o", "Open"), ("d", "nexus-dev"), ("p", "nexus-prod"))
FILTER_NS = {"d": "nexus-dev", "p": "nexus-prod"}
# setMyCommands at start: the menu Telegram shows beside the text field
COMMANDS = (
    ("status", "Open Incidents, levels, Kill Switch"),
    ("pods", "Deployments and pods of nexus-dev or nexus-prod"),
    ("incident", "One Incident, or the list, 5 per page"),
)

Buttons = list[list[tuple[str, str]]]  # rows of (label, callback_data)


@dataclass(frozen=True)
class Reply:
    message: RichText
    rows: Buttons = field(default_factory=list)
    edit: bool = False  # replace the pressed message (paging, filters, toggles, Refresh)
    leading: list[RichText] = field(default_factory=list)  # sent before `message` (a split reasoning view)


def usage() -> RichText:
    rt = RichText().title("NEXUS bot")
    for cmd, desc in COMMANDS:
        rt.bold(f"/{cmd}").text(f": {desc}").nl()
    return rt


def parse(text: object) -> tuple[str, list[str]] | None:
    """'/cmd@BotName a b' -> ('cmd', ['a', 'b']); None for anything that is not a command."""
    if not isinstance(text, str) or not text.startswith("/"):
        return None
    words = text.strip().split()
    cmd = words[0][1:].split("@", 1)[0].lower()
    return (cmd, words[1:]) if re.fullmatch(r"[a-z]{1,20}", cmd) else None


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


def _utc(now: datetime, seconds: bool = False) -> str:
    return now.astimezone(UTC).strftime("%H:%M:%S UTC" if seconds else "%H:%M UTC")


def _incident_lines(rt: RichText, inc: Incident, tail: str) -> None:
    p = inc.status.proposal
    rt.text(f"{views.icon(inc.status.phase)} ").value(views.short_name(inc.metadata.name), 40) \
        .text(" · ").value(inc.status.phase or "-", 30).nl()
    rt.text("   ").value(views.target(inc), 320).text(" · ").value(p.actionId if p else "-", 64) \
        .text(" · ").value(tail, 20).nl()


def status(reader: Reader, namespaces: tuple[str, ...], now: datetime) -> RichText:
    rt = RichText().title("📊 NEXUS status")
    try:
        state = reader.killswitch()
        rt.fact("Kill Switch", state if state is not None else "missing (K4 denies)", 30)
    except NexusClientError as e:
        rt.bold("Kill Switch:").text(" unavailable").nl()
        views.detail(rt, "Error", e, 80)
    for ns in namespaces:
        try:
            lvl = reader.level(ns)
            shown = "unset" if lvl is None else (f"L{lvl}" if lvl.isdigit() else lvl)
            rt.fact(f"Level {ns}", shown, 8)
        except NexusClientError as e:
            rt.bold(f"Level {ns}:").text(" unavailable").nl()
            views.detail(rt, "Error", e, 80)
    rt.nl()
    try:
        open_ = [i for i in reader.list_incidents() if i.status.phase not in TERMINAL]
    except NexusClientError as e:
        rt.bold("Open Incidents:").text(" unavailable").nl()
        views.detail(rt, "Error", e, 120)
    else:
        rt.bold("Open Incidents:").text(f" {len(open_)}").nl()
        for inc in open_:
            _incident_lines(rt, inc, _age(inc.metadata.creationTimestamp, now))
    rt.nl().italic(f"As of {_utc(now)}")
    return rt


STATUS_ROWS: Buttons = [[("📦 Pods", "v:pods"), ("🗂 Incidents", "v:inc")]]


def pick(namespaces: tuple[str, ...]) -> Reply:
    row = [(ns, data) for ns in namespaces if (data := pods_data(ns)) is not None]
    return Reply(RichText().title("📦 Pods").text("Which namespace?"), [row])


def _owner(pod: str, deploys: list[Deploy]) -> Deploy | None:
    """The Deployment whose name, and a dash, starts the pod's name; the longest wins."""
    owners = [d for d in deploys if pod.startswith(d.name + "-")]
    return max(owners, key=lambda d: len(d.name)) if owners else None


def _pod_row(p: Pod, name: str, now: datetime) -> list[str]:
    ready = f"{p.ready_containers}/{p.containers}" if p.containers else ("1/1" if p.ready else "0/1")
    return [name, ready, p.status or p.phase, str(p.restarts), _age(p.created, now)]


POD_HEADER = ["NAME", "READY", "STATUS", "RESTARTS", "AGE"]
DEPLOY_HEADER = ["READY", "UP-TO-DATE", "AVAILABLE", "AGE"]


def pods_table(deploys: list[Deploy], pod_list: list[Pod], now: datetime) -> str:
    """One group per Deployment: its name, its READY UP-TO-DATE AVAILABLE AGE, then its pods with the
    Deployment's name dropped from theirs. Pods of no Deployment come last, under their full names.
    At most 36 characters a line (views.TABLE_WIDTH)."""
    groups: list[tuple[Deploy | None, list[Pod]]] = [(d, []) for d in deploys]
    index = {d.name: i for i, d in enumerate(deploys)}
    orphans: list[Pod] = []
    for p in pod_list:
        d = _owner(p.name, deploys)
        (groups[index[d.name]][1] if d else orphans).append(p)
    if orphans:
        groups.append((None, orphans))
    blocks = []
    for d, members in groups:
        lines = [views.fit_line(d.name if d else "pods of no Deployment")]
        if d:
            lines += views.table(DEPLOY_HEADER, [[f"{d.ready}/{d.desired}", str(d.updated), str(d.available),
                                                 _age(d.created, now)]], shrink={})
        if members:
            rows = [_pod_row(p, p.name[len(d.name) + 1:] if d else p.name, now) for p in members]
            lines += views.table(POD_HEADER, rows, shrink={0: True, 2: False})
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "No Deployments or pods"


def _pods_rows(ns: str, cards: bool) -> Buttons:
    toggle = ("📋 Table", pods_data(ns, TABLE)) if cards else ("🃏 Cards", pods_data(ns, CARDS))
    refresh = ("🔄 Refresh", pods_data(ns, CARDS if cards else TABLE))
    return [[(label, data) for label, data in (toggle, refresh) if data]]


def pods(reader: Reader, namespaces: tuple[str, ...], args: list[str], now: datetime | None = None, *,
         cards: bool = False, edit: bool = False) -> Reply:
    """/pods <ns>: a kubectl-style table by default (`cards=False`), or one card per line."""
    if not args:
        return pick(namespaces)
    if len(args) != 1 or args[0] not in namespaces:
        return Reply(RichText().text(f"usage: /pods <namespace>, one of {', '.join(namespaces)}"))
    ns, now = args[0], now or datetime.now(UTC)
    rt = RichText().title(f"📦 {ns}")
    rows = _pods_rows(ns, cards)
    try:
        deploys, pod_list = reader.deployments(ns), reader.pods(ns)
    except NexusClientError as e:
        rt.text("Unavailable").nl()
        return Reply(views.detail(rt, "Error", e, 120), rows, edit)
    if not cards:
        rt.text(f"Deployments ({len(deploys)}) · Pods ({len(pod_list)})").nl()
        rt.pre(pods_table(deploys, pod_list, now)).nl()
        rt.italic(f"As of {_utc(now, seconds=True)}")
        return Reply(rt, rows, edit)
    rt.bold(f"Deployments ({len(deploys)})").nl()
    for d in deploys:
        rt.value(views.short_name(d.name, 20, 8), 40).text(f": {d.ready}/{d.desired} ready").nl()
    rt.nl().bold(f"Pods ({len(pod_list)})").nl()
    for p in pod_list:
        rt.value(views.short_name(p.name, 20, 8), 40).text(": ").value(p.status or p.phase, 30) \
            .text(f", {'ready' if p.ready else 'not ready'}, {p.restarts} restarts").nl()
    rt.nl().italic(f"As of {_utc(now, seconds=True)}")
    return Reply(rt, rows, edit)


def _explain(inc: Incident, namespaces: tuple[str, ...] = (), links: Links = DEFAULT_LINKS) -> Reply:
    return Reply(views.explain(inc, links), views.incident_rows(inc, namespaces, explain=False))


def _not_found() -> Reply:
    return Reply(RichText().text("Incident not found"))


def _unavailable(e: Exception) -> Reply:
    rt = RichText().text("Unavailable").nl()
    return Reply(views.detail(rt, "Error", e, 120))


def _filtered(items: list[Incident], filt: str) -> list[Incident]:
    if filt == "o":
        return [i for i in items if i.status.phase not in TERMINAL]
    if filt in FILTER_NS:
        return [i for i in items if i.spec.target.namespace == FILTER_NS[filt]]
    return items


def incidents(reader: Reader, namespaces: tuple[str, ...], now: datetime, filt: str = "a", page: int = 0, *,
              edit: bool = False) -> Reply:
    """The Incident list: newest first, five a page, each with its age; ◀ Newer / Older ▶ and the
    filters All, Open, nexus-dev, nexus-prod (the namespaces configured)."""
    try:
        items = reader.list_incidents()
    except NexusClientError as e:
        return _unavailable(e)
    items.sort(key=lambda i: i.metadata.creationTimestamp or "", reverse=True)
    names = dict(FILTERS)
    filt = filt if filt in names else "a"
    shown = _filtered(items, filt)
    pages = min(max(1, -(-len(shown) // RECENT)), MAX_PAGE + 1)
    page = min(max(page, 0), pages - 1)
    rt = RichText().title(f"🗂 Incidents: {names[filt]}")
    count = f"{len(shown)} Incident{'' if len(shown) == 1 else 's'}"
    rt.text(f"{count} · page {page + 1} of {pages} · as of {_utc(now)}").nl().nl()
    rows: Buttons = []
    for inc in shown[page * RECENT:(page + 1) * RECENT]:
        _incident_lines(rt, inc, _age(inc.metadata.creationTimestamp, now))
        data = incident_data(inc)
        if data is not None:
            label = f"{views.icon(inc.status.phase)} {views.short_name(inc.metadata.name)}"
            rows.append([(label, data)])
    if not shown:
        rt.text("No Incidents").nl()
    elif rows:
        rt.nl().italic("Tap one for its Explain view.")
    nav = [(label, data) for label, data, ok in (("◀ Newer", list_data(filt, page - 1), page > 0),
                                                  ("Older ▶", list_data(filt, page + 1), page + 1 < pages))
           if ok and data]
    if nav:
        rows.append(nav)
    rows.append([(("✓ " if f == filt else "") + label, data) for f, label in FILTERS
                 if (f not in FILTER_NS or FILTER_NS[f] in namespaces) and (data := list_data(f, 0))])
    return Reply(rt, rows, edit)


def recent(reader: Reader, namespaces: tuple[str, ...] = (), now: datetime | None = None) -> Reply:
    return incidents(reader, namespaces, now or datetime.now(UTC))


def incident(reader: Reader, args: list[str], namespaces: tuple[str, ...] = (), now: datetime | None = None,
             links: Links = DEFAULT_LINKS) -> Reply:
    if not args:
        return recent(reader, namespaces, now)
    if len(args) != 1 or not NAME.fullmatch(args[0]):
        return Reply(RichText().text("usage: /incident <name>, for example "
                                     "/incident inc-0123abcd-1700000000"))
    try:
        return _explain(reader.get_incident(args[0]), namespaces, links)
    except NotFound:
        return _not_found()
    except NexusClientError as e:
        return _unavailable(e)


def _by_key(reader: Reader, key: str) -> Incident | Reply:
    try:
        matches = [i for i in reader.list_incidents() if uid_key(i.metadata.uid) == key]
    except NexusClientError as e:
        return _unavailable(e)
    return matches[0] if len(matches) == 1 else _not_found()


def incident_by_key(reader: Reader, key: str, namespaces: tuple[str, ...] = (),
                    links: Links = DEFAULT_LINKS) -> Reply:
    inc = _by_key(reader, key)
    return inc if isinstance(inc, Reply) else _explain(inc, namespaces, links)


def reasoning_by_key(reader: Reader, key: str) -> Reply:
    inc = _by_key(reader, key)
    if isinstance(inc, Reply):
        return inc
    *lead, last = views.reasoning(inc)
    return Reply(last, views.back_row(inc), leading=lead)


def runbooks_by_key(reader: Reader, key: str, links: Links = DEFAULT_LINKS) -> Reply:
    inc = _by_key(reader, key)
    return inc if isinstance(inc, Reply) else Reply(views.runbooks(inc, links), views.back_row(inc))


def commit_by_key(reader: Reader, key: str, links: Links = DEFAULT_LINKS) -> Reply:
    inc = _by_key(reader, key)
    return inc if isinstance(inc, Reply) else Reply(views.commit(inc, links), views.back_row(inc))


def view(v: View, reader: Reader, namespaces: tuple[str, ...], now: Callable[[], datetime] | None = None,
         links: Links = DEFAULT_LINKS) -> Reply:
    """A button: the same read-only answers as the typed commands."""
    t = (now or (lambda: datetime.now(UTC)))()
    if v.kind in (PODS, TABLE, CARDS):
        return pods(reader, namespaces, [v.arg], t, cards=v.kind == CARDS, edit=v.kind != PODS)
    if v.kind == INCIDENT:
        return incident_by_key(reader, v.arg, namespaces, links)
    if v.kind == REASONING:
        return reasoning_by_key(reader, v.arg)
    if v.kind == RUNBOOKS:
        return runbooks_by_key(reader, v.arg, links)
    if v.kind == COMMIT:
        return commit_by_key(reader, v.arg, links)
    if v.kind == LIST:
        return incidents(reader, namespaces, t, v.arg, v.page, edit=True)
    if v.kind == PICK:
        return pick(namespaces) if v.arg == "pods" else incidents(reader, namespaces, t)
    return Reply(usage())


def run(text: object, reader: Reader, namespaces: tuple[str, ...], now: Callable[[], datetime],
        links: Links = DEFAULT_LINKS) -> Reply:
    parsed = parse(text)
    if parsed is None:
        return Reply(usage())
    cmd, args = parsed
    if cmd == "status":
        return Reply(status(reader, namespaces, now()), STATUS_ROWS)
    if cmd == "pods":
        return pods(reader, namespaces, args, now())
    if cmd == "incident":
        return incident(reader, args, namespaces, now(), links)
    return Reply(usage())
