"""Terminal-injection defence (spec §16A "Security of the client").

Every untrusted string (evidence, model output, alert labels) goes through `sanitize` before it
is printed: ANSI/VT escape sequences are removed whole, remaining control characters, Unicode
bidirectional overrides and zero-width characters are dropped, line breaks become spaces, and
the result is truncated. A crafted log line cannot redraw the screen, move the cursor, or
impersonate a trusted field.
"""

import re

# CSI (ESC [ ... final), OSC/DCS/PM/APC strings (ESC ] ... BEL|ST), and two-character escapes.
_ANSI = re.compile(
    r"""
    \x1b\[[0-?]*[ -/]*[@-~]                        # CSI
    | \x1b[\]PX^_].*?(?:\x07|\x1b\\)               # OSC, DCS, SOS, PM, APC up to BEL or ST
    | \x1b[@-Z\\-_]                                # two-character escape
    | \x9b[0-?]*[ -/]*[@-~]                        # 8-bit CSI
    """,
    re.VERBOSE | re.DOTALL,
)
_LINE_BREAKS = re.compile(r"[\r\n\t\v\f  \x85]+")
_DROP = re.compile(
    "[\x00-\x1f\x7f-\x9f"            # C0 and C1 controls (line breaks were already turned into spaces)
    "​-‏"                  # zero-width space/joiners, LRM, RLM
    "‪-‮⁦-⁩"     # bidirectional embeddings, overrides, isolates
    "⁠﻿­]"            # word joiner, BOM, soft hyphen
)
ELLIPSIS = "…"


def sanitize(value: object, max_chars: int = 400) -> str:
    """A printable, single-line, length-capped rendering of an untrusted value."""
    text = "" if value is None else str(value)
    text = _ANSI.sub("", text)
    text = _LINE_BREAKS.sub(" ", text)
    text = _DROP.sub("", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + ELLIPSIS
    return text
