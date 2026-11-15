"""A stand-in for RESTClientObject.request: records requests, answers with a canned response."""

import json
from typing import Any

from kubernetes.client.rest import RESTResponse


class _Raw:
    def __init__(self, status: int, body: dict[str, Any], audit: str | None) -> None:
        self.status, self.reason = status, "OK" if status < 400 else "Forbidden"
        self.data = json.dumps(body).encode()
        self.headers = {"Content-Type": "application/json", **({"Audit-Id": audit} if audit else {})}


class FakeHttp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, str], Any]] = []
        self._next: _Raw | None = None

    def reply(self, status: int, body: dict[str, Any], audit: str | None = None) -> None:
        self._next = _Raw(status, body, audit)

    def request(self, method: str, url: str, headers: dict[str, str] | None = None,
                body: Any = None, **_kw: Any) -> RESTResponse:
        self.sent.append((method, dict(headers or {}), body))
        assert self._next is not None, "no reply queued"
        return RESTResponse(self._next)
