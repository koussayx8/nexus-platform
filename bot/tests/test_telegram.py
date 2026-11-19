"""The Telegram client against a fake Bot API server on 127.0.0.1 (no Internet)."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from nexus_bot.telegram import MAX_TEXT, HttpTransport, TelegramClient, TelegramError, keyboard

TOKEN = "123456789:" + "A" * 35


class FakeBotApi(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict[str, Any]]] = []
    updates: list[dict[str, Any]] = []
    status = 200

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        FakeBotApi.calls.append((self.path, body))
        if FakeBotApi.status != 200:
            out: dict[str, Any] = {"ok": False, "error_code": FakeBotApi.status, "description": "Unauthorized"}
            self.send_response(FakeBotApi.status)
        else:
            if method == "getUpdates":
                off = body.get("offset", 0)
                result: Any = [u for u in FakeBotApi.updates if u["update_id"] >= off]
            elif method == "sendMessage":
                result = {"message_id": 1, "chat": {"id": body["chat_id"]}}
            else:
                result = True
            out = {"ok": True, "result": result}
            self.send_response(200)
        data = json.dumps(out).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    FakeBotApi.calls, FakeBotApi.updates, FakeBotApi.status = [], [], 200
    srv = HTTPServer(("127.0.0.1", 0), FakeBotApi)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def client(base: str) -> TelegramClient:
    return TelegramClient(HttpTransport(TOKEN, base))


def test_offset_handling(server: str) -> None:
    FakeBotApi.updates = [{"update_id": 5}, {"update_id": 6}]
    tg = client(server)
    assert [u["update_id"] for u in tg.get_updates(None)] == [5, 6]
    assert tg.get_updates(7) == []
    first, second = FakeBotApi.calls[0][1], FakeBotApi.calls[1][1]
    assert "offset" not in first and second["offset"] == 7
    assert first["allowed_updates"] == ["message", "callback_query"] and first["timeout"] == 50


def test_delete_webhook_and_paths(server: str) -> None:
    client(server).delete_webhook()
    assert FakeBotApi.calls[0][0] == f"/bot{TOKEN}/deleteWebhook"


def test_no_request_carries_parse_mode_and_previews_are_off(server: str) -> None:
    tg = client(server)
    tg.send_message(1, "*bold* <b>x</b> [link](https://evil.example)", [("Approve", "a:" + "0" * 16 + ":" + "0" * 32)])
    tg.answer_callback("cb", "ok")
    tg.remove_buttons(1, 2)
    tg.get_updates(None)
    tg.delete_webhook()
    assert all("parse_mode" not in body for _p, body in FakeBotApi.calls)
    send = [b for p, b in FakeBotApi.calls if p.endswith("/sendMessage")][0]
    assert send["link_preview_options"] == {"is_disabled": True}
    assert send["text"].startswith("*bold* <b>x</b>")  # sent as is, interpreted as nothing


def test_text_is_cut_at_4096(server: str) -> None:
    client(server).send_message(1, "x" * 10000)
    assert len(FakeBotApi.calls[0][1]["text"]) == MAX_TEXT


def test_callback_data_over_64_bytes_is_refused() -> None:
    with pytest.raises(ValueError):
        keyboard([("x", "y" * 65)])


def test_token_never_in_error_text_http_error(server: str) -> None:
    FakeBotApi.status = 401
    with pytest.raises(TelegramError) as e:
        client(server).get_updates(None)
    assert TOKEN not in str(e.value) and TOKEN.split(":")[1] not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert "Unauthorized" in str(e.value)


def test_token_never_in_error_text_unreachable() -> None:
    with pytest.raises(TelegramError) as e:
        client("http://127.0.0.1:1").send_message(1, "x")  # nothing listens on port 1
    assert TOKEN not in str(e.value) and e.value.__cause__ is None
    assert TOKEN not in repr(HttpTransport(TOKEN))
