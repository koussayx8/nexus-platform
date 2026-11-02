"""Reasoner Client for the harness (spec sections 4, 16): redact, one HTTP call, 30 s timeout,
no retry, proposal/v1 validation. Used by the offline replay (phase B); the operator carries
its own client. The Reasoner is untrusted: its output is data, parsed and schema-checked,
never executed.

Failure of any kind (network, timeout, 5xx, 422, unparseable, schema-invalid) yields a
result with ok=False and a reason; this client never retries and never falls back silently.
The raw output is always kept for the Flight Recorder `proposals` table.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

TIMEOUT_S = 30
FAULT_CLASSES = (
    "gray_failure", "cpu_saturation", "bad_rollout", "traffic_spike", "dependency_failure",
    "image_pull", "cert_expiry", "dns_failure", "disk_pressure", "unknown",
)  # fmt: skip
ACTION_IDS = ("restart_workload", "scale_deployment", "escalate", "no_action")
RECOMMENDATION_KINDS = (
    "git_revert", "config_change", "resource_limit_change", "dependency_investigation", "investigate",
)  # fmt: skip

# GitLeaks-class patterns, ordered; counts per pattern go to bundle.redaction.counts.
PATTERNS = {
    "token": re.compile(
        r"(?i)\b(?:bearer\s+[a-z0-9._~+/=-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}"
        r"|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
    ),
    "key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    "credential": re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*(?!\[REDACTED)\S+"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}  # fmt: skip


def redact(bundle: dict) -> dict:
    """Scrub every string in the bundle, then set redaction.counts and hash.
    Idempotent: redacting a redacted bundle changes nothing."""
    prior = (bundle.get("redaction") or {}).get("counts", {})
    counts = {
        name: int(prior.get(name, 0)) for name in PATTERNS
    }  # re-redaction keeps earlier counts

    def scrub(v):
        if isinstance(v, str):
            for name, rx in PATTERNS.items():
                if name == "credential":
                    v, n = rx.subn(
                        lambda m, name=name: f"{m.group(1)}=[REDACTED:{name}]", v
                    )
                else:
                    v, n = rx.subn(f"[REDACTED:{name}]", v)
                counts[name] += n
            return v
        if isinstance(v, list):
            return [scrub(x) for x in v]
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in v.items() if k not in ("redaction", "hash")}
        return v

    out = scrub(bundle)
    out["redaction"] = {"counts": counts}
    out["hash"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {k: v for k, v in out.items() if k != "hash"}, sort_keys=True
            ).encode()
        ).hexdigest()
    )
    return out


def validate_proposal(p: object) -> list[str]:
    """Schema and catalogue errors for a proposal/v1 object; [] means valid.
    Pure shape checks: bounds and target binding belong to the Safety Gate."""
    if not isinstance(p, dict):
        return ["not an object"]
    errs = []
    if p.get("schema") != "proposal/v1":
        errs.append("schema != proposal/v1")
    h = p.get("hypothesis")
    if not isinstance(h, dict) or h.get("faultClass") not in FAULT_CLASSES:
        errs.append("hypothesis.faultClass not in enum")
    for k in ("evidenceRefs", "runbookRefs"):
        if not isinstance(p.get(k), list) or not all(isinstance(x, str) for x in p[k]):
            errs.append(f"{k} not a list of strings")
    a = p.get("action")
    if not isinstance(a, dict) or a.get("id") not in ACTION_IDS:
        errs.append("action.id not in catalogue")
    elif a["id"] in ("restart_workload", "scale_deployment"):
        t = a.get("target")
        if not isinstance(t, dict) or not all(
            isinstance(t.get(x), str) for x in ("namespace", "kind", "name")
        ):
            errs.append("action.target incomplete")
    if not isinstance(p.get("abstain"), bool):
        errs.append("abstain not boolean")
    c = p.get("confidence")
    if not isinstance(c, (int, float)) or isinstance(c, bool) or not 0 <= c <= 1:
        errs.append("confidence not in [0, 1]")
    if isinstance(a, dict) and a.get("id") == "escalate":
        e = p.get("escalation")
        rec = e.get("recommendation") if isinstance(e, dict) else None
        if not isinstance(rec, dict) or rec.get("kind") not in RECOMMENDATION_KINDS:
            errs.append("escalation.recommendation.kind missing or not in enum")
        if not isinstance(e, dict) or not e.get("reason"):
            errs.append("escalation.reason missing")
    return errs


@dataclass
class Result:
    ok: bool
    reason: str | None = (
        None  # why not ok: transport | timeout | http_<code> | unparseable | schema_invalid
    )
    proposal: dict | None = None
    raw: str | None = None
    errors: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    bundle: dict | None = None  # the redacted bundle that was sent


def propose(
    endpoint: str, bundle: dict, context: dict, timeout: float = TIMEOUT_S, opener=None
) -> Result:
    """POST /v1/propose once. `context`: incident uid, level, catalogue version, mode, model id.
    The bundle is redacted here, whatever the caller passed."""
    sent = redact(bundle)
    body = json.dumps({"bundle": sent, "context": context}).encode()
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/propose",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return Result(False, f"http_{e.code}", bundle=sent)
    except TimeoutError:
        return Result(False, "timeout", bundle=sent)
    except urllib.error.URLError as e:
        return Result(
            False,
            "timeout"
            if isinstance(e.reason, (TimeoutError, socket.timeout))
            else "transport",
            bundle=sent,
        )
    except OSError:
        return Result(False, "transport", bundle=sent)
    try:
        doc = json.loads(raw)
    except ValueError:
        return Result(False, "unparseable", raw=raw, bundle=sent)
    # Response is proposal/v1 plus metadata, either nested or flat.
    prop = doc.get("proposal", doc) if isinstance(doc, dict) else doc
    meta = doc.get("metadata", {}) if isinstance(doc, dict) else {}
    errs = validate_proposal(prop)
    if errs:
        return Result(
            False,
            "schema_invalid",
            prop if isinstance(prop, dict) else None,
            raw,
            errs,
            meta,
            sent,
        )
    return Result(True, None, prop, raw, [], meta, sent)
