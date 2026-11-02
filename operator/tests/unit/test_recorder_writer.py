"""The Writer against a real PostgreSQL (spec §14): idempotent appends, the write-ahead intent and
its completion, the breaker's counts, and fail-closed behaviour when the database is not there.
Needs server binaries (the `pg` fixture) and asyncpg. Locally it skips without them; in CI (the
CI variable) a missing one fails, because the write-ahead rule is a safety invariant."""

import asyncio
import datetime
import importlib
import os

import pytest

from nexus_operator.recorder import (
    ActionIntent,
    PostgresRecorder,
    RecorderUnavailable,
    connect,
)

if os.environ.get("CI"):
    importlib.import_module("asyncpg")
else:
    pytest.importorskip("asyncpg")

T0 = datetime.datetime(2026, 11, 2, 9, 0, 0, tzinfo=datetime.timezone.utc)


def minutes(n):
    return T0 + datetime.timedelta(minutes=n)


def run(pg, coro_fn):
    """Run `coro_fn(recorder)` with a writer-role recorder, and always close the pool."""

    async def go():
        rec = await connect(host="127.0.0.1", port=pg.port, password="w-pass")
        try:
            return await coro_fn(rec)
        finally:
            await rec._pool.close()

    return asyncio.run(go())


def intent(key="k-1", uid="u-1", **kw):
    return ActionIntent(
        idempotency_key=key,
        incident_uid=uid,
        action_id="scale-out",
        parameters={"replicas": 2},
        action_class="runtime",
        previous_state={"replicas": 1},
        **kw,
    )


def test_appends_are_idempotent(pg):
    async def go(rec):
        assert await rec.open_incident("u-idem", "nexus-dev/sample-api", "alert", T0)
        assert not await rec.open_incident(
            "u-idem", "nexus-dev/sample-api", "alert", T0
        )
        t = ("u-idem", 0, None, "Detected", T0, "intake")
        assert await rec.record_transition(*t)
        assert not await rec.record_transition(*t)
        assert await rec.record_gate_decision("u-idem", 3, "pass", {"bounds": "ok"}, T0)
        assert not await rec.record_gate_decision("u-idem", 3, "fail", None, T0)
        assert await rec.record_bundle(
            "h-1", "u-idem", "evidence/v1", {"a": 1}, {"n": 0}, T0
        )
        assert not await rec.record_bundle(
            "h-1", "u-idem", "evidence/v1", {"a": 1}, {"n": 0}, T0
        )
        assert await rec.record_proposal(
            "p-1", "u-idem", "rules", True, parsed_proposal={"actionId": "scale-out"}
        )
        assert not await rec.record_proposal("p-1", "u-idem", "rules", True)
        assert await rec.record_verification("u-idem", "c1", {"err": 0.0}, "pass", T0)
        assert not await rec.record_verification(
            "u-idem", "c1", {"err": 0.0}, "pass", T0
        )

    run(pg, go)
    r = pg.psql(
        "postgres", "SELECT count(*) FROM transitions WHERE incident_uid='u-idem'"
    )
    assert r.stdout.strip() == "1"


def test_close_incident_writes_once(pg):
    async def go(rec):
        await rec.open_incident("u-close", "nexus-dev/sample-api", "alert", T0)
        assert await rec.close_incident("u-close", "Resolved", "remediation_verified")
        assert not await rec.close_incident(
            "u-close", "Escalated", "verification_failed"
        )

    run(pg, go)
    r = pg.psql(
        "postgres", "SELECT terminal_phase FROM incidents WHERE incident_uid='u-close'"
    )
    assert r.stdout.strip() == "Resolved"


def test_write_ahead_intent_then_completion(pg):
    async def go(rec):
        await rec.open_incident("u-wa", "nexus-dev/sample-api", "alert", T0)
        assert await rec.begin_action(intent("k-wa", "u-wa", intent_at=T0))
        # The intent is committed and visible before the request: no audit_id, no request time.
        row = pg.psql(
            "postgres",
            "SELECT request_at IS NULL, audit_id IS NULL, http_status IS NULL"
            " FROM actions WHERE idempotency_key='k-wa'",
        )
        assert row.stdout.strip() == "t|t|t"
        assert not await rec.begin_action(
            intent("k-wa", "u-wa", intent_at=T0)
        )  # a retry
        assert await rec.complete_action(
            "k-wa", minutes(1), "audit-uuid-1", 200, "applied"
        )
        assert not await rec.complete_action("k-wa", minutes(2), "other", 500, "again")

    run(pg, go)
    r = pg.psql(
        "postgres",
        "SELECT audit_id, http_status, result FROM actions WHERE idempotency_key='k-wa'",
    )
    assert r.stdout.strip() == "audit-uuid-1|200|applied"


def test_intent_without_its_incident_fails_closed(pg):
    async def go(rec):
        with pytest.raises(RecorderUnavailable):
            await rec.begin_action(intent("k-orphan", "u-does-not-exist"))

    run(pg, go)
    r = pg.psql(
        "postgres", "SELECT count(*) FROM actions WHERE idempotency_key='k-orphan'"
    )
    assert r.stdout.strip() == "0"


def test_breaker_counts_by_target_and_window(pg):
    async def go(rec):
        for uid, target in (("u-b1", "nexus-dev/b-api"), ("u-b2", "nexus-dev/other")):
            await rec.open_incident(uid, target, "alert", T0)
        await rec.begin_action(intent("k-b1", "u-b1", intent_at=minutes(0)))
        await rec.begin_action(intent("k-b2", "u-b1", intent_at=minutes(10)))
        await rec.begin_action(intent("k-b3", "u-b2", intent_at=minutes(11)))
        assert await rec.count_actions("nexus-dev/b-api", minutes(-1)) == 2
        assert await rec.count_actions("nexus-dev/b-api", minutes(5)) == 1
        assert await rec.count_actions("nexus-dev/nobody", minutes(-1)) == 0
        assert await rec.last_action_at("nexus-dev/b-api") == minutes(10)
        assert await rec.last_action_at("nexus-dev/nobody") is None

    run(pg, go)


def test_unreachable_database_is_unavailable():
    async def go():
        with pytest.raises(RecorderUnavailable):
            await connect(host="127.0.0.1", port=1, password="x", timeout_s=1)

    asyncio.run(go())


def test_a_hung_database_times_out_as_unavailable():
    class Hung:
        async def execute(self, *a):
            await asyncio.sleep(30)

    async def go():
        rec = PostgresRecorder(Hung(), timeout_s=0.05)
        with pytest.raises(RecorderUnavailable):
            await rec.begin_action(intent())

    asyncio.run(go())
