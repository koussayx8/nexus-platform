"""Read-only commands (D21; ADR-040 decision 8). Fixed grammar, fixed namespaces, no writes:
these functions are given a Reader only."""

import re
from collections.abc import Callable
from datetime import datetime

from nexus_client.errors import NexusClientError, NotFound
from nexusctl.render.sanitize import sanitize
from nexusctl.render.views import table_text

from . import views
from .kube import Reader

NAME = re.compile(r"^inc-[a-z0-9-]{1,59}$")
TERMINAL = frozenset({"Recorded", "Recommended", "Dismissed", "Resolved", "Escalated", "Blocked",
                      "Rejected", "Expired"})
USAGE = "Commands: /status, /pods <namespace>, /incident <name>"


def parse(text: object) -> tuple[str, list[str]] | None:
    """'/cmd@BotName a b' -> ('cmd', ['a', 'b']); None for anything that is not a command."""
    if not isinstance(text, str) or not text.startswith("/"):
        return None
    words = text.strip().split()
    cmd = words[0][1:].split("@", 1)[0].lower()
    return (cmd, words[1:]) if re.fullmatch(r"[a-z]{1,20}", cmd) else None


def status(reader: Reader, namespaces: tuple[str, ...], now: datetime) -> str:
    lines = []
    try:
        open_ = [i for i in reader.list_incidents() if i.status.phase not in TERMINAL]
        lines += [f"Open Incidents: {len(open_)}"]
        if open_:
            lines.append(table_text(open_, now))
    except NexusClientError as e:
        lines.append(f"Incidents: unavailable ({sanitize(e, 120)})")
    for ns in namespaces:
        try:
            level = reader.level(ns)
            lines.append(f"Level {ns}: {sanitize(level, 8) if level is not None else 'unset'}")
        except NexusClientError as e:
            lines.append(f"Level {ns}: unavailable ({sanitize(e, 80)})")
    try:
        state = reader.killswitch()
        lines.append(f"Kill Switch: {sanitize(state, 20) if state is not None else 'missing (K4 denies)'}")
    except NexusClientError as e:
        lines.append(f"Kill Switch: unavailable ({sanitize(e, 80)})")
    return "\n".join(lines)


def pods(reader: Reader, namespaces: tuple[str, ...], args: list[str]) -> str:
    if len(args) != 1 or args[0] not in namespaces:
        return f"usage: /pods <namespace>, one of {', '.join(namespaces)}"
    ns = args[0]
    try:
        deploys, pod_list = reader.deployments(ns), reader.pods(ns)
    except NexusClientError as e:
        return f"{ns}: unavailable ({sanitize(e, 120)})"
    lines = [f"{ns}: {len(deploys)} Deployments, {len(pod_list)} pods"]
    lines += [f"deploy {sanitize(d.name, 63)}  {d.ready}/{d.desired} ready" for d in deploys]
    lines += [f"pod {sanitize(p.name, 63)}  {sanitize(p.phase, 20)}  {'ready' if p.ready else 'not ready'}  "
              f"restarts {p.restarts}" for p in pod_list]
    return "\n".join(lines)


def incident(reader: Reader, args: list[str]) -> str:
    if len(args) != 1 or not NAME.fullmatch(args[0]):
        return "usage: /incident <name>, for example /incident inc-0123abcd-1700000000"
    try:
        return views.explain(reader.get_incident(args[0]))
    except NotFound:
        return "Incident not found"
    except NexusClientError as e:
        return f"unavailable ({sanitize(e, 120)})"


def run(text: object, reader: Reader, namespaces: tuple[str, ...], now: Callable[[], datetime]) -> str:
    parsed = parse(text)
    if parsed is None:
        return USAGE
    cmd, args = parsed
    if cmd == "status":
        return status(reader, namespaces, now())
    if cmd == "pods":
        return pods(reader, namespaces, args)
    if cmd == "incident":
        return incident(reader, args)
    return USAGE
