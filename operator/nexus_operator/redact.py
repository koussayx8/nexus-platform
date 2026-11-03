"""The Evidence Redactor (spec §4, §16; Q1): scrub secrets and personal data from the bundle.

Runs in the operator on the whole bundle BEFORE it is hashed, stored in the Flight Recorder,
handed to the Rules Engine or sent anywhere: nothing downstream ever holds an unredacted value.
Every string value is scanned, at any depth; keys are schema names and are left alone. A match is
replaced by `[REDACTED:<class>]` and counted per class, and the counts travel with the bundle
(`redaction.counts`), so a reader can see that something was removed and what kind.

The patterns are GitLeaks-class, deliberately broad: they redact too much rather than too little.
Image digests and git SHAs are not secrets and are kept.
"""

import re
from typing import Any

# (class, pattern, group to replace or None for the whole match), in order: a longer, more specific
# pattern first so its pieces are not counted as something else.
_PATTERNS: list[tuple[str, re.Pattern, int | None]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        None,
    ),
    (
        "url_credential",
        re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:([^\s/@]+)@"),
        1,
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
        None,
    ),
    ("bearer", re.compile(r"(?i)\b(?:bearer|basic)\s+([A-Za-z0-9._~+/=-]{8,})"), 1),
    ("token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), None),
    ("token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), None),
    ("token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), None),
    ("token", re.compile(r"\b(?:sk|gsk|pk|rk)[-_][A-Za-z0-9_-]{16,}\b"), None),
    ("token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), None),
    ("key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), None),
    (
        "password",
        re.compile(
            r"(?i)\b[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
            r"|private[_-]?key|credentials?|authorization)[\w.-]*\s*[:=]\s*[\"']?(?!\[REDACTED:)([^\s\"',;&]{3,})"
        ),
        1,
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"),
        None,
    ),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), None),
    (
        "ip",
        re.compile(
            r"(?<![\w:])(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
            r"|(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{0,4}::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4})"
            r"(?![\w:])"
        ),
        None,
    ),
]
_VALID_IPV4 = re.compile(
    r"(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)"
)
CLASSES = (
    "private_key",
    "url_credential",
    "jwt",
    "bearer",
    "token",
    "key",
    "password",
    "email",
    "ip",
)
MARK = "[REDACTED:{}]"
# Redaction is idempotent: no pattern may see a marker, so a marker is never matched, counted or
# nested. Each pass splits the text on markers and rewrites only the text between them.
_MARKER = re.compile(r"\[REDACTED:[a-z_]+\]")


def redact_text(text: str, counts: dict[str, int]) -> str:
    for cls, pattern, group in _PATTERNS:

        def sub(m: re.Match, cls=cls, group=group) -> str:
            if (
                cls == "ip"
                and "." in m.group(0)
                and not _VALID_IPV4.fullmatch(m.group(0))
            ):
                return m.group(0)  # a version number, not an address
            counts[cls] = counts.get(cls, 0) + 1
            if group is None:
                return MARK.format(cls)
            whole, lo, hi = (
                m.group(0),
                m.start(group) - m.start(),
                m.end(group) - m.start(),
            )
            return whole[:lo] + MARK.format(cls) + whole[hi:]

        parts = _MARKER.split(text)
        markers = _MARKER.findall(text)
        parts = [pattern.sub(sub, part) for part in parts]
        text = "".join(
            p + (markers[i] if i < len(markers) else "") for i, p in enumerate(parts)
        )
    return text


def redact(value: Any, counts: dict[str, int]) -> Any:
    """A copy of `value` with every string scrubbed; counts is updated in place."""
    if isinstance(value, str):
        return redact_text(value, counts)
    if isinstance(value, dict):
        return {k: redact(v, counts) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v, counts) for v in value]
    return value


def redact_bundle(bundle: dict) -> tuple[dict, dict[str, int]]:
    """(the redacted bundle, counts for every class including zeros)."""
    counts: dict[str, int] = {}
    out = redact(bundle, counts)
    return out, {cls: counts.get(cls, 0) for cls in CLASSES}


def unredacted(bundle: dict) -> list[str]:
    """Classes still found in a bundle that claims to be redacted; empty means it is.

    A second pass over every string. It is idempotent on the operator's own output (the marker
    `[REDACTED:<class>]` is not itself a match), so any hit is something redaction did not
    remove. The Reasoner Client refuses to send a bundle with a hit (Q1, defence in depth).
    """
    counts: dict[str, int] = {}
    body = {k: v for k, v in bundle.items() if k not in ("hash", "redaction")}
    redact(body, counts)
    return sorted(cls for cls, n in counts.items() if n)
