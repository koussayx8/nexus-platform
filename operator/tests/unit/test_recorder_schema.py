"""The Flight Recorder schema and grants on a real PostgreSQL (spec §14): the tables exist, the
init is rerunnable, and the operator role can append but not rewrite or delete (append-only)."""

import pgfixture

TABLES = {
    "runs",
    "incidents",
    "transitions",
    "evidence_bundles",
    "proposals",
    "gate_decisions",
    "actions",
    "verifications",
    "audit_links",
}

SEED = """
INSERT INTO runs VALUES ('r1','s1','rules',1,1,'abc','gt','{}',true,now(),NULL,false,NULL)
  ON CONFLICT DO NOTHING;
INSERT INTO incidents (incident_uid, run_id, target, source, detected_at)
  VALUES ('u1','r1','nexus-dev/sample-api','alert',now()) ON CONFLICT DO NOTHING;
INSERT INTO actions (idempotency_key, incident_uid, action_id, parameters, intent_at, action_class)
  VALUES ('k1','u1','scale-out','{"replicas":2}',now(),'runtime') ON CONFLICT DO NOTHING;
"""


def seed(pg):
    r = pg.psql("postgres", SEED)
    assert r.returncode == 0, r.stderr


def denied(pg, user, statements):
    for stmt in statements:
        r = pg.psql(user, stmt)
        assert r.returncode != 0, f"{user} was allowed: {stmt}"
        assert "permission denied" in r.stderr or "must be owner" in r.stderr, (
            stmt,
            r.stderr,
        )


def test_tables_match_spec_section_14(pg):
    r = pg.psql(
        "nexus_reader",
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'",
    )
    assert set(r.stdout.split()) == TABLES


def test_schema_is_idempotent(pg):
    r = pg._psql_file(pgfixture.K8S / "05-schema.sql", {})
    assert r.returncode == 0, r.stderr


def test_writer_appends_and_completes_only_its_columns(pg):
    seed(pg)
    for stmt in [
        "INSERT INTO transitions VALUES ('u1',0,NULL,'Detected',now(),'intake',NULL)",
        "INSERT INTO gate_decisions VALUES ('u1',1,'pass',NULL,now())",
        (
            "UPDATE actions SET request_at=now(), audit_id='a1', http_status=200,"
            " result='applied' WHERE idempotency_key='k1'"
        ),
        (
            "UPDATE incidents SET terminal_phase='Resolved', reason='remediation_verified'"
            " WHERE incident_uid='u1'"
        ),
    ]:
        r = pg.psql("nexus_writer", stmt)
        assert r.returncode == 0, f"{stmt}: {r.stderr}"


def test_writer_cannot_rewrite_or_delete(pg):
    seed(pg)
    denied(
        pg,
        "nexus_writer",
        [
            "UPDATE actions SET parameters='{}' WHERE idempotency_key='k1'",
            "UPDATE actions SET intent_at=now() WHERE idempotency_key='k1'",
            "UPDATE transitions SET to_phase='x'",
            "UPDATE incidents SET target='x'",
            "UPDATE runs SET scenario='x'",
            "DELETE FROM actions",
            "DELETE FROM transitions",
            "TRUNCATE actions",
            "INSERT INTO runs VALUES ('r2','s','rules',1,1,'c','g','{}',true,now(),NULL,false,NULL)",
            "INSERT INTO audit_links VALUES ('a1','k1','matched',now())",
            "DROP TABLE actions",
        ],
    )


def test_reader_is_read_only(pg):
    seed(pg)
    assert pg.psql("nexus_reader", "SELECT count(*) FROM actions").returncode == 0
    denied(
        pg,
        "nexus_reader",
        [
            "INSERT INTO gate_decisions VALUES ('u1',2,'pass',NULL,now())",
            "UPDATE actions SET result='x'",
            "DELETE FROM actions",
        ],
    )


def test_runner_writes_runs_and_audit_links_only(pg):
    seed(pg)
    r = pg.psql(
        "nexus_runner", "INSERT INTO audit_links VALUES ('a-r','k1','matched',now())"
    )
    assert r.returncode == 0, r.stderr
    r = pg.psql("nexus_runner", "UPDATE runs SET ended_at=now() WHERE run_id='r1'")
    assert r.returncode == 0, r.stderr
    denied(
        pg,
        "nexus_runner",
        [
            (
                "INSERT INTO actions (idempotency_key, incident_uid, action_id, parameters,"
                " intent_at, action_class) VALUES ('k9','u1','x','{}',now(),'runtime')"
            ),
            "UPDATE audit_links SET verdict='x'",
            "DELETE FROM runs",
        ],
    )


def test_checks_reject_inconsistent_rows(pg):
    seed(pg)
    # An Audit-Id without a request time is impossible; a terminal phase needs a reason.
    r = pg.psql(
        "postgres",
        "INSERT INTO actions (idempotency_key, incident_uid, action_id, parameters, intent_at,"
        " action_class, audit_id) VALUES ('k2','u1','x','{}',now(),'runtime','a2')",
    )
    assert r.returncode != 0 and "check constraint" in r.stderr
    r = pg.psql(
        "postgres",
        "INSERT INTO incidents (incident_uid, target, source, detected_at, terminal_phase)"
        " VALUES ('u2','t','s',now(),'Resolved')",
    )
    assert r.returncode != 0 and "check constraint" in r.stderr
