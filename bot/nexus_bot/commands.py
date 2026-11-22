"""Read-only commands (D21; ADR-040 decision 8). Fixed grammar, fixed namespaces, no writes:
these functions are given a Reader only.

`/pods` with no argument offers the two namespaces as buttons; `/incident` with no argument offers
the five most recent Incidents. Those buttons (`payload.View`) lead back here, to the same read-only
functions.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from nexus_client.errors import NexusClientError, NotFound
from nexus_client.models import Incident

from . import views
from .kube import Reader
from .payload import INCIDENT, PODS, View, incident_data, pods_data, uid_key
from .richtext import RichText

NAME = re.compile(r"^inc-[a-z0-9-]{1,59}$")
TERMINAL = frozenset({"Recorded", "Recommended", "Dismissed", "Resolved", "Escalated", "Blocked",
                      "Rejected", "Expired"})
RECENT = 5
# setMyCommands at start: the menu Telegram shows beside the text field
COMMANDS = (
    ("status", "Open Incidents, levels, Kill Switch"),
    ("pods", "Deployments and pods of nexus-dev or nexus-prod"),
    ("incident", "One Incident, or the 5 most recent"),
)

Buttons = list[list[tuple[str, str]]]  # rows of (label, callback_data)


@dataclass(frozen=True)
class Reply:
    message: RichText
    rows: Buttons = field(default_factory=list)


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
    rt.nl().italic(f"As of {now.astimezone(UTC):%H:%M} UTC")
    return rt


def pods(reader: Reader, namespaces: tuple[str, ...], args: list[str]) -> Reply:
    if not args:
        row = [(ns, data) for ns in namespaces if (data := pods_data(ns)) is not None]
        return Reply(RichText().title("📦 Pods").text("Which namespace?"), [row])
    if len(args) != 1 or args[0] not in namespaces:
        return Reply(RichText().text(f"usage: /pods <namespace>, one of {', '.join(namespaces)}"))
    ns = args[0]
    rt = RichText().title(f"📦 {ns}")
    try:
        deploys, pod_list = reader.deployments(ns), reader.pods(ns)
    except NexusClientError as e:
        rt.text("Unavailable").nl()
        return Reply(views.detail(rt, "Error", e, 120))
    rt.bold(f"Deployments ({len(deploys)})").nl()
    for d in deploys:
        rt.value(views.short_name(d.name, 20, 8), 40).text(f": {d.ready}/{d.desired} ready").nl()
    rt.nl().bold(f"Pods ({len(pod_list)})").nl()
    for p in pod_list:
        rt.value(views.short_name(p.name, 20, 8), 40).text(": ").value(p.phase, 20) \
            .text(f", {'ready' if p.ready else 'not ready'}, {p.restarts} restarts").nl()
    return Reply(rt)


def _explain(inc: Incident) -> Reply:
    return Reply(views.explain(inc))


def _not_found() -> Reply:
    return Reply(RichText().text("Incident not found"))


def _unavailable(e: Exception) -> Reply:
    rt = RichText().text("Unavailable").nl()
    return Reply(views.detail(rt, "Error", e, 120))


def recent(reader: Reader) -> Reply:
    try:
        items = reader.list_incidents()
    except NexusClientError as e:
        return _unavailable(e)
    items.sort(key=lambda i: i.metadata.creationTimestamp or "", reverse=True)
    rt = RichText().title("🗂 Recent Incidents")
    rows: Buttons = []
    for inc in items[:RECENT]:
        _incident_lines(rt, inc, views.hhmm(inc.metadata.creationTimestamp))
        data = incident_data(inc)
        if data is not None:
            label = f"{views.icon(inc.status.phase)} {views.short_name(inc.metadata.name)}"
            rows.append([(label, data)])
    if not items:
        rt.text("No Incidents").nl()
    elif rows:
        rt.nl().italic("Tap one for its Explain view.")
    return Reply(rt, rows)


def incident(reader: Reader, args: list[str]) -> Reply:
    if not args:
        return recent(reader)
    if len(args) != 1 or not NAME.fullmatch(args[0]):
        return Reply(RichText().text("usage: /incident <name>, for example "
                                     "/incident inc-0123abcd-1700000000"))
    try:
        return _explain(reader.get_incident(args[0]))
    except NotFound:
        return _not_found()
    except NexusClientError as e:
        return _unavailable(e)


def incident_by_key(reader: Reader, key: str) -> Reply:
    try:
        matches = [i for i in reader.list_incidents() if uid_key(i.metadata.uid) == key]
    except NexusClientError as e:
        return _unavailable(e)
    return _explain(matches[0]) if len(matches) == 1 else _not_found()


def view(v: View, reader: Reader, namespaces: tuple[str, ...]) -> Reply:
    """A /pods or /incident button: the same read-only answers as the typed commands."""
    if v.kind == PODS:
        return pods(reader, namespaces, [v.arg])
    if v.kind == INCIDENT:
        return incident_by_key(reader, v.arg)
    return Reply(usage())


def run(text: object, reader: Reader, namespaces: tuple[str, ...], now: Callable[[], datetime]) -> Reply:
    parsed = parse(text)
    if parsed is None:
        return Reply(usage())
    cmd, args = parsed
    if cmd == "status":
        return Reply(status(reader, namespaces, now()))
    if cmd == "pods":
        return pods(reader, namespaces, args)
    if cmd == "incident":
        return incident(reader, args)
    return Reply(usage())
