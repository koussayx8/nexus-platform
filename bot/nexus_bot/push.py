"""Pushes for AwaitingApproval, Escalated, Resolved and Blocked (D9; ADR-040 step 4).

One message per Incident and phase, to every allowlisted user's private chat. The bot keeps no
persistent state. On the first scan after a start:

- every Incident in AwaitingApproval is pushed, whatever its age, so a restart never loses a
  pending approval's buttons (owner, #252 Q1);
- an Incident in another push phase entered more than `catch_up` ago is marked as already pushed,
  so a restart does not resend old news; one entered within the window is pushed (it may be a
  resend). Phases missed while the bot was down for longer are visible in /status.

A failed send is retried on the next scan.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from nexus_client.errors import NexusClientError
from nexus_client.models import Incident

from . import views
from .kube import Reader
from .payload import APPROVE, EXPLAIN, REJECT, encode
from .telegram import TelegramClient, TelegramError

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
                 now: Callable[[], datetime], catch_up: timedelta) -> None:
        self._reader, self._tg, self._chats = reader, tg, chats
        self._now, self._catch_up = now, catch_up
        self._sent: set[tuple[str, str, int]] = set()  # (uid or name, phase, chat)
        self._first = True

    def tick(self) -> int:
        """One scan. Returns the number of messages sent."""
        try:
            incidents = self._reader.list_incidents()
        except NexusClientError as e:
            log.warning("push: cannot list Incidents: %s", type(e).__name__)
            return 0
        sent, live = 0, set()
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
                    self._tg.send_message(chat, views.push_text(inc), buttons(inc) or None)
                except TelegramError as e:
                    log.warning("push: send failed for chat %s: %s", chat, e)
                    continue
                self._sent.add(key)
                sent += 1
        self._sent = {k for k in self._sent if k[0] in live}  # forget deleted Incidents
        self._first = False
        return sent
