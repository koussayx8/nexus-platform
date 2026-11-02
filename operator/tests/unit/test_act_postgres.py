"""The act path against the REAL Writer (PostgresActRecorder on a throwaway PostgreSQL): the
write-ahead rule, the Kill Switch, verification and compensation, and redaction in storage.
Skips without server binaries or asyncpg; fails in CI (see conftest.pg)."""

import asyncio
import datetime
import importlib
import json
import os

import pytest

from fakes import T0
from fakes_act import FakeMetrics, proposal_doc
from nexus_operator.model import iso
from nexus_operator.recorder import (
    ReconnectingRecorder,
    RecorderUnavailable,
    connect_act,
)
from rig import Rig, ended

if os.environ.get("CI"):
    importlib.import_module("asyncpg")
else:
    pytest.importorskip("asyncpg")

_n = 0


def fresh(name: str) -> str:
    """A name no earlier test used: the database outlives a test, incidents are keyed by uid."""
    global _n
    _n += 1
    return f"{name}-{_n}"


class PgRig(Rig):
    def __init__(self, pg, **kw):
        loop = asyncio.new_event_loop()
        rec = loop.run_until_complete(
            connect_act(host="127.0.0.1", port=pg.port, password="w-pass")
        )
        super().__init__(recorder=rec, **kw)
        self.loop.close()
        self.loop = loop  # the pool lives on this loop
        self.pg = pg
        # The database outlives a test and the cool-down and breaker windows are time-based, so
        # every rig runs 3 hours after the previous one: no earlier test's actions are in range.
        global _n
        _n += 1
        self.clock.t = self.clock.t + datetime.timedelta(hours=3 * _n)
        self.api.audit_prefix = f"audit-pg{_n}"
        self._trace()

    def _trace(self):
        """Put the recorder's write-ahead calls into the same event list as the API requests."""
        begin, complete = self.rec.begin_action, self.rec.complete_action

        async def traced_begin(intent):
            wrote = await begin(intent)
            self.api.events.append(f"intent:{intent.idempotency_key}")
            return wrote

        async def traced_complete(key, *args):
            done = await complete(key, *args)
            self.api.events.append(f"complete:{key}")
            return done

        self.rec.begin_action, self.rec.complete_action = traced_begin, traced_complete

    def sql(self, query: str, *args):
        return self.call(self.rec._pool.fetch(query, *args))

    def close(self):
        self.call(self.rec._pool.close())

    def seed(self, name):
        self.api.put(name, created=T0, status={})


def uid_of(name: str) -> str:
    return f"uid-{name}"


def run_scale(pg, metrics, name=None, **kw):
    name = name or fresh("pg")
    rig = PgRig(pg, metrics=metrics, **kw)
    rig.proposed(proposal_doc("scale_deployment", 3), name=name)
    return rig, name


def test_write_ahead_with_the_real_writer(pg):
    rig, name = run_scale(pg, FakeMetrics())
    try:
        rig.run(name, ticks=2)
        row = rig.sql(
            "SELECT intent_at, request_at, audit_id, http_status, result, action_class"
            " FROM actions WHERE incident_uid = $1",
            uid_of(name),
        )
        assert len(row) == 1
        r = row[0]
        assert (
            r["audit_id"] == f"{rig.api.audit_prefix}-2"
            and r["http_status"] == 200
            and r["result"] == "applied"
        )
        assert r["action_class"] == "executed"
        assert r["request_at"] is not None and r["intent_at"] <= r["request_at"]
        # the intent row was committed before the request, the completion after
        assert [
            e for e in rig.api.events if e.startswith(("intent", "request", "complete"))
        ] == [
            f"intent:{name}-1",
            "request:scale",
            f"complete:{name}-1",
        ]
    finally:
        rig.close()


def test_a_resolved_incident_leaves_a_complete_record(pg):
    rig, name = run_scale(pg, FakeMetrics())
    try:
        rig.run(name, ticks=2)
        verify_rig = rig
        for _ in range(14):
            if rig.status(name)["phase"] != "Verifying":
                break
            rig.tick(name, seconds=10)
        assert ended(rig, name) == ("Resolved", "remediation_verified")
        u = uid_of(name)
        inc = rig.sql(
            "SELECT target, source, terminal_phase, reason FROM incidents WHERE incident_uid=$1",
            u,
        )
        assert dict(inc[0]) == {
            "target": "nexus-dev/sample-api",
            "source": "detector",
            "terminal_phase": "Resolved",
            "reason": "remediation_verified",
        }
        trans = rig.sql(
            "SELECT seq, to_phase FROM transitions WHERE incident_uid=$1 ORDER BY seq",
            u,
        )
        assert [(t["seq"], t["to_phase"]) for t in trans] == [
            (4, "Executing"),
            (5, "Verifying"),
            (7, "Resolved"),
        ]
        gates = rig.sql(
            "SELECT stage, verdict FROM gate_decisions WHERE incident_uid=$1 ORDER BY stage",
            u,
        )
        assert [(g["stage"], g["verdict"]) for g in gates] == [
            (i, "pass") for i in range(1, 8)
        ]
        ver = rig.sql(
            "SELECT criteria_id, result FROM verifications WHERE incident_uid=$1", u
        )
        assert [(v["criteria_id"], v["result"]) for v in ver] == [("scale_v1", "met")]
        assert verify_rig is rig
    finally:
        rig.close()


def test_kill_switch_halted_blocks_before_any_row_or_request(pg):
    name = fresh("pg-ks")
    rig = PgRig(pg, killswitch={"data": {"state": "halted"}})
    try:
        rig.proposed(proposal_doc("scale_deployment", 3), name=name)
        rig.run(name)
        assert ended(rig, name) == ("Blocked", "killswitch")
        assert rig.api.mutations() == []
        assert (
            rig.sql("SELECT 1 FROM actions WHERE incident_uid=$1", uid_of(name)) == []
        )
        stage5 = rig.sql(
            "SELECT verdict FROM gate_decisions WHERE incident_uid=$1 AND stage=5",
            uid_of(name),
        )
        assert [g["verdict"] for g in stage5] == ["fail"]
    finally:
        rig.close()


def test_a_failed_verification_is_compensated_with_two_audited_rows(pg):
    rig, name = run_scale(pg, FakeMetrics(p95=0.9))
    try:
        rig.run(name, ticks=2)
        for _ in range(26):
            if rig.status(name)["phase"] != "Verifying":
                break
            rig.tick(name, seconds=10)
        rig.run(name)
        assert ended(rig, name) == ("Escalated", "verification_failed")
        rows = rig.sql(
            "SELECT idempotency_key, action_class, audit_id FROM actions"
            " WHERE incident_uid=$1 ORDER BY intent_at, idempotency_key",
            uid_of(name),
        )
        assert sorted((r["action_class"], r["audit_id"] is not None) for r in rows) == [
            ("compensation", True),
            ("executed", True),
        ]
        assert rig.api.replicas == 2
    finally:
        rig.close()


def test_an_unreachable_database_blocks_before_any_request(tmp_path):
    pw = tmp_path / "password"
    pw.write_text("w-pass\n")
    rec = ReconnectingRecorder(
        host="127.0.0.1",
        port=1,
        database="nexus_recorder",
        user="nexus_writer",
        password_file=str(pw),
    )
    rig = Rig(recorder=rec)
    rig.api.put(
        "inc",
        created=T0,
        status={
            "phase": "Proposed",
            "autonomyLevel": 3,
            "timestamps": {"proposed": iso(rig.clock())},
            "proposal": {"hash": "sha256:inc"},
        },
    )
    rig.run()  # no proposal can be read, so the 30 s gate timeout ends it
    assert ended(rig) == ("Escalated", "gate_timeout")
    assert rig.api.mutations() == []
    assert rec.up is False


def test_the_reconnecting_recorder_connects_drops_and_reconnects(pg, tmp_path):
    pw = tmp_path / "password"
    pw.write_text("w-pass\n")
    uid = fresh("pg-reconnect")

    async def go():
        rec = ReconnectingRecorder(
            host="127.0.0.1",
            port=pg.port,
            database="nexus_recorder",
            user="nexus_writer",
            password_file=str(pw),
        )
        assert (
            await rec.open_incident(uid, "nexus-dev/sample-api", "detector", T0) is True
        )
        assert rec.up is True
        await rec._inner._pool.close()  # the connection is lost under it
        with pytest.raises(RecorderUnavailable):
            await rec.open_incident(uid, "nexus-dev/sample-api", "detector", T0)
        assert rec.up is False and rec._inner is None
        assert (
            await rec.open_incident(uid, "nexus-dev/sample-api", "detector", T0)
            is False
        )  # reconnected
        assert rec.up is True
        await rec.close()

    asyncio.run(go())


def test_the_password_file_is_required_and_read_at_connect(tmp_path):
    async def go():
        rec = ReconnectingRecorder(
            host="127.0.0.1",
            port=1,
            database="d",
            user="u",
            password_file=str(tmp_path / "missing"),
        )
        with pytest.raises(RecorderUnavailable, match="password file"):
            await rec.get_proposal("h")
        empty = tmp_path / "empty"
        empty.write_text("\n")
        rec2 = ReconnectingRecorder(
            host="127.0.0.1", port=1, database="d", user="u", password_file=str(empty)
        )
        with pytest.raises(RecorderUnavailable, match="empty"):
            await rec2.get_proposal("h")

    asyncio.run(go())


def test_redaction_holds_in_the_real_database(pg):
    from test_diagnose import SECRETS, poison

    name = fresh("pg-redact")
    rig = PgRig(pg, collector=True, metrics=FakeMetrics(latencyP95=4.0))
    try:
        poison(rig)
        rig.api.put(name, created=T0, status={"phase": "Detected", "autonomyLevel": 3})
        rig.run(name, ticks=3)
        assert rig.status(name)["phase"] in {"Proposed", "Executing"}
        u = uid_of(name)
        stored = {
            "bundles": [
                dict(r)
                for r in rig.sql(
                    "SELECT * FROM evidence_bundles WHERE incident_uid=$1", u
                )
            ],
            "proposals": [
                dict(r)
                for r in rig.sql("SELECT * FROM proposals WHERE incident_uid=$1", u)
            ],
            "transitions": [
                dict(r)
                for r in rig.sql("SELECT * FROM transitions WHERE incident_uid=$1", u)
            ],
            "gates": [
                dict(r)
                for r in rig.sql(
                    "SELECT * FROM gate_decisions WHERE incident_uid=$1", u
                )
            ],
            "status": rig.api.incidents[name],
        }
        assert stored["bundles"] and stored["proposals"]
        blob = json.dumps(stored, default=str)
        for secret in SECRETS:
            assert secret not in blob, secret
        counts = json.loads(stored["bundles"][0]["redaction_counts"])
        assert counts["token"] >= 1 and counts["email"] >= 1
        assert iso(
            T0
        )  # (the clock helper is imported for symmetry with the fake-recorder test)
    finally:
        rig.close()
