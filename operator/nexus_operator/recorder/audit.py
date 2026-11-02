"""The Audit-Id client wrapper (spec §14, §27; M2-5 spike).

The operator's mutating requests go through here so that the API server's `Audit-Id` response
header is captured with the status. The Flight Recorder stores it in `actions.audit_id`, and
reconciliation joins the audit log to the recorder on it.

Nothing here raises on a 4xx: a denial (RBAC, Kyverno, PDB) is an outcome to record, and its
message is how the blocking layer is attributed. Only a transport failure raises (aiohttp's own
errors), and the caller records that as an outcome too.

It uses the operator identity only: the in-cluster token, read again on every request.
"""

import dataclasses
import json
import ssl
from collections.abc import Callable
from typing import Any

import aiohttp

from ..kube import USER_AGENT, Credentials

AUDIT_ID_HEADER = "Audit-Id"
MAX_BODY_CHARS = 2000


@dataclasses.dataclass(frozen=True)
class ApiResult:
    status: int
    audit_id: (
        str | None
    )  # None: the server sent no Audit-Id (audit disabled, or a proxy)
    body: Any  # parsed JSON, or the text (cut to MAX_BODY_CHARS) when it is not JSON

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def message(self) -> str:
        """The server's explanation of a failure: a Status message, or the cut body."""
        if isinstance(self.body, dict):
            return str(self.body.get("message") or "")[:MAX_BODY_CHARS]
        return str(self.body or "")[:MAX_BODY_CHARS]


class AuditedClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        credentials: Callable[[], Credentials],
    ) -> None:
        self._session = session
        self._credentials = credentials

    @classmethod
    def create(
        cls, credentials: Callable[[], Credentials], timeout_s: float = 10
    ) -> "AuditedClient":
        session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            connector=aiohttp.TCPConnector(
                ssl=ssl.create_default_context(cafile=credentials().ca_path)
            ),
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        )
        return cls(session, credentials)

    async def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        dry_run: bool = False,
        content_type: str = "application/json",
    ) -> ApiResult:
        """One request; `path` starts with '/'. `dry_run` adds dryRun=All (Safety Gate stage 7)."""
        creds = self._credentials()
        headers = {"Authorization": f"Bearer {creds.token}"}
        kwargs: dict[str, Any] = {}
        if body is not None:
            headers["Content-Type"] = content_type
            kwargs["data"] = json.dumps(body)
        if dry_run:
            kwargs["params"] = {"dryRun": "All"}
        async with self._session.request(
            method, creds.server + path, headers=headers, **kwargs
        ) as r:
            text = await r.text()
            try:
                parsed: Any = json.loads(text) if text else None
            except ValueError:
                parsed = text[:MAX_BODY_CHARS]
            return ApiResult(r.status, r.headers.get(AUDIT_ID_HEADER), parsed)

    async def close(self) -> None:
        await self._session.close()
