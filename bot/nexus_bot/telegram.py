"""The Telegram Bot API, outbound only (ADR-040 decision 2).

Long polling with `getUpdates`; no webhook, no listening socket. Every message is plain text: no
`parse_mode` is ever sent, so Telegram interprets no Markdown or HTML, and link previews are off
(decision 11). The token is part of every request URL, so no exception raised here carries the
URL, the token or a chained cause.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

API = "https://api.telegram.org"
MAX_TEXT = 4096  # Bot API: sendMessage text, after entity parsing
MAX_CALLBACK_DATA = 64  # Bot API: callback_data, in bytes
POLL_TIMEOUT = 50  # seconds the server holds a getUpdates call open
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


def keyboard(buttons: list[tuple[str, str]]) -> dict[str, Any]:
    """One row of inline buttons: (label, callback_data)."""
    for _label, data in buttons:
        if len(data.encode()) > MAX_CALLBACK_DATA:
            raise ValueError("callback_data exceeds 64 bytes")
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}


class TelegramClient:
    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def _call(self, method: str, payload: dict[str, Any], timeout: float = 15) -> Any:
        assert "parse_mode" not in payload  # decision 11: plain text only, by construction
        return self._t.call(method, payload, timeout)

    def delete_webhook(self) -> None:
        """At start: a webhook set with a stolen token must not silently take the updates."""
        self._call("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": POLL_TIMEOUT, "allowed_updates": ALLOWED_UPDATES}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=POLL_TIMEOUT + 10)
        return [u for u in (result or []) if isinstance(u, dict) and isinstance(u.get("update_id"), int)]

    def send_message(self, chat_id: int, text: str,
                     buttons: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": cut(text),
                                   "link_preview_options": {"is_disabled": True}}
        if buttons:
            payload["reply_markup"] = keyboard(buttons)
        result = self._call("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": cut(text, 200)})

    def remove_buttons(self, chat_id: int, message_id: int) -> None:
        self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                              "reply_markup": {"inline_keyboard": []}})
