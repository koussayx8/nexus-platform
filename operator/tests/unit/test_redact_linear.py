"""Redaction time is linear in the length of a string (authority-boundary review, A1), and the
linear path (redact._RUNS) redacts exactly what the patterns themselves do."""

import random
import time

import pytest

from nexus_operator import redact

MB = 1 << 20
# One megabyte of each, on one line. The first is the review's; each of the others is the worst
# shape for one of the four patterns that begin with a run, or a line of very many short runs.
# Plain pattern.sub needs hours on the first six: its time grows with the square of the length
# (8,000 characters of `a.` took 3.8 s). The bound only has to tell seconds from hours.
HOSTILE = {
    "a.": 0,  # no keyword, no "@", no "://": every word boundary restarts three patterns
    "token": 0,  # one run made of password keywords, and no separator after it
    "eyJaaaaa-": 0,  # a JWT start every nine characters, and no "." in the run
    "a-a@b ": 0,  # runs that reach an "@" and have no dotted domain after it
    "x+x.": 0,  # a URL scheme run with no "://"
    "x://": 0,  # a scheme at every boundary, and no credential
    "a ": 0,  # half a million runs of one character
    "j@k.io ": MB // 7,  # and a line that is nothing but matches
}
BOUND_S = 20


@pytest.mark.parametrize("unit", HOSTILE)
def test_a_one_megabyte_hostile_line_is_redacted_in_bounded_time(unit):
    line = unit * (MB // len(unit))
    counts: dict[str, int] = {}
    started = time.perf_counter()
    out = redact.redact_text(line, counts)
    assert time.perf_counter() - started < BOUND_S
    assert sum(counts.values()) == HOSTILE[unit]
    if not counts:
        assert out == line


def by_the_patterns(text: str) -> tuple[str, dict[str, int]]:
    """redact_text with every pattern applied by plain pattern.sub: what the linear path replaces."""
    linear, redact._RUNS = redact._RUNS, {}
    try:
        counts: dict[str, int] = {}
        return redact.redact_text(text, counts), counts
    finally:
        redact._RUNS = linear


def linear(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    return redact.redact_text(text, counts), counts


# Where a match starts inside a run, and what happens around the previous match.
CASES = [
    ("alice@example.com", "[REDACTED:email]"),
    ("..abc@x.yz", "..[REDACTED:email]"),  # the first word boundary of the run, not its start
    ("\u00e9a@x.yz", "\u00e9a@x.yz"),  # no boundary before the "a": no match, as before
    ("a@b@c.de", "a@[REDACTED:email]"),  # the first "@" has no domain; the second run matches
    ("a@b.c%d@e.fg", "[REDACTED:email][REDACTED:email]"),  # a run that starts where a match ended
    ("x.token=abc", "x.token=[REDACTED:password]"),
    ("tokentoken: hunter2", "tokentoken: [REDACTED:password]"),
    ("my-token.name", "my-token.name"),  # a keyword and no separator
    ("pa\u017f\u017fword=hunter2", "pa\u017f\u017fword=[REDACTED:password]"),  # (?i) folds the long s
    ("x-eyJabcde.fghij.klmno", "x-[REDACTED:jwt]"),
    ("eyJaaaaa-eyJbbbbb.ccccc.ddddd", "[REDACTED:jwt]"),  # the leftmost start of the run wins
    ("eyJaaaaa-eyJbbbbb", "eyJaaaaa-eyJbbbbb"),
    ("git+ssh://user:pw@host", "git+ssh://user:[REDACTED:url_credential]@host"),
    ("1x://user:pw@host", "1x://user:pw@host"),  # the scheme must start with a letter at a boundary
]  # fmt: skip


@pytest.mark.parametrize("text,want", CASES)
def test_the_linear_path_starts_a_match_where_the_pattern_does(text, want):
    assert linear(text)[0] == want
    assert linear(text) == by_the_patterns(text)


TOKENS = ["a", "b", "Z", "\u00e9", ".", "-", "_", "@", ":", "/", "://", "=", " ", "\n", "\t", "pwd",
          "token", "Secret", "api_key", "eyJ", "abcde", "b.c", "%", "+", "'", '"', "1", "9", ",", ";",
          "&", "..", "--", "\u017f", "\u212a", "[REDACTED:ip]", "x://u:pw@h", "j@k.io",
          "eyJabcde.fghij.klmno", "passwd: hunter2"]  # fmt: skip


def test_the_linear_path_redacts_exactly_what_the_patterns_do():
    # 20,000 random strings of pieces that start, break and fake every one of the four patterns.
    rng = random.Random(20261113)
    redacted = 0
    for _ in range(20_000):
        text = "".join(rng.choice(TOKENS) for _ in range(rng.randint(1, 12)))
        want = by_the_patterns(text)
        assert linear(text) == want, repr(text)
        redacted += bool(want[1])
    assert redacted > 5_000  # the strings do exercise the patterns


def test_every_pattern_that_begins_with_a_run_has_a_linear_path():
    classes = {cls for cls, pattern, _ in redact._PATTERNS if pattern in redact._RUNS}
    assert classes == {"url_credential", "jwt", "password", "email"}
