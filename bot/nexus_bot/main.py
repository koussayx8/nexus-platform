"""The bot's two loops: the Telegram long poll (updates) and the Incident scan (pushes).

Log lines carry IDs and outcomes, never message text or the token.
"""

import json
import logging
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from . import commands
from .auth import Sender, log_ids, sender_of
from .config import Config, ConfigError, load
from .decide import handle_press
from .kube import ApproverFactory, Reader
from .payload import decode
from .push import Pusher
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
            self.tg.send_message(sender.user_id, reply)

    def _press(self, cq: dict[str, Any], sender: Sender) -> None:
        press = decode(cq.get("data"))
        cq_id = str(cq.get("id", ""))
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
        self.tg.send_message(sender.user_id, outcome.text)

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


def _loop(fn: Callable[[], object], pause: float, name: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            fn()
        except Exception as e:
            log.warning("%s: %s", name, e if isinstance(e, TelegramError) else type(e).__name__)
            stop.wait(5)
            continue
        stop.wait(pause)


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
    pusher = Pusher(bot.reader, tg, sorted(cfg.allowlist), now=utcnow,
                    catch_up=timedelta(minutes=cfg.catch_up_minutes))
    stop = threading.Event()
    threading.Thread(target=_loop, args=(pusher.tick, cfg.poll_seconds, "push", stop), daemon=True).start()
    log.info("started: %d allowlisted user(s), namespaces %s", len(cfg.allowlist), ",".join(cfg.namespaces))
    try:
        _loop(bot.poll_once, 0, "poll", stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
