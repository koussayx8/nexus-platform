"""proposals.request_id and reject_reason (ADR-035): written when the database has them, left out
when it does not, so a new image against an old volume still records everything else."""

import asyncio

from nexus_operator.recorder import PostgresRecorder


class Pool:
    """A pool double: records SQL, answers the information_schema question."""

    def __init__(self, has_columns: bool):
        self.has_columns = has_columns
        self.inserts: list[tuple] = []
        self.probes = 0

    async def fetchval(self, sql, *args):
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
