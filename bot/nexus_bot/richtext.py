"""Rich text without a parse mode (ADR-040 decision 11, as amended on 2026-11-22).

A message is plain text plus the `entities` of the Bot API (MessageEntity) that the bot computes
itself. No `parse_mode` is ever sent, so Telegram parses nothing in the text: a `<b>`, a `*` or a
`[link](x)` written by the model is shown as those characters.

- `bold`, `italic`, `code` and `pre` take the bot's own labels: string literals in this package. `pre`
  also holds a table of cluster values (names, counts, phases) that its caller sanitized cell by cell.
- `link` is a `text_link` entity over the bot's own text or a value that fully matches a strict
  pattern (`prompt/v3`, a runbook chunk id, 7 hex of a commit). Its URL is built only by the bot,
  from its configuration (`views.Links`); no URL is ever taken from model or cluster text.
- `value` and `untrusted` take anything else. Both go through nexusctl's `sanitize` and never get
  an entity of their own. Untrusted text (model output, evidence, logs, the Incident's proposal,
  reasoning and escalation, error text) is written only inside `quote()`, so the one entity that
  may cover it is the blockquote that contains it. `build` refuses a formatting entity that
  overlaps untrusted text.
- URLs in untrusted text are defanged (`defang`): Telegram auto-links URLs even when `entities` is
  given, so a URL from the model or a log must not be tappable. C8's own pull request link is the
  one exception (its caller passes `defang_urls=False`).
- `ident` is a value that may be shown as `code` (tap to copy) only when it matches a strict
  pattern: an Incident name or a `sha256:` hash. Anything else is a plain value.

Offsets and lengths are UTF-16 code units, as the Bot API counts them (an emoji outside the BMP is
2). The text is held as Python code points, so a cut never splits a surrogate pair. Over 4,096
units, `build` keeps the start, clips every entity to it and appends the truncation line.
"""

import re
from bisect import bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from nexusctl.render.injection import looks_like_instruction
from nexusctl.render.sanitize import sanitize

MAX_TEXT = 4096  # Bot API: sendMessage text, after entity parsing
FORMATS = frozenset({"bold", "italic", "code", "pre", "text_link"})
QUOTES = frozenset({"blockquote", "expandable_blockquote"})
TYPES = FORMATS | QUOTES
FLAG = "⚠️ "
NOTE = "(truncated: {shown} of {total} characters; the full text is in the Flight Recorder)"
_LINK_URL = re.compile(r"^https://github\.com/[A-Za-z0-9._/#-]+$")
IDENT = (
    re.compile(r"inc-[a-z0-9-]{1,59}"),       # an Incident name (the /incident grammar)
    re.compile(r"sha256:[0-9a-f]{64}"),       # a proposal, bundle or index hash
)


# a URL with an http(s) scheme (any case) and its host part, or a bare "www." host
_URL = re.compile(r"(?i)\b(https?)://([^\s/?#\\]*)|(?<![\w.\[])www\.([^\s/?#\\]*)")


def defang(text: str) -> str:
    """http(s) -> hxxp(s), the dots of the host part -> [.], a bare "www." -> "www[.]" (with the
    dots of the rest of its host). A hash or a file name such as values.yaml is unchanged."""
    def one(m: re.Match[str]) -> str:
        if m[1] is not None:
            return f"hxxp{m[1][4:].lower()}://" + m[2].replace(".", "[.]")
        return "www[.]" + m[3].replace(".", "[.]")

    return _URL.sub(one, text)


def units(text: str) -> int:
    """Length in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def _prefix_units(text: str) -> list[int]:
    cum = [0]
    for ch in text:
        cum.append(cum[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return cum


def cut_point(text: str, limit: int = MAX_TEXT) -> tuple[int, str]:
    """(code points kept, truncation line) for a text over `limit` UTF-16 units: the longest start
    that fits with the line after it. A cut between code points never splits a surrogate pair."""
    cum, total = _prefix_units(text), len(text)
    shown = total
    while True:
        note = "\n" + NOTE.format(shown=shown, total=total)
        best = bisect_right(cum, limit - units(note)) - 1  # the longest start that fits
        if best >= shown:
            return shown, note
        shown = max(best, 0)


@dataclass(frozen=True)
class Message:
    text: str
    entities: list[dict[str, Any]]
    untrusted: list[tuple[int, int]]  # (offset, length) in UTF-16 units; for tests, never sent


class RichText:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._n = 0  # code points so far
        self._spans: list[tuple[str, int, int, str | None]] = []  # (type, start, end, url) in code points
        self._untrusted: list[tuple[int, int]] = []
        self._quote: int | None = None
        self.flagged = 0  # instruction-like untrusted values written so far

    def _put(self, text: str, kind: str | None = None, url: str | None = None) -> "RichText":
        start = self._n
        self._parts.append(text)
        self._n += len(text)
        if kind is not None and text:
            self._spans.append((kind, start, self._n, url))
        return self

    # -- the bot's own text -------------------------------------------------------------------------
    def text(self, s: str) -> "RichText":
        return self._put(s)

    def bold(self, s: str) -> "RichText":
        return self._put(s, "bold")

    def italic(self, s: str) -> "RichText":
        return self._put(s, "italic")

    def code(self, s: str) -> "RichText":
        return self._put(s, "code")

    def pre(self, s: str) -> "RichText":
        return self._put(s, "pre")

    def link(self, s: str, url: str) -> "RichText":
        """A trusted link: `s` is the bot's text (sanitized), `url` one the bot built from its config."""
        if not _LINK_URL.fullmatch(url):
            raise ValueError("a link URL is built by the bot, on the configured repository")
        return self._put(sanitize(s, 120), "text_link", url)

    def nl(self) -> "RichText":
        return self._put("\n")

    def title(self, s: str) -> "RichText":
        return self.bold(s).nl()

    def fact(self, label: str, value: object, n: int = 120) -> "RichText":
        """One fact on its own line: a bold label and a plain, sanitized value."""
        return self.bold(f"{label}:").text(" ").value(value, n).nl()

    # -- everything else ----------------------------------------------------------------------------
    def value(self, v: object, n: int = 120) -> "RichText":
        """A value read from the cluster (a name, an enum, a count): sanitized, never formatted."""
        return self._put(sanitize(v, n))

    def ident(self, v: object, n: int = 120) -> "RichText":
        text = sanitize(v, n)
        return self._put(text, "code" if any(p.fullmatch(text) for p in IDENT) else None)

    def untrusted(self, v: object, n: int, *, defang_urls: bool = True) -> "RichText":
        """Model, evidence or log text: sanitized, URLs defanged, flagged when instruction-like,
        never formatted, and only inside a quote."""
        if self._quote is None:
            raise ValueError("untrusted text is written only inside a quote")
        text = sanitize(v, n)
        if defang_urls:
            text = defang(text)
        if looks_like_instruction(text):
            self.flagged += 1
            self._put(FLAG)
        start = self._n
        self._put(text)
        if text:
            self._untrusted.append((start, self._n))
        return self

    @contextmanager
    def quote(self, expandable: bool = False) -> Iterator["RichText"]:
        if self._quote is not None:
            raise ValueError("a quote cannot hold another quote")
        self._quote = self._n
        try:
            yield self
        finally:
            start, self._quote = self._quote, None
            kind = "expandable_blockquote" if expandable else "blockquote"
            self._spans.append((kind, start, self._n, None))

    # -- output -------------------------------------------------------------------------------------
    def build(self, limit: int = MAX_TEXT) -> Message:
        raw = "".join(self._parts)
        lead = len(raw) - len(raw.lstrip())  # Telegram trims the text; offsets follow the trim
        text = raw.strip()
        spans = [(k, s - lead, e - lead, u) for k, s, e, u in self._spans]
        bad = [(k, s, e) for k, s, e, _u in spans if k in FORMATS
               for us, ue in self._untrusted if s < ue - lead and us - lead < e]
        if bad:
            raise ValueError(f"a {bad[0][0]} entity overlaps untrusted text")
        untrusted = [(s - lead, e - lead) for s, e in self._untrusted]

        end, note = len(text), ""
        if units(text) > limit:
            end, note = cut_point(text, limit)
            text = text[:end]
        clipped = []
        for k, s, e, u in spans:
            s, e = max(s, 0), min(e, end)
            while e > s and text[e - 1] == "\n":  # an entity never ends on a line break
                e -= 1
            while s < e and text[s] == "\n":
                s += 1
            if e > s:
                clipped.append((k, s, e, u))
        untrusted = [(max(s, 0), min(e, end)) for s, e in untrusted if min(e, end) > max(s, 0)]
        if note:
            clipped.append(("italic", end + 1, end + len(note), None))
            text += note

        cum = _prefix_units(text)
        clipped.sort(key=lambda t: (t[1], -(t[2] - t[1])))
        entities = [{"type": k, "offset": cum[s], "length": cum[e] - cum[s]} | ({"url": u} if u else {})
                    for k, s, e, u in clipped]
        return Message(text, entities, [(cum[s], cum[e] - cum[s]) for s, e in untrusted])
