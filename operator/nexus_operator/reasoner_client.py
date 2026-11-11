"""The Reasoner Client (spec §4, §6 transitions 4-6, §17): one HTTP call to the untrusted Reasoner.

  POST {reasonerEndpoint}/v1/propose   {"bundle": <redacted evidence/v1>, "context": {...}}
  200 -> {"proposal": <proposal/v1>, "metadata": {...}}
  422 -> {"error", "detail", "raw", "metadata"}      (the Reasoner refused or its model misbehaved)

Rules this module keeps:
* Only the operator's redacted bundle leaves. Before anything is sent the bundle is checked to be
  redacted (counts present, hash equal to the canonical hash, and a second pass over every string
  finds nothing); otherwise nothing is sent and the result is `not_redacted` (Q1).
* One attempt, 30 s timeout, no retry. The caller falls back to the embedded Rules Engine once.
* The answer is data. It is read with a size cap, parsed, and checked with proposal.validate
  before anything else sees it. Whether the action is in the catalogue and within bounds is the
  Safety Gate's decision, not this client's: an out-of-envelope proposal (R0) must reach the Gate
  to be blocked there and counted.
* Every call has a request id (sent as X-Request-Id and kept with the result) for the Flight
  Recorder. The Reasoner's metadata is kept only as short strings and numbers.

The client never raises on an HTTP or transport failure: that is a Result with a reason.
"""

import dataclasses
import json
import time
import uuid
from typing import Any

import aiohttp

from . import proposal
from .redact import unredacted

TIMEOUT_S = 30
MAX_RESPONSE_BYTES = 1_048_576
CATALOGUE_VERSION = "1"
# evidence/v1.1 (ADR-039) adds metrics.zscores.inflight; v1 bundles stay valid input.
EVIDENCE_SCHEMAS = ("evidence/v1", "evidence/v1.1")
_META_KEYS = (
    "mode",
    "model",
    "arm",
    "provider",
    "corpusHash",
    "promptVersion",
    "latencyMs",
    "noRunbook",
    "rejection",
)


@dataclasses.dataclass
class Result:
    ok: bool
    request_id: str
    reason: str | None = (
        None  # not_redacted | transport | timeout | http_<n> | unparseable | schema_invalid | rejected
    )
    doc: dict | None = None
    raw: str | None = None
    errors: list[str] = dataclasses.field(default_factory=list)
    meta: dict = dataclasses.field(default_factory=dict)
    status: int | None = None
    latency_ms: int | None = None


def check_redacted(bundle: Any) -> str | None:
    """None when the bundle is the operator's redacted evidence/v1.1 bundle, else why not.

    evidence/v1 is still accepted: v1.1 only adds `metrics.zscores.inflight` (ADR-039).
    The reason never repeats a value from the bundle, only a class name."""
    if not isinstance(bundle, dict) or bundle.get("schema") not in EVIDENCE_SCHEMAS:
        return "not an evidence/v1 or evidence/v1.1 bundle"
    if not isinstance((bundle.get("redaction") or {}).get("counts"), dict):
        return "redaction.counts missing"
    body = {k: v for k, v in bundle.items() if k != "hash"}
    if bundle.get("hash") != proposal.hash_of(body):
        return "hash does not match the bundle"
    left = unredacted(bundle)
    if left:
        return f"unredacted content found: {', '.join(left)}"
    return None


def _clean_meta(meta: Any) -> dict:
    """Short scalars from the Reasoner's metadata; anything else is dropped."""
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for key in _META_KEYS:
        v = meta.get(key)
        if isinstance(v, bool | int | float) or v is None:
            out[key] = v
        elif isinstance(v, str):
            out[key] = v[:200]
    chunks = meta.get("retrievedChunks")
    if isinstance(chunks, list):
        out["retrievedChunks"] = [c[:100] for c in chunks if isinstance(c, str)][:20]
    return out


class ReasonerClient:
    def __init__(
        self,
        endpoint: str,
        *,
        mode: str,
        model: str,
        effort: str | None = None,
        timeout_s: float = TIMEOUT_S,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.mode, self.model, self.effort = mode, model, effort
        self.timeout_s = timeout_s
        self._session = session

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    def context(self, incident_uid: str, level: int) -> dict:
        ctx: dict = {
            "incidentUid": incident_uid,
            "level": f"L{level}",
            "catalogueVersion": CATALOGUE_VERSION,
            "mode": self.mode,
        }
        if self.model:
            ctx["model"] = self.model
        if self.effort:
            ctx["reasoningEffort"] = self.effort
        return ctx

    async def propose(self, bundle: dict, *, incident_uid: str, level: int) -> Result:
        request_id = str(uuid.uuid4())
        why = check_redacted(bundle)
        if why:
            return Result(
                False, request_id, "not_redacted", errors=[why]
            )  # nothing is sent
        body = json.dumps(
            {"bundle": bundle, "context": self.context(incident_uid, level)}
        )
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            async with self._http().post(
                self.endpoint + "/v1/propose",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Request-Id": request_id,
                },
                timeout=aiohttp.ClientTimeout(total=self.timeout_s),
            ) as r:
                status = r.status
                raw = await self._read(r)
        except TimeoutError:
            return Result(False, request_id, "timeout", latency_ms=elapsed())
        except (aiohttp.ClientError, OSError):
            return Result(False, request_id, "transport", latency_ms=elapsed())
        if raw is None:
            return Result(
                False, request_id, "unparseable", status=status, latency_ms=elapsed()
            )
        if status == 422:
            return self._rejection(request_id, status, raw, elapsed())
        if status != 200:
            return Result(
                False, request_id, f"http_{status}", status=status, latency_ms=elapsed()
            )
        return self._accept(request_id, status, raw, elapsed())

    @staticmethod
    async def _read(r: aiohttp.ClientResponse) -> str | None:
        """The body as text, or None when it is over the size cap."""
        chunks: list[bytes] = []
        size = 0
        async for chunk in r.content.iter_chunked(65536):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace")

    @staticmethod
    def _rejection(request_id: str, status: int, raw: str, latency_ms: int) -> Result:
        """A 422 keeps what the Reasoner said: its reason, and the model's message if it sent one."""
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return Result(
                False, request_id, "rejected", status=status, latency_ms=latency_ms
            )
        meta = _clean_meta(body.get("metadata"))
        meta["rejection"] = str(body.get("error", ""))[:100]
        model_raw = body.get("raw") if isinstance(body.get("raw"), str) else None
        return Result(
            False,
            request_id,
            "rejected",
            raw=model_raw,
            errors=[str(body.get("detail", ""))[:500]],
            meta=meta,
            status=status,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _accept(request_id: str, status: int, raw: str, latency_ms: int) -> Result:
        try:
            body = json.loads(raw)
        except ValueError:
            return Result(
                False,
                request_id,
                "unparseable",
                raw=raw[:4000],
                status=status,
                latency_ms=latency_ms,
            )
        doc = body.get("proposal") if isinstance(body, dict) else None
        meta = _clean_meta(body.get("metadata")) if isinstance(body, dict) else {}
        errors = proposal.validate(doc)  # the Reasoner is never trusted
        if errors:
            return Result(
                False,
                request_id,
                "schema_invalid",
                doc=doc if isinstance(doc, dict) else None,
                raw=raw[:4000],
                errors=errors,
                meta=meta,
                status=status,
                latency_ms=latency_ms,
            )
        return Result(
            True,
            request_id,
            doc=doc,
            raw=raw[:4000],
            meta=meta,
            status=status,
            latency_ms=latency_ms,
        )
