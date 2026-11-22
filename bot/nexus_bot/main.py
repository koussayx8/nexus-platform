"""The bot's loops: the Telegram long poll (updates), the Incident scan (pushes) and C8, the revert
pull request (ADR-041), when it is configured.

Log lines carry IDs and outcomes, never message text or a token. C8's own lines (a pull request
opened, a refusal and its check, a merge seen) name only Incidents, commits and pull requests.
"""

import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nexus_revert.config import DEFAULT_CONFIG as REVERT_CONFIG
from nexus_revert.config import DEFAULT_TOKEN_FILE as REVERT_TOKEN_FILE
from nexus_revert.config import ConfigError as RevertConfigError
from nexus_revert.config import load as load_revert
from nexus_revert.service import IncidentReader, Service
from nexusctl.render.sanitize import sanitize

from . import commands, views
from .auth import Sender, log_ids, sender_of
from .config import Config, ConfigError, load
from .decide import handle_press
from .kube import ApproverFactory, Reader
from .payload import decode, decode_view
from .push import Pusher
from .richtext import RichText
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("nexus_bot")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Bot:
    def __init__(self, cfg: Config, tg: TelegramClient, reader: Reader, approver_for: ApproverFactory,
                 now: Callable[[], datetime] = utcnow) -> None:
        self.cfg, self.tg, self.reader, self.approver_for, self.now = cfg, tg, reader, approver_for, now
        self.offset: int | None = None

    def handle(self, update: dict[str, Any]) -> None:
        sender = sender_of(update, self.cfg.allowlist)
        if sender is None:
            log.info("dropped %s", json.dumps(log_ids(update)))
            return
        if isinstance(update.get("callback_query"), dict):
            self._press(update["callback_query"], sender)
        elif isinstance(update.get("message"), dict):
            reply = commands.run(update["message"].get("text"), self.reader, self.cfg.namespaces, self.now)
            self.tg.send_message(sender.user_id, reply.message, rows=reply.rows)

    def _press(self, cq: dict[str, Any], sender: Sender) -> None:
        cq_id = str(cq.get("id", ""))
        view = decode_view(cq.get("data"))
        if view is not None:  # a /pods or /incident button: read-only, given the Reader only
            try:
                self.tg.answer_callback(cq_id)
            except TelegramError as e:
                log.warning("cannot answer callback: %s", e)
            reply = commands.view(view, self.reader, self.cfg.namespaces)
            log.info("view %s from %s", view.kind, sender.user_id)
            self.tg.send_message(sender.user_id, reply.message, rows=reply.rows)
            return
        press = decode(cq.get("data"))
        if press is None:
            log.info("dropped malformed payload from %s", sender.user_id)
            self.tg.answer_callback(cq_id)
            return
        try:  # a late answer (e.g. after downtime) is refused by Telegram; the press still runs
            self.tg.answer_callback(cq_id, "Working…")
        except TelegramError as e:
            log.warning("cannot answer callback: %s", e)
        outcome = handle_press(press, sender, reader=self.reader, approver_for=self.approver_for,
                               group=self.cfg.approver_group, now=self.now)
        log.info("press %s from %s: wrote=%s", press.action, sender.user_id, outcome.wrote)
        msg = cq.get("message") or {}
        if outcome.remove_buttons and isinstance(msg.get("message_id"), int):
            try:
                self.tg.remove_buttons(sender.user_id, msg["message_id"])
            except TelegramError as e:
                log.warning("cannot remove buttons: %s", e)
        self.tg.send_message(sender.user_id, outcome.message)

    def poll_once(self) -> int:
        """One getUpdates call; handles each update, then advances the offset past it."""
        updates = self.tg.get_updates(self.offset)
        for u in updates:
            try:
                self.handle(u)
            except Exception as e:  # one bad update must not stop the loop or replay forever
                log.warning("update %s failed: %s", u.get("update_id"), type(e).__name__)
            self.offset = u["update_id"] + 1
        return len(updates)


def _loop(fn: Callable[[], object], pause: float, name: str, stop: threading.Event,
          backoff: float = 5) -> None:
    while not stop.is_set():
        try:
            fn()
        except Exception as e:
            log.warning("%s: %s", name, e if isinstance(e, TelegramError) else type(e).__name__)
            stop.wait(backoff)
            continue
        stop.wait(pause)


C8_PREFIX = "C8 revert: "
# (pattern over the text after "<incident>: ", title). Only C8's own fixed wording, anchored at the
# start, picks the title; the rest of the text (a check's reason, a GitHub error, a link) is
# untrusted and stays in the blockquote.
C8_TITLES = (
    (re.compile(r"revert pull request #\d+ opened against "), "🟠 C8: revert PR opened"),
    (re.compile(r"revert pull request #\d+ merged by "), "🟢 C8: revert PR merged"),
    (re.compile(r"revert pull request #\d+ closed, not merged"), "⚪ C8: revert PR closed"),
    (re.compile(r"revert pull request #\d+ still open after "), "⚪ C8: revert PR no longer watched"),
    (re.compile(r"no revert pull request "), "⛔ C8: no revert PR"),
    (re.compile(r"revert of [0-9a-f]{12} \(.*\) pushed to the fork; open the pull request: "),
     "🟠 C8: open the revert PR"),
    (re.compile(r"the fork has the revert, but the pull request was refused "), "🟠 C8: open the revert PR"),
)
_C8_INCIDENT = re.compile(r"^(inc-[a-z0-9-]{1,59}): (.*)$", re.DOTALL)


def c8_message(text: str) -> RichText:
    """C8's line for the chat: a title chosen from C8's fixed wording, the Incident, and the whole
    line, sanitized, in a blockquote."""
    line = sanitize(text, 1000)
    m = _C8_INCIDENT.match(line)
    rest = m[2] if m else line
    title = next((t for rx, t in C8_TITLES if m and rx.match(rest)), "⚪ C8 revert")
    rt = RichText().title(title)
    if m:
        rt.fact("Incident", views.short_name(m[1]), 40)
    with rt.quote():
        rt.untrusted(rest, 1000)
    return rt


def c8_notifier(tg: TelegramClient, chats: list[int]) -> Callable[[str], None]:
    """C8's result to the log and to every allowlisted chat (ADR-041 decision 10, "As built"): a pull
    request opened, a refusal and the check that refused it, a merge or a close seen. Sanitized, in
    a blockquote under a title the bot picks (ADR-040 decision 11, as amended). A failed send is
    logged and not retried: the line is in the log."""

    def notify(text: str) -> None:
        line = C8_PREFIX + sanitize(text, 1000)
        log.info("%s", line)
        msg = c8_message(text)
        for chat in chats:
            try:
                tg.send_message(chat, msg)
            except TelegramError as e:
                log.warning("c8: send failed for chat %s: %s", chat, e)

    return notify


def _kube_incidents() -> IncidentReader:
    from nexus_revert.main import KubeIncidentReader

    return KubeIncidentReader()


def c8_service(notify: Callable[[str], None], env: dict[str, str] | None = None, *,
               reader_factory: Callable[[], IncidentReader] = _kube_incidents,
               now: Callable[[], datetime] = utcnow) -> Service | None:
    """C8, or None. On only when both the revert config and the GitHub token file are present
    (the Secret mount is optional); otherwise, or when either is unusable, one log line and off.
    Nothing here stops the bot."""
    env = dict(os.environ) if env is None else env
    files = (("the revert config", Path(env.get("NEXUS_REVERT_CONFIG", REVERT_CONFIG))),
             ("the GitHub token file", Path(env.get("NEXUS_REVERT_TOKEN_FILE", REVERT_TOKEN_FILE))))
    missing = [what for what, path in files if not path.is_file()]
    if missing:
        log.info("C8 off: %s absent", " and ".join(missing))
        return None
    try:
        cfg = load_revert(env)
    except RevertConfigError as e:  # the message never holds the token
        log.error("C8 off: %s", e)
        return None
    from nexus_revert.gitcmd import SubprocessGit
    from nexus_revert.github import GitHub
    from nexus_revert.github import HttpTransport as GitHubTransport

    def git_factory(work: Path) -> SubprocessGit:
        return SubprocessGit(work, push_url=cfg.fork_url, login=cfg.machine, token=cfg.token)

    try:
        reader = reader_factory()
        svc = Service(cfg, reader, GitHub(GitHubTransport(cfg.token, cfg.github_api), cfg.upstream),
                      git_factory, notify, now=now, scratch=Path(env.get("NEXUS_REVERT_SCRATCH", "/tmp")))
        recovered = svc.recover()
    except Exception as e:
        log.error("C8 off: cannot start: %s", type(e).__name__)
        return None
    log.info("C8 on: %d branch(es), mode %s, every %gs, %d open revert pull request(s) recovered",
             len(cfg.branches), cfg.mode, cfg.poll_seconds, recovered)
    return svc


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        cfg = load()
    except ConfigError as e:
        log.error("config: %s", e)
        return 2
    from .kube import KubeReader, kube_approver
    from .telegram import HttpTransport

    tg = TelegramClient(HttpTransport(cfg.token))
    bot = Bot(cfg, tg, KubeReader(), kube_approver)
    tg.delete_webhook()
    try:
        tg.set_commands(commands.COMMANDS)
    except TelegramError as e:  # the menu is a convenience; typed commands work without it
        log.warning("cannot set the command menu: %s", e)
    pusher = Pusher(bot.reader, tg, sorted(cfg.allowlist), now=utcnow,
                    catch_up=timedelta(minutes=cfg.catch_up_minutes))
    stop = threading.Event()
    threading.Thread(target=_loop, args=(pusher.tick, cfg.poll_seconds, "push", stop), daemon=True).start()
    c8 = c8_service(c8_notifier(tg, sorted(cfg.allowlist)))
    if c8 is not None:  # its own thread: a C8 error is logged there and never reaches the poll loop
        threading.Thread(target=_loop, args=(c8.tick, c8.cfg.poll_seconds, "c8", stop), daemon=True).start()
    log.info("started: %d allowlisted user(s), namespaces %s", len(cfg.allowlist), ",".join(cfg.namespaces))
    try:
        _loop(bot.poll_once, 0, "poll", stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
