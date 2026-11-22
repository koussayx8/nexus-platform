"""Checks shared by the message tests: what an entity covers, and the decision-11 invariant."""

from typing import Any

from nexus_bot.richtext import FORMATS, QUOTES, TYPES, Message, units


def covered(text: str, e: dict[str, Any]) -> str:
    """The text an entity covers, read by its UTF-16 offset and length, as Telegram reads it."""
    raw = text.encode("utf-16-le")
    return raw[2 * e["offset"]: 2 * (e["offset"] + e["length"])].decode("utf-16-le")


def quotes_hold_untrusted(msg: Message) -> None:
    """Every entity is a known type inside the text; no formatting entity touches untrusted text;
    every untrusted span lies inside one blockquote."""
    n = units(msg.text)
    for e in msg.entities:
        assert e["type"] in TYPES and e["length"] > 0 and 0 <= e["offset"] and e["offset"] + e["length"] <= n
        covered(msg.text, e)  # decodes: the offsets fall on code point boundaries
    for off, length in msg.untrusted:
        for e in msg.entities:
            if e["type"] in FORMATS:
                assert e["offset"] + e["length"] <= off or off + length <= e["offset"], e
        assert any(e["type"] in QUOTES and e["offset"] <= off and off + length <= e["offset"] + e["length"]
                   for e in msg.entities), (off, length)
