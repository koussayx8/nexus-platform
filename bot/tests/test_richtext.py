"""The entity builder (ADR-040 decision 11, as amended): UTF-16 offsets, untrusted text literal and
unformatted, truncation that keeps every entity inside the text."""

import pytest

from nexus_bot.richtext import MAX_TEXT, RichText, units
from tests.helpers import covered, quotes_hold_untrusted

HOSTILE = ["<b>bold</b>", "*star*", "`tick`", "[link](x)", "_it_", "~~s~~", "||spoiler||",
           "a\x00b\x07c\x1b[2Jd‮e​f\nline"]


def test_offsets_count_emoji_and_non_bmp_as_two_units() -> None:
    rt = RichText().bold("🟠 Approval needed").nl().text("𝔘 ").bold("Hash:").text(" ⚠️ ").italic("é😀x")
    msg = rt.build()
    assert [(e["type"], covered(msg.text, e)) for e in msg.entities] == [
        ("bold", "🟠 Approval needed"), ("bold", "Hash:"), ("italic", "é😀x")]
    first, second, third = msg.entities
    assert first == {"type": "bold", "offset": 0, "length": 18}  # 🟠 is 2 units
    assert second["offset"] == 19 + 3  # 𝔘 (2) and a space
    assert third["offset"] == 22 + 5 + 4 and third["length"] == 4  # " ⚠️ " is 4 units; 😀 is 2


@pytest.mark.parametrize("hostile", HOSTILE)
def test_untrusted_text_stays_literal_and_gets_no_entity(hostile: str) -> None:
    rt = RichText().bold("Title").nl()
    with rt.quote(expandable=True):
        rt.bold("Hypothesis:").text(" ").untrusted(hostile, 300)
    msg = rt.build()
    quotes_hold_untrusted(msg)
    [(off, length)] = msg.untrusted
    shown = covered(msg.text, {"offset": off, "length": length})
    if "\x00" in hostile:
        assert shown == "abcdef line"  # controls, escapes, bidi and zero-width removed; no markup made
    else:
        assert shown == hostile  # literal: no parse_mode, so Telegram parses none of it
    kinds = [e["type"] for e in msg.entities]
    assert kinds == ["bold", "expandable_blockquote", "bold"]


def test_untrusted_text_outside_a_quote_is_refused() -> None:
    with pytest.raises(ValueError):
        RichText().untrusted("model text", 100)


def test_quotes_do_not_nest() -> None:
    rt = RichText()
    with pytest.raises(ValueError), rt.quote(), rt.quote():
        pass


def test_a_formatting_entity_over_untrusted_text_is_refused() -> None:
    rt = RichText()
    with rt.quote():
        rt.untrusted("model", 10)
    rt._spans.append(("bold", 0, 3))  # what a wrong call site would produce
    with pytest.raises(ValueError):
        rt.build()


def test_values_and_idents() -> None:
    msg = (RichText().value("*not bold*").text(" ").ident("inc-0123abcd-1").text(" ")
           .ident("sha256:" + "a" * 64).text(" ").ident("inc-<b>").build())
    assert [covered(msg.text, e) for e in msg.entities] == ["inc-0123abcd-1", "sha256:" + "a" * 64]
    assert msg.text.startswith("*not bold* ") and msg.text.endswith(" inc-<b>")


def test_no_entity_ends_on_or_starts_with_a_line_break_and_the_text_is_trimmed() -> None:
    rt = RichText().text("  ").bold("A\n")
    with rt.quote():
        rt.nl().untrusted("x", 5).nl()
    rt.nl().nl()
    msg = rt.build()
    assert msg.text == "A\n\nx"
    assert [(e["type"], covered(msg.text, e)) for e in msg.entities] == [("bold", "A"), ("blockquote", "x")]


def build_long(fill: str) -> RichText:
    rt = RichText().bold("🟠 Approval needed").nl()
    for i in range(400):
        rt.bold(f"Fact {i}:").text(" ").value(fill * 3).nl()
    with rt.quote(expandable=True):
        rt.bold("Model text: untrusted, never a decision").nl()
        rt.untrusted(fill * 2000, 4000)
    return rt


@pytest.mark.parametrize("fill", ["x", "😀", "a😀"])
def test_truncation_keeps_every_entity_inside_the_text(fill: str) -> None:
    msg = build_long(fill).build()
    assert units(msg.text) <= MAX_TEXT
    note = msg.text.split("\n")[-1]
    assert note.startswith("(truncated: ") and note.endswith("the full text is in the Flight Recorder)")
    quotes_hold_untrusted(msg)  # every entity in range and decodable: no split surrogate pair
    assert msg.entities[-1]["type"] == "italic" and covered(msg.text, msg.entities[-1]) == note
    assert not any("\ud800" <= ch <= "\udfff" for ch in msg.text)


def test_truncation_inside_a_surrogate_heavy_quote() -> None:
    rt = RichText().bold("T").nl()
    with rt.quote(expandable=True):
        rt.untrusted("😀" * 5000, 6000)
    msg = rt.build()
    assert units(msg.text) <= MAX_TEXT
    q = [e for e in msg.entities if e["type"] == "expandable_blockquote"][0]
    assert set(covered(msg.text, q)) == {"😀"} and units(msg.text) >= MAX_TEXT - 1  # as much as fits
    quotes_hold_untrusted(msg)


def test_a_text_that_fits_is_untouched() -> None:
    msg = RichText().bold("a").text("b" * (MAX_TEXT - 1)).build()
    assert len(msg.text) == MAX_TEXT and "truncated" not in msg.text
