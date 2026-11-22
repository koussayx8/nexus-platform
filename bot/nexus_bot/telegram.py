"""The Telegram Bot API, outbound only (ADR-040 decision 2).

Long polling with `getUpdates`; no webhook, no listening socket. No `parse_mode` is ever sent, so
Telegram interprets no Markdown or HTML; a message is plain text with the `entities` the bot
computed (`richtext`), and link previews are off (decision 11, as amended). The token is part of
every request URL, so no exception raised here carries the URL, the token or a chained cause.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from .richtext import MAX_TEXT, RichText, cut_point, units

API = "https://api.telegram.org"
MAX_CALLBACK_DATA = 64  # Bot API: callback_data, in bytes
# Seconds the server holds a getUpdates call open. 25, not 50: the live bot logged a getUpdates
# TimeoutError about once a minute at 50 s, most likely an idle connection dropped on the path
# before the server answered. The client waits POLL_SLACK longer than the server holds the call.
POLL_TIMEOUT = 25
POLL_SLACK = 10
ALLOWED_UPDATES = ["message", "callback_query"]
ELLIPSIS = "…"


class TelegramError(Exception):
    """A Bot API call failed. The message names the method and the cause, never the token."""


class Transport(Protocol):
    def call(self, method: str, payload: dict[str, Any], timeout: float) -> Any:
        """POST one Bot API method; return its `result`, or raise TelegramError."""
        ...


class HttpTransport:
    def __init__(self, token: str, base: str = API) -> None:
        self._token = token
        self._base = base.rstrip("/")

    def __repr__(self) -> str:
        return f"HttpTransport(base={self._base!r})"

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "<token>") if self._token else text

    def call(self, method: str, payload: dict[str, Any], timeout: float) -> Any:
        url = f"{self._base}/bot{self._token}/{method}"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        cause: str
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https base
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # the Bot API's own error body; its description never holds the token
            try:
                body = json.loads(e.read())
            except (ValueError, OSError):
                body = {}
            cause = f"HTTP {e.code}: {body.get('description', 'no description')}"
            raise TelegramError(self._redact(f"{method}: {cause}")) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            reason = getattr(e, "reason", None) or type(e).__name__
            raise TelegramError(self._redact(f"{method}: {reason}")) from None
        if not isinstance(body, dict) or not body.get("ok"):
            desc = body.get("description", "not ok") if isinstance(body, dict) else "malformed reply"
            raise TelegramError(self._redact(f"{method}: {desc}"))
        return body.get("result")


def cut(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + ELLIPSIS


def fit(text: str, limit: int = MAX_TEXT) -> str:
    """The whole text when it fits one message (counted in UTF-16 units); otherwise its start and a
    last line saying how much was shown. The full model text stays in the Flight Recorder."""
    if units(text) <= limit:
        return text
    shown, note = cut_point(text, limit)
    return text[:shown] + note


def keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    """Rows of inline buttons: (label, callback_data)."""
    for row in rows:
        for _label, data in row:
            if len(data.encode()) > MAX_CALLBACK_DATA:
                raise ValueError("callback_data exceeds 64 bytes")
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                for row in rows]}


class TelegramClient:
    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def _call(self, method: str, payload: dict[str, Any], timeout: float = 15) -> Any:
        if "parse_mode" in payload:  # decision 11; tests check every payload the bot builds
            raise ValueError("parse_mode is never sent")
        return self._t.call(method, payload, timeout)

    def delete_webhook(self) -> None:
        """At start: a webhook set with a stolen token must not silently take the updates."""
        self._call("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": POLL_TIMEOUT, "allowed_updates": ALLOWED_UPDATES}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=POLL_TIMEOUT + POLL_SLACK)
        return [u for u in (result or []) if isinstance(u, dict) and isinstance(u.get("update_id"), int)]

    def set_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        """The command menu (default scope): names and short descriptions, the bot's own text."""
        self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})

    def send_message(self, chat_id: int, text: str | RichText,
                     buttons: list[tuple[str, str]] | None = None, *,
                     rows: list[list[tuple[str, str]]] | None = None) -> dict[str, Any]:
        """`buttons` is one row; `rows` is several. A RichText is sent with its entities."""
        payload: dict[str, Any] = {"chat_id": chat_id, "link_preview_options": {"is_disabled": True}}
        if isinstance(text, RichText):
            msg = text.build(MAX_TEXT)
            payload["text"] = msg.text
            if msg.entities:
                payload["entities"] = msg.entities
        else:
            payload["text"] = fit(text)
        markup = (rows or []) + ([buttons] if buttons else [])
        if any(markup):
            payload["reply_markup"] = keyboard([r for r in markup if r])
        result = self._call("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    def edit_message(self, chat_id: int, message_id: int, text: RichText,
                     rows: list[list[tuple[str, str]]] | None = None) -> None:
        """Replace a message the bot sent (a list page, a filter, Refresh), entities and buttons."""
        msg = text.build(MAX_TEXT)
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": msg.text,
                                   "link_preview_options": {"is_disabled": True},
                                   "reply_markup": keyboard([r for r in rows or [] if r])}
        if msg.entities:
            payload["entities"] = msg.entities
        self._call("editMessageText", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": cut(text, 200)})

    def remove_buttons(self, chat_id: int, message_id: int) -> None:
        self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                              "reply_markup": {"inline_keyboard": []}})
