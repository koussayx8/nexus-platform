"""Strict xfail tests for docs/reviews/2026-11-10-authority-boundary.md, finding A4, against a
real PostgreSQL (the `pg` fixture: skipped without server binaries, required in CI).

PostgreSQL stores neither a NUL byte in `text` nor the escape \\u0000 in `jsonb`. The Writer wraps
every database error as RecorderUnavailable, so one such character in a pod's log, or in what the
Reasoner returns, reads as a Flight Recorder outage: the Incident ends Blocked /
recorder_unavailable and neither the bundle nor the proposal is stored.
"""

import asyncio
import datetime
import importlib
import os

import pytest

from nexus_operator.recorder import RecorderUnavailable, connect_act

if os.environ.get("CI"):
    importlib.import_module("asyncpg")
else:
    pytest.importorskip("asyncpg")

T0 = datetime.datetime(2026, 11, 2, 9, 0, 0, tzinfo=datetime.timezone.utc)
NUL = "before\x00after"


def review(finding: str):
    return pytest.mark.xfail(
        strict=True,
        raises=RecorderUnavailable,
        reason=f"authority-boundary review {finding}",
    )


def run(pg, coro_fn):
    async def go():
        rec = await connect_act(host="127.0.0.1", port=pg.port, password="w-pass")
        try:
            return await coro_fn(rec)
        finally:
            await rec._pool.close()

    return asyncio.run(go())


@review("A4: a NUL character in a log line makes the bundle unstorable")
def test_a_bundle_with_a_nul_character_in_a_log_line_is_stored(pg):
    async def go(rec):
        await rec.open_incident("u-rev-a4a", "nexus-dev/rev-api", "detector", T0)
        bundle = {"schema": "evidence/v1.1", "logs": {"current": [NUL]}}
        assert await rec.record_bundle(
            "sha256:rev-a4a", "u-rev-a4a", "evidence/v1.1", bundle, {}, T0
        )

    run(pg, go)


@review(
    "A4: a NUL character in the Reasoner's raw output makes the proposal unstorable"
)
def test_a_proposal_with_a_nul_character_in_the_raw_output_is_stored(pg):
    async def go(rec):
        await rec.open_incident("u-rev-a4b", "nexus-dev/rev-api", "detector", T0)
        assert await rec.record_proposal(
            "sha256:rev-a4b", "u-rev-a4b", "R2", False, raw_output=NUL
        )

    run(pg, go)


@review("A4: the escape \\u0000 in a parsed proposal makes it unstorable")
def test_a_proposal_with_a_nul_character_in_its_summary_is_stored(pg):
    async def go(rec):
        await rec.open_incident("u-rev-a4c", "nexus-dev/rev-api", "detector", T0)
        doc = {"schema": "proposal/v1", "hypothesis": {"summary": NUL}}
        assert await rec.record_proposal(
            "sha256:rev-a4c", "u-rev-a4c", "R2", True, parsed_proposal=doc
        )

    run(pg, go)
