"""The Reasoner HTTP service (spec section 16): POST /v1/propose and GET /healthz.

Mode router: `rules` is the embedded Rules Engine R1; `llm` is retrieval, the versioned prompt and
the Model Adapter; `adversarial` (R0, G15) is not built yet and answers 501. Every response body
is data for the operator's Safety Gate, which validates it again.

The request names the mode and the model id the operator's configuration chose. A model id the
service's own configuration does not list is refused: a request cannot pick a host or a model.
"""

from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nexus_operator import proposal, rules

from . import adapter, prompt
from .config import MODES, Config
from .retrieval import Retriever

MAX_BODY = 1_048_576
LEVELS = ("L0", "L1", "L2", "L3")


class Rejected(Exception):
    def __init__(self, status: int, error: str, detail: str = "", **extra):
        super().__init__(error)
        self.status, self.error, self.detail, self.extra = status, error, detail, extra


def check_bundle(bundle) -> None:
    """evidence/v1 that the operator redacted: counts present and hash equal to the canonical hash."""
    if not isinstance(bundle, dict) or bundle.get("schema") != "evidence/v1":
        raise Rejected(422, "invalid_bundle", "not an evidence/v1 bundle")
    if not isinstance((bundle.get("redaction") or {}).get("counts"), dict):
        raise Rejected(422, "invalid_bundle", "redaction.counts missing")
    body = {k: v for k, v in bundle.items() if k != "hash"}
    if bundle.get("hash") != proposal.hash_of(body):
        raise Rejected(422, "invalid_bundle", "hash does not match the bundle")


def resolves(bundle, path: str) -> bool:
    """True when a path such as logs.current[2] or metrics.zscores.latencyP95 exists in the bundle."""
    cur = bundle
    pos = 0
    for part in path.split("."):
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)((?:\[\d+\])*)", part)
        if not m:
            return False
        if not isinstance(cur, dict) or m.group(1) not in cur:
            return False
        cur = cur[m.group(1)]
        for idx in re.findall(r"\[(\d+)\]", m.group(2)):
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return False
            cur = cur[int(idx)]
        pos += 1
    return pos > 0


class Reasoner:
    def __init__(self, cfg: Config, retriever: Retriever, opener=None):
        self.cfg, self.retriever, self.opener = cfg, retriever, opener

    def health(self) -> dict:
        return {
            "status": "ready",
            "corpusHash": self.retriever.corpus_hash,
            "promptVersion": prompt.VERSION,
            "scoreFloor": self.cfg.score_floor,
        }

    def propose(self, req) -> dict:
        if not isinstance(req, dict):
            raise Rejected(422, "invalid_request", "body must be an object")
        bundle, ctx = req.get("bundle"), req.get("context")
        if not isinstance(ctx, dict):
            raise Rejected(422, "invalid_request", "context missing")
        check_bundle(bundle)
        mode = ctx.get("mode")
        if mode not in MODES:
            raise Rejected(422, "invalid_request", f"mode must be one of {MODES}")
        t0 = time.monotonic()
        if mode == "adversarial":
            raise Rejected(501, "mode_not_available", "R0 is built with G15")
        if mode == "rules":
            doc = rules.propose(bundle)
            meta = {"mode": "rules", "model": None}
        else:
            doc, meta = self._llm(bundle, ctx)
        errs = proposal.validate(doc)
        if errs:
            raise Rejected(
                422, "invalid_proposal", "; ".join(errs[:5]), raw=json.dumps(doc)[:4000]
            )
        meta.update(
            {
                "corpusHash": self.retriever.corpus_hash,
                "promptVersion": prompt.VERSION if mode == "llm" else None,
                "incidentUid": ctx.get("incidentUid"),
                "latencyMs": int((time.monotonic() - t0) * 1000),
            }
        )
        return {"proposal": doc, "metadata": meta}

    def _llm(self, bundle: dict, ctx: dict) -> tuple[dict, dict]:
        level = ctx.get("level")
        if level not in LEVELS:
            raise Rejected(422, "invalid_request", f"level must be one of {LEVELS}")
        entry = self.cfg.entry_for_model(ctx.get("model") or "")
        if entry is None:
            raise Rejected(
                422, "unknown_model", "model id is not in the Reasoner configuration"
            )
        effort = ctx.get("reasoningEffort")
        if effort is not None and effort not in ("low", "medium", "high"):
            raise Rejected(
                422, "invalid_request", "reasoningEffort must be low, medium or high"
            )
        chunks = self.retriever.retrieve(bundle)
        system, user = prompt.build(
            bundle, chunks, level, str(ctx.get("catalogueVersion", ""))
        )
        try:
            comp = adapter.complete(self.cfg, entry, system, user, effort, self.opener)
        except adapter.AdapterError as e:
            code = (
                503
                if e.reason in ("no_credentials", "not_configured")
                else 504
                if e.reason == "timeout"
                else 502
            )
            raise Rejected(code, "model_unavailable", e.reason) from None
        try:
            doc = adapter.parse_json(comp.text)
        except ValueError:
            raise Rejected(
                422, "unparseable", "model output is not JSON", raw=comp.text[:4000]
            ) from None
        meta = {
            "mode": "llm",
            "arm": entry.name,
            "provider": entry.provider,
            "model": comp.model,
            "settings": comp.settings,
            "usage": comp.usage,
            "retrievedChunks": [c["id"] for c in chunks],
            "noRunbook": not chunks,
        }
        self._check_refs(doc, bundle, chunks)
        return doc, meta

    @staticmethod
    def _check_refs(doc, bundle, chunks) -> None:
        if not isinstance(doc, dict):
            return  # validate() reports it
        got = {c["id"] for c in chunks}
        bad_rb = [r for r in doc.get("runbookRefs") or [] if r not in got]
        bad_ev = [
            r
            for r in doc.get("evidenceRefs") or []
            if not isinstance(r, str) or not resolves(bundle, r)
        ]
        if bad_rb or bad_ev:
            raise Rejected(
                422, "invalid_refs", f"runbookRefs not retrieved: {bad_rb[:5]}; evidenceRefs not in bundle: {bad_ev[:5]}",
                raw=json.dumps(doc)[:4000],
            )  # fmt: skip


def make_handler(rs: Reasoner):
    class Handler(BaseHTTPRequestHandler):
        server_version = "nexus-reasoner"

        def _send(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, rs.health())
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/v1/propose":
                return self._send(404, {"error": "not_found"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= MAX_BODY:
                    raise Rejected(422, "invalid_request", "body missing or over 1 MiB")
                try:
                    req = json.loads(self.rfile.read(n))
                except ValueError:
                    raise Rejected(422, "invalid_request", "body is not JSON") from None
                self._send(200, rs.propose(req))
            except Rejected as r:
                self._send(r.status, {"error": r.error, "detail": r.detail, **r.extra})
            except Exception:  # noqa: BLE001 - never leak internals; the operator falls back on 5xx
                print("internal error handling /v1/propose", file=sys.stderr)
                self._send(500, {"error": "internal"})

        def log_message(self, fmt, *args):  # request lines only, no bodies
            print(f"{self.address_string()} {fmt % args}", file=sys.stderr)

    return Handler


def serve(rs: Reasoner) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((rs.cfg.host, rs.cfg.port), make_handler(rs))
