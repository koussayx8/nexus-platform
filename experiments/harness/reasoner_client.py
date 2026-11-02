"""Reasoner Client for the harness (spec sections 4, 16): one HTTP call, 30 s timeout, no retry,
proposal/v1 validation. Used by the offline replay (phase B).

It receives a bundle the operator already redacted (Q1) and refuses any other. Redaction,
proposal/v1 validation and the catalogue are the operator's own modules (operator_lib.py).
The Reasoner is untrusted: its output is data, parsed and checked, never executed, and nothing
in it reaches verification criteria (I13, I15).

Failure of any kind (refused bundle, network, timeout, 5xx, 422, unparseable, schema-invalid,
not in the catalogue) yields ok=False and a reason; propose() never retries. propose_with_fallback()
falls back to the embedded Rules Engine R1 once, and reports `blocked` if R1's output is invalid too.
The raw output is always kept for the Flight Recorder `proposals` table.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .operator_lib import catalogue, proposal, rules

TIMEOUT_S = 30


def check_redacted(bundle: dict) -> str | None:
    """None when the bundle is the operator's redacted evidence/v1 bundle, else why not.

    The operator redacts before it stores or sends anything (Q1); this client never scrubs a
    bundle itself, it refuses one the operator did not redact: counts present, hash equal to the
    canonical hash of the bundle without it. (A second Redactor pass is not a usable check: the
    operator's `password` pattern matches its own `password=[REDACTED:password]` marker.)"""
    if not isinstance(bundle, dict) or bundle.get("schema") != "evidence/v1":
        return "not an evidence/v1 bundle"
    if not isinstance((bundle.get("redaction") or {}).get("counts"), dict):
        return "redaction.counts missing"
    body = {k: v for k, v in bundle.items() if k != "hash"}
    if bundle.get("hash") != proposal.hash_of(body):
        return "hash does not match the bundle"
    return None


@dataclass
class Result:
    ok: bool
    reason: str | None = (
        None  # not_redacted | transport | timeout | http_<code> | unparseable | schema_invalid | not_in_catalogue
    )
    proposal: dict | None = None
    raw: str | None = None
    errors: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    fallback_used: bool = False
    blocked: bool = False


def propose(
    endpoint: str, bundle: dict, context: dict, timeout: float = TIMEOUT_S, opener=None
) -> Result:
    """POST /v1/propose once. `context`: incident uid, level, catalogue version, mode, model id."""
    why = check_redacted(bundle)
    if why:
        return Result(False, "not_redacted", errors=[why])  # nothing is sent
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/propose",
        data=json.dumps({"bundle": bundle, "context": context}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return Result(False, f"http_{e.code}")
    except TimeoutError:
        return Result(False, "timeout")
    except urllib.error.URLError as e:
        return Result(
            False, "timeout" if isinstance(e.reason, TimeoutError) else "transport"
        )
    except OSError:
        return Result(False, "transport")
    try:
        doc = json.loads(raw)
    except ValueError:
        return Result(False, "unparseable", raw=raw)
    # Response is proposal/v1 plus metadata, either nested or flat.
    prop = doc.get("proposal", doc) if isinstance(doc, dict) else doc
    meta = doc.get("metadata", {}) if isinstance(doc, dict) else {}
    errs = proposal.validate(prop)
    if errs:
        return Result(
            False,
            "schema_invalid",
            prop if isinstance(prop, dict) else None,
            raw,
            errs,
            meta,
        )
    if prop["action"]["id"] not in catalogue.CATALOGUE:
        return Result(
            False,
            "not_in_catalogue",
            prop,
            raw,
            [f"action {prop['action']['id']!r}"],
            meta,
        )
    return Result(True, None, prop, raw, [], meta)


def propose_with_fallback(
    endpoint: str, bundle: dict, context: dict, timeout: float = TIMEOUT_S, opener=None
) -> Result:
    """propose(), then R1 once if it failed; `blocked` when R1's own output is invalid."""
    r = propose(endpoint, bundle, context, timeout, opener)
    if r.ok or r.reason == "not_redacted":
        return r
    doc = rules.propose(bundle)
    errs = proposal.validate(doc)
    if errs or doc["action"]["id"] not in catalogue.CATALOGUE:
        return Result(
            False, r.reason, doc, r.raw, errs, r.meta, fallback_used=True, blocked=True
        )
    return Result(
        True,
        None,
        doc,
        r.raw,
        [],
        {"mode": "rules", "primary_failure": r.reason},
        fallback_used=True,
    )
