"""The Reasoner Client (spec §4, §6 transitions 4-6, §17): one HTTP call to the untrusted Reasoner.

  POST {reasonerEndpoint}/v1/propose   {"bundle": <redacted evidence/v1>, "context": {...}}
  200 -> {"proposal": <proposal/v1>, "metadata": {...}}
  422 -> {"error", "detail", "raw", "metadata"}      (the Reasoner refused or its model misbehaved)

Rules this module keeps:
* Only the operator's redacted bundle leaves. Before anything is sent the bundle is checked to be
  redacted (counts present, hash equal to the canonical hash, and a second pass over every string
  finds nothing); otherwise nothing is sent and the result is `not_redacted` (Q1).
* One attempt from the operator. Its timeout is reasonerTimeoutSeconds + 5 s (30 + 5 by default,
  at most 180 + 5): the Reasoner's own budget, which holds its one provider retry (ADR-035
  addendum 2026-11-20), plus its work around the model call. The caller falls back to the
  embedded Rules Engine once.
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
import re
import time
import uuid
from typing import Any

import aiohttp

from . import proposal
from .redact import unredacted

TIMEOUT_S = 30
MAX_RESPONSE_BYTES = 1_048_576
REASONING_MAX_CHARS = (
    16000  # proposals.reasoning; status.reasoner shows REASONING_EXCERPT_CHARS
)
REASONING_EXCERPT_CHARS = 300
CATALOGUE_VERSION = "1"
# evidence/v1.1 (ADR-039) adds metrics.zscores.inflight; v1 bundles stay valid input.
EVIDENCE_SCHEMAS = ("evidence/v1", "evidence/v1.1", "evidence/v1.2")  # v1.2: ADR-044
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
        return "not an evidence/v1, v1.1 or v1.2 bundle"
    if not isinstance((bundle.get("redaction") or {}).get("counts"), dict):
        return "redaction.counts missing"
    body = {k: v for k, v in bundle.items() if k != "hash"}
    if bundle.get("hash") != proposal.hash_of(body):
        return "hash does not match the bundle"
    left = unredacted(bundle)
    if left:
        return f"unredacted content found: {', '.join(left)}"
    return None


_UNSAFE = re.compile(r"[^A-Za-z0-9_.:/ -]")


def _error_detail(raw: str | None) -> list[str]:
    """`error` and `detail` of a non-200 answer as one short line, e.g. "model_unavailable: http_403"
    (a 502 whose provider call was refused). The Reasoner is untrusted: only a JSON object is
    read, each part keeps a small character set and 100 characters at most. [] when unreadable."""
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        return []
    if not isinstance(body, dict):
        return []
    parts = [
        _UNSAFE.sub("?", v)[:100]
        for v in (body.get("error"), body.get("detail"))
        if isinstance(v, str) and v
    ]
    return [": ".join(parts)] if parts else []


_USAGE_KEYS = (  # ours <- the provider's (groq first, then ollama)
    ("promptTokens", ("prompt_tokens", "prompt_eval_count")),
    ("completionTokens", ("completion_tokens", "eval_count")),
    ("totalTokens", ("total_tokens",)),
)


def _usage(raw: Any) -> dict:
    """The token counts of one call from the Reasoner's `metadata.usage` (the provider's own
    `usage`, passed through): non-negative integers only, anything else dropped. The total is the
    sum when the provider gives none (ollama). {} when nothing usable."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for ours, theirs in _USAGE_KEYS:
        for key in theirs:
            v = raw.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10**9:
                out[ours] = v
                break
    if "totalTokens" not in out and {"promptTokens", "completionTokens"} <= set(out):
        out["totalTokens"] = out["promptTokens"] + out["completionTokens"]
    return out


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
    for key in ("retrievedChunks", "expandedChunks"):  # expandedChunks: ADR-043
        chunks = meta.get(key)
        if isinstance(chunks, list):
            out[key] = [c[:100] for c in chunks if isinstance(c, str)][:20]
    usage = _usage(meta.get("usage"))
    if usage:
        out["usage"] = usage
    out.update(_excerpt_meta(meta))
    # the model's thinking trace: untrusted text, kept for the record only (ADR-035 addendum C)
    text = meta.get("reasoning")
    if isinstance(text, str) and text:
        out["reasoning"] = text[:REASONING_MAX_CHARS]
        n = _count(meta.get("reasoningLength"))
        out["reasoningLength"] = n if n is not None else len(text)
    return out


def _error_excerpt(raw: str) -> dict:
    """A 502/503/504 body's excerpt: the prompt was built, and for a 502 it was sent."""
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    meta = body.get("metadata") if isinstance(body, dict) else None
    return _excerpt_meta(meta) if isinstance(meta, dict) else {}


EXCERPT_KEYS = (
    "modelConfig",  # the Reasoner config that served the call (r2-primary, r3-primary, ...); also on a failure
    "logSelection",
    "promptTrimLevel",
    "promptTokensEstimate",
    "requestTokensEstimate",  # the 422 prompt_too_large path: the request never sent
)
_SELECTION_COUNTS = ("lines", "kept_error_lines", "kept_recent_lines", "dropped")


def _count(v: Any) -> int | None:
    return (
        v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10**7 else None
    )


def _excerpt_meta(meta: dict) -> dict:
    """What prompt/v2 put in the prompt (ADR-042 decision 6): the trim level, the prompt's token
    estimate and, per pod, the line counts kept and dropped. Counts are non-negative integers, a pod
    name a short string or null, at most 10 pods; anything else is dropped. {} when none is given."""
    out: dict = {}
    name = meta.get("modelConfig")
    if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        out["modelConfig"] = name
    for key in ("promptTrimLevel", "promptTokensEstimate", "requestTokensEstimate"):
        v = _count(meta.get(key))
        if v is not None:
            out[key] = v
    sel = meta.get("logSelection")
    if isinstance(sel, dict):
        clean: dict = {}
        pods = sel.get("pods")
        if isinstance(pods, list):
            rows = []
            for p in pods[:10]:
                if not isinstance(p, dict):
                    continue
                name = p.get("pod")
                row: dict = {"pod": name[:100] if isinstance(name, str) else None}
                for k in _SELECTION_COUNTS:
                    c = _count(p.get(k))
                    if c is not None:
                        row[k] = c
                rows.append(row)
            clean["pods"] = rows
        for k in ("eventsShown", "eventsTotal"):
            c = _count(sel.get(k))
            if c is not None:
                clean[k] = c
        if clean:
            out["logSelection"] = clean
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
        # Any other status: the reason stays http_<n>; the Reasoner's own words go in errors.
        if status != 200:
            return Result(
                False,
                request_id,
                f"http_{status}",
                errors=_error_detail(raw),
                meta=_error_excerpt(raw),
                status=status,
                latency_ms=elapsed(),
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
