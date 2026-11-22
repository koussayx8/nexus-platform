"""Pushes for AwaitingApproval, Escalated, Resolved and Blocked (D9; ADR-040 step 4).

One message per Incident and phase, to every allowlisted user's private chat. The bot keeps no
persistent state. On the first scan after a start:

- every Incident in AwaitingApproval is pushed, whatever its age, so a restart never loses a
  pending approval's buttons (owner, #252 Q1);
- an Incident in another push phase entered more than `catch_up` ago is marked as already pushed,
  so a restart does not resend old news; one entered within the window is pushed (it may be a
  resend). Phases missed while the bot was down for longer are visible in /status.

A failed send is retried on the next scan.

The AwaitingApproval push keeps its three buttons, Approve, Reject, Explain, and nothing else
(tests/test_golden_approval.py). A result push (Escalated, Resolved, Blocked) also offers 🧠 Reasoning,
📦 Pods, 📚 Runbooks and 🔗 Commit where each applies.

Two more pushes (2026-11-22), read with the bot's existing permissions (the Kill Switch ConfigMap and
the two namespaces' level label): the Kill Switch changing state (🛑 halted / 🟢 active) and a
namespace's autonomy level changing (L3 → L1). The first value read is the baseline: no push for the
state found at start. A failed read changes nothing; a failed send is retried on the next scan.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from nexus_client.errors import NexusClientError
from nexus_client.models import Incident

from . import views
from .config import NAMESPACES
from .kube import Reader
from .payload import APPROVE, EXPLAIN, REJECT, encode
from .richtext import RichText
from .telegram import TelegramClient, TelegramError
from .views import DEFAULT_LINKS, Links

log = logging.getLogger("nexus_bot.push")
PUSH_PHASES = frozenset({"AwaitingApproval", "Escalated", "Resolved", "Blocked"})
TERMINAL_STAMP = "terminal"  # the operator stamps every terminal phase under this key


def phase_started(inc: Incident) -> datetime | None:
    phase = inc.status.phase or ""
    ts = inc.status.timestamps
    raw = ts.get(phase.lower()) or (ts.get(TERMINAL_STAMP) if phase != "AwaitingApproval" else None)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def buttons(inc: Incident) -> list[tuple[str, str]]:
    actions = [("Approve", APPROVE), ("Reject", REJECT), ("Explain", EXPLAIN)] \
        if inc.status.phase == "AwaitingApproval" else [("Explain", EXPLAIN)]
    out = []
    for label, letter in actions:
        data = encode(letter, inc)
        if data is not None:
            out.append((label, data))
    return out


class Pusher:
    def __init__(self, reader: Reader, tg: TelegramClient, chats: list[int], *,
                 now: Callable[[], datetime], catch_up: timedelta,
                 namespaces: tuple[str, ...] = NAMESPACES, links: Links = DEFAULT_LINKS) -> None:
        self._reader, self._tg, self._chats = reader, tg, chats
        self._now, self._catch_up = now, catch_up
        self._namespaces, self._links = namespaces, links
        self._sent: set[tuple[str, str, int]] = set()  # (uid or name, phase, chat)
        self._shown: dict[tuple[str, int], str] = {}  # (killswitch | level:<ns>, chat) -> value last shown
        self._first = True

    def _states(self) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            out["killswitch"] = self._reader.killswitch() or "missing"
        except NexusClientError as e:
            log.warning("push: cannot read the Kill Switch: %s", type(e).__name__)
        for ns in self._namespaces:
            try:
                out[f"level:{ns}"] = views.level_text(self._reader.level(ns))
            except NexusClientError as e:
                log.warning("push: cannot read the level of %s: %s", ns, type(e).__name__)
        return out

    def _state_message(self, what: str, before: str, after: str, now: datetime) -> RichText:
        if what == "killswitch":
            return views.killswitch_change(before, after, now)
        return views.level_change(what.removeprefix("level:"), before, after, now)

    def states_tick(self) -> int:
        """The Kill Switch and the levels: a push per change since the value last shown."""
        sent, now = 0, self._now()
        for what, value in self._states().items():
            for chat in self._chats:
                before = self._shown.get((what, chat))
                if before is None:  # the baseline: the state found at start is not news
                    self._shown[(what, chat)] = value
                    continue
                if before == value:
                    continue
                try:
                    self._tg.send_message(chat, self._state_message(what, before, value, now))
                except TelegramError as e:
                    log.warning("push: send failed for chat %s: %s", chat, e)
                    continue
                self._shown[(what, chat)] = value
                sent += 1
        return sent

    def tick(self) -> int:
        """One scan. Returns the number of messages sent."""
        sent = self.states_tick()
        try:
            incidents = self._reader.list_incidents()
        except NexusClientError as e:
            log.warning("push: cannot list Incidents: %s", type(e).__name__)
            return sent
        live = set()
        now = self._now()
        for inc in incidents:
            phase = inc.status.phase or ""
            ident = inc.metadata.uid or inc.metadata.name
            live.add(ident)
            if phase not in PUSH_PHASES:
                continue
            started = phase_started(inc)
            old = (self._first and phase != "AwaitingApproval" and started is not None
                   and now - started > self._catch_up)
            for chat in self._chats:
                key = (ident, phase, chat)
                if key in self._sent:
                    continue
                if old:
                    self._sent.add(key)
                    continue
                try:
                    if phase == "AwaitingApproval":  # unchanged: Approve, Reject, Explain only
                        self._tg.send_message(chat, views.push(inc), buttons(inc) or None)
                    else:
                        self._tg.send_message(chat, views.push(inc, self._links),
                                              rows=views.incident_rows(inc, self._namespaces, explain=True))
                except TelegramError as e:
                    log.warning("push: send failed for chat %s: %s", chat, e)
                    continue
                self._sent.add(key)
                sent += 1
        self._sent = {k for k in self._sent if k[0] in live}  # forget deleted Incidents
        self._first = False
        return sent
