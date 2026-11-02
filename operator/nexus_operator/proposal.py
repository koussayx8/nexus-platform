"""Schema validation for proposal/v1 (spec §16) — Safety Gate stage 1.

The Reasoner is untrusted: whatever it returns is data until this check passes. The check is
strict (unknown keys fail) and hand-written, so the operator image carries no schema library.
It validates shape only; the action, bounds and target are stages 2 and 3.
"""

import hashlib
import json

from .catalogue import FAULT_CLASSES, RECOMMENDATION_KINDS

_KEYS = {
    "schema",
    "hypothesis",
    "evidenceRefs",
    "runbookRefs",
    "action",
    "expectedEffect",
    "confidence",
    "escalation",
    "abstain",
}
_TARGET_KEYS = {"namespace", "kind", "name"}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _strings(v) -> bool:
    return isinstance(v, list) and all(isinstance(s, str) for s in v)


def validate(doc) -> list[str]:
    """Every violation of proposal/v1 found in doc; empty means valid."""
    if not isinstance(doc, dict):
        return ["proposal is not an object"]
    errors: list[str] = []
    if doc.get("schema") != "proposal/v1":
        errors.append("schema must be proposal/v1")
    for extra in sorted(set(doc) - _KEYS):
        errors.append(f"unknown field {extra}")
    hyp = doc.get("hypothesis")
    if not isinstance(hyp, dict):
        errors.append("hypothesis must be an object")
    else:
        if hyp.get("faultClass") not in FAULT_CLASSES:
            errors.append("hypothesis.faultClass is not in the enum")
        if not isinstance(hyp.get("summary"), str):
            errors.append("hypothesis.summary must be a string")
    for key in ("evidenceRefs", "runbookRefs"):
        if not _strings(doc.get(key)):
            errors.append(f"{key} must be a list of strings")
    action = doc.get("action")
    if not isinstance(action, dict):
        errors.append("action must be an object")
    else:
        if not isinstance(action.get("id"), str):
            errors.append("action.id must be a string")
        target = action.get("target")
        if not (
            isinstance(target, dict)
            and set(target) == _TARGET_KEYS
            and all(isinstance(v, str) for v in target.values())
        ):
            errors.append("action.target must be {namespace, kind, name} strings")
        params = action.get("parameters")
        if not isinstance(params, dict):
            errors.append("action.parameters must be an object")
        elif set(params) - {"replicas"}:
            errors.append("action.parameters may only hold replicas")
        elif "replicas" in params and not _is_int(params["replicas"]):
            errors.append("action.parameters.replicas must be an integer")
    if not isinstance(doc.get("expectedEffect"), str):
        errors.append("expectedEffect must be a string")
    conf = doc.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, int | float):
        errors.append("confidence must be a number")
    if not isinstance(doc.get("abstain"), bool):
        errors.append("abstain must be a boolean")
    esc = doc.get("escalation")
    if isinstance(action, dict) and action.get("id") == "escalate":
        rec = esc.get("recommendation") if isinstance(esc, dict) else None
        if not (
            isinstance(esc, dict)
            and isinstance(esc.get("reason"), str)
            and isinstance(rec, dict)
            and rec.get("kind") in RECOMMENDATION_KINDS
            and isinstance(rec.get("detail", ""), str)
        ):
            errors.append(
                "escalate needs escalation {reason, recommendation{kind, detail}}"
            )
    elif esc is not None and not isinstance(esc, dict):
        errors.append("escalation must be an object or null")
    return errors


def hash_of(doc: dict) -> str:
    """sha256 of the canonical JSON form; the identity an approval pins (§8)."""
    blob = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()
