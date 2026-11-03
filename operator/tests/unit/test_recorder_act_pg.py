"""The act path's two extra reads (recorder/act.py) against a real PostgreSQL. Skips without
server binaries or asyncpg, and fails in CI (see conftest.pg)."""

import asyncio
import datetime
import importlib
import os

import pytest

from nexus_operator.recorder import ActionIntent, connect_act

if os.environ.get("CI"):
    importlib.import_module("asyncpg")
else:
    pytest.importorskip("asyncpg")

T0 = datetime.datetime(2026, 11, 2, 9, 0, 0, tzinfo=datetime.timezone.utc)


def minutes(n):
    return T0 + datetime.timedelta(minutes=n)


def run(pg, coro_fn):
    async def go():
        rec = await connect_act(host="127.0.0.1", port=pg.port, password="w-pass")
        try:
            return await coro_fn(rec)
        finally:
            await rec._pool.close()

    return asyncio.run(go())


def test_recent_actions_joins_the_target_and_filters_by_time(pg):
    async def go(rec):
        await rec.open_incident("u-act1", "nexus-dev/act-api", "detector", T0)
        for key, at in (("k-act1", 0), ("k-act2", 10)):
            await rec.begin_action(
                ActionIntent(
                    key,
                    "u-act1",
                    "scale_deployment",
                    {"replicas": 3},
                    "executed",
                    None,
                    minutes(at),
                )
            )
        await rec.complete_action("k-act2", minutes(10), "audit-act", 403, "denied: no")
        rows = [
            r
            for r in await rec.recent_actions(minutes(5))
            if r.incident_uid == "u-act1"
        ]
        assert [(r.target, r.result, r.action_class) for r in rows] == [
            ("nexus-dev/act-api", "denied: no", "executed")
        ]
        both = [
            r
            for r in await rec.recent_actions(minutes(-1))
            if r.incident_uid == "u-act1"
        ]
        assert len(both) == 2

    run(pg, go)


def test_get_proposal_by_hash(pg):
    async def go(rec):
        await rec.open_incident("u-act2", "nexus-dev/act-api", "detector", T0)
        doc = {"schema": "proposal/v1", "abstain": False}
        await rec.record_proposal("h-ok", "u-act2", "rules", True, parsed_proposal=doc)
        await rec.record_proposal(
            "h-bad", "u-act2", "llm", False, raw_output="not json"
        )
        assert await rec.get_proposal("h-ok") == doc
        assert await rec.get_proposal("h-bad") == "not json"
        assert await rec.get_proposal("h-none") is None

    run(pg, go)


def test_a_rejected_proposal_row_keeps_its_request_id_and_reason(pg):
    async def go(rec):
        await rec.open_incident("u-act3", "nexus-dev/act-api", "detector", T0)
        assert await rec.record_proposal(
            "h-fail",
            "u-act3",
            "llm",
            False,
            model_id="m",
            raw_output='{"x": 1}',
            request_id="req-1",
            reject_reason="rejected: not JSON",
        )
        assert await rec.record_proposal(
            "h-used", "u-act3", "rules", True, fallback_used=True, request_id="req-1"
        )
        rows = await rec._pool.fetch(
            "SELECT proposal_hash, request_id, reject_reason, fallback_used, raw_output"
            " FROM proposals WHERE incident_uid = 'u-act3' ORDER BY proposal_hash"
        )
        assert [dict(r) for r in rows] == [
            {"proposal_hash": "h-fail", "request_id": "req-1", "reject_reason": "rejected: not JSON",
             "fallback_used": False, "raw_output": '{"x": 1}'},
            {"proposal_hash": "h-used", "request_id": "req-1", "reject_reason": None,
             "fallback_used": True, "raw_output": None},
        ]  # fmt: skip

    run(pg, go)
