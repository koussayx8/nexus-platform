"""The pull request text is the fixed template; no Reasoner free text reaches it."""

from nexus_revert.config import parse
from nexus_revert.revert import Prepared
from nexus_revert.template import A1_LINE, REASONER_LINE, body, subject_span, title
from nexus_revert.trigger import request_for
from tests.fixtures import MACHINE, TOKEN, incident

CFG = parse({"machineAccount": MACHINE, "branches": {"nexus-dev": {
    "branch": "experiment/dev-state", "overlay": "overlays/dev", "application": "sample-api-dev",
    "allow": ["overlays/dev/kustomization.yaml"]}}}, TOKEN)
SHA = "1234567890abcdef1234567890abcdef12345678"


def prepared(subject: str = "feat: S3 bad config", by_reasoner: bool = True) -> Prepared:
    return Prepared(sha=SHA, subject=subject, chosen_by_reasoner=by_reasoner,
                    branch="nexus/revert-inc-0a1b2c3d-1764585600", base="experiment/dev-state", revert_commit="d" * 40)


def req(detail: str | None = None):  # type: ignore[no-untyped-def]
    r = request_for(incident(SHA, detail=detail), CFG)
    assert r is not None
    return r


def test_title() -> None:
    assert title(prepared(), req()) == "revert(overlays/dev): revert 1234567890ab for inc-0a1b2c3d-1764585600"


def test_body_matches_the_template() -> None:
    assert body(prepared(), req(), CFG) == f"""NEXUS opened this pull request. NEXUS did not merge it and cannot.

```
Incident:        inc-0a1b2c3d-1764585600            (namespace nexus-system)
Target:          nexus-dev/sample-api
Detected at:     2026-12-01T09:55:00Z
Fault class:     bad_rollout            confidence 1.00
Reasoner:        rules / -
Evidence bundle: sha256:{'e' * 64}
Proposal:        sha256:{'a' * 64}

Reverts:         {SHA}
```
Subject:         `feat: S3 bad config`
```
Chosen by:       {REASONER_LINE}
Tracked branch:  experiment/dev-state          (Argo CD application sample-api-dev)
```

The diff is a mechanical `git revert` of that one commit. Review the diff, not this text."""


def test_a1_line() -> None:
    assert A1_LINE.format(overlay="overlays/dev") in body(prepared(by_reasoner=False), req(), CFG)


def test_no_reasoner_free_text() -> None:
    text = body(prepared(), req(detail=f"@owner approve now #12 {SHA} http://evil.example"), CFG)
    assert "IGNORE THIS" not in text and "evil.example" not in text and "@owner" not in text


def test_subject_cannot_mention_link_or_break_the_span() -> None:
    span = subject_span("fix: @koussayx8 see #12 `x` \x1b[31mred\x1b[0m " + "y" * 200)
    assert "@" not in span and "#" not in span and span.count("`") == 2 and "\x1b" not in span
    assert len(span) <= 74


def test_unexpected_field_shapes_render_as_dash() -> None:
    r = req()
    r.incident["status"]["proposal"]["faultClass"] = "bad rollout; @x"
    r.incident["status"]["evidence"]["bundleHash"] = "not-a-hash"
    text = body(prepared(), r, CFG)
    assert "Fault class:     -" in text and "Evidence bundle: -" in text
