"""proposals.request_id and reject_reason (ADR-035), and proposals.usage (Block 3, F-B): written
when the database has them, left out when it does not, so a new image against an old volume still
records everything else."""

import asyncio
import json

from nexus_operator.recorder import PostgresRecorder


class Pool:
    """A pool double: records SQL, answers the information_schema question."""

    def __init__(self, has_columns: bool, has_usage: bool = False):
        self.has_columns, self.has_usage = has_columns, has_usage
        self.inserts: list[tuple] = []
        self.probes = 0
        self.usage_probes = 0

    async def fetchval(self, sql, *args):
        if "'usage'" in sql:
            self.usage_probes += 1
            return 1 if self.has_usage else 0
        self.probes += 1
        return 2 if self.has_columns else 0

    async def execute(self, sql, *args):
        self.inserts.append((sql, args))
        return "INSERT 0 1"


def record(rec, **kw):
    return asyncio.run(
        rec.record_proposal(
            "h", "u", "llm", False, request_id="r-1", reject_reason="timeout", **kw
        )
    )


def test_the_new_columns_are_written_when_the_database_has_them():
    pool = Pool(True)
    assert record(PostgresRecorder(pool)) is True
    sql, args = pool.inserts[0]
    assert "request_id, reject_reason" in sql and "$13, $14" in sql
    assert args[-2:] == ("r-1", "timeout")


def test_an_old_schema_still_takes_the_row_without_them():
    pool = Pool(False)
    assert record(PostgresRecorder(pool)) is True
    sql, args = pool.inserts[0]
    assert "request_id" not in sql and len(args) == 12


def test_a_missing_column_is_looked_for_again_and_a_present_one_only_once():
    pool = Pool(False)
    rec = PostgresRecorder(pool)
    record(rec)
    record(rec)
    assert pool.probes == 2  # the volume may be migrated while the operator runs
    pool.has_columns = True
    record(rec)
    record(rec)
    assert pool.probes == 3
    assert pool.usage_probes == 0  # no usage to write, no question asked


USAGE = {
    "promptTokens": 1200,
    "completionTokens": 150,
    "totalTokens": 1350,
    "latencyMs": 812,
}


def test_usage_is_written_as_jsonb_when_the_column_exists():
    pool = Pool(True, has_usage=True)
    assert record(PostgresRecorder(pool), usage=USAGE) is True
    sql, args = pool.inserts[0]
    assert "request_id, reject_reason, usage" in sql and "$13, $14, $15::jsonb" in sql
    assert json.loads(args[-1]) == USAGE


def test_usage_without_the_extras_takes_the_next_placeholder():
    pool = Pool(False, has_usage=True)
    record(PostgresRecorder(pool), usage=USAGE)
    sql, args = pool.inserts[0]
    assert "request_id" not in sql and ", usage)" in sql and "$13::jsonb" in sql
    assert len(args) == 13


def test_usage_is_left_out_until_the_column_exists_and_then_probed_once_more():
    pool = Pool(True, has_usage=False)
    rec = PostgresRecorder(pool)
    record(rec, usage=USAGE)
    sql, args = pool.inserts[0]
    assert "usage" not in sql and len(args) == 14  # the row is still recorded
    pool.has_usage = True
    record(rec, usage=USAGE)
    record(rec, usage=USAGE)
    assert pool.usage_probes == 2 and "usage" in pool.inserts[1][0]


def test_the_schema_adds_the_column_the_writer_looks_for():
    """No PostgreSQL in this test run: at least the shipped schema adds `usage` as the writer
    expects it (additive, idempotent, jsonb), next to the ADR-035 pair."""
    import pathlib
    import re

    sql = (
        pathlib.Path(__file__).parents[2]
        / "k8s"
        / "flight-recorder-db"
        / "05-schema.sql"
    ).read_text()
    line = r"^ALTER TABLE proposals ADD COLUMN IF NOT EXISTS usage\s+jsonb;$"
    assert re.search(line, sql, re.MULTILINE)
