"""The Flight Recorder Writer (spec §14; ADR-031).

`Recorder` is the interface the operator's stages code against; `PostgresRecorder` implements it
over an asyncpg-style pool (`execute`, `fetchval`, `fetchrow`). Three properties matter:

* Idempotent. Every append is INSERT ... ON CONFLICT DO NOTHING on the spec's row key, so a retry
  after a crash or a timeout writes nothing twice. Each append returns True when it wrote the row.
* Fail closed. Any database error, timeout or lost connection raises `RecorderUnavailable`. The
  caller treats it as `Blocked` / `recorder_unavailable` (§17) before any mutation. A malformed
  row (a foreign-key or check violation) is also unavailable: NEXUS never mutates what it cannot
  first record.
* Append-only. The database role (10-init.sh) may insert, and update only the completion columns
  of `actions` and the terminal columns of `incidents`.

The write-ahead rule is `begin_action` (the intent row, committed) followed, after the Kubernetes
request, by `complete_action`. `actions.execute_recorded` runs that sequence.
"""

import asyncio
import dataclasses
import datetime
import json
from collections.abc import Sequence
from typing import Any, Protocol

DEFAULT_TIMEOUT_S = 3.0


class RecorderUnavailable(Exception):
    """The intent or record could not be written. Fail closed: send no request."""


@dataclasses.dataclass(frozen=True)
class ActionIntent:
    """What the operator is about to do: the `actions` row as of the write-ahead commit."""

    idempotency_key: str
    incident_uid: str
    action_id: str
    parameters: dict
    action_class: str
    previous_state: dict | None = None
    intent_at: datetime.datetime | None = None  # None: the database clock


class Recorder(Protocol):
    async def open_incident(
        self,
        incident_uid: str,
        target: str,
        source: str,
        detected_at: datetime.datetime,
        run_id: str | None = None,
    ) -> bool: ...

    async def close_incident(
        self, incident_uid: str, terminal_phase: str, reason: str
    ) -> bool: ...

    async def record_transition(
        self,
        incident_uid: str,
        seq: int,
        from_phase: str | None,
        to_phase: str,
        at: datetime.datetime,
        stage: str,
        reason: str | None = None,
    ) -> bool: ...

    async def record_bundle(
        self,
        bundle_hash: str,
        incident_uid: str,
        schema_version: str,
        content: dict,
        redaction_counts: dict,
        collected_at: datetime.datetime,
    ) -> bool: ...

    async def record_proposal(
        self,
        proposal_hash: str,
        incident_uid: str,
        reasoner_mode: str,
        schema_valid: bool,
        *,
        model_id: str | None = None,
        prompt_version: str | None = None,
        index_hash: str | None = None,
        retrieved_chunk_ids: Sequence[str] = (),
        raw_output: str | None = None,
        parsed_proposal: dict | None = None,
        latency_ms: int | None = None,
        fallback_used: bool = False,
        request_id: str | None = None,
        reject_reason: str | None = None,
    ) -> bool: ...

    async def record_gate_decision(
        self,
        incident_uid: str,
        stage: int,
        verdict: str,
        detail: dict | None,
        at: datetime.datetime,
    ) -> bool: ...

    async def record_verification(
        self,
        incident_uid: str,
        criteria_id: str,
        observations: dict,
        result: str,
        at: datetime.datetime,
    ) -> bool: ...

    async def begin_action(self, intent: ActionIntent) -> bool: ...

    async def complete_action(
        self,
        idempotency_key: str,
        request_at: datetime.datetime,
        audit_id: str | None,
        http_status: int | None,
        result: str,
    ) -> bool: ...

    async def count_actions(self, target: str, since: datetime.datetime) -> int: ...

    async def last_action_at(self, target: str) -> datetime.datetime | None: ...


def _j(value: Any) -> str | None:
    return (
        None
        if value is None
        else json.dumps(value, sort_keys=True, separators=(",", ":"))
    )


class PostgresRecorder:
    """`Recorder` over an asyncpg-style pool."""

    def __init__(self, pool: Any, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._pool = pool
        self._timeout = timeout_s
        self._extras = (
            False  # proposals.request_id / reject_reason exist (see record_proposal)
        )

    async def _run(self, call):
        try:
            return await asyncio.wait_for(call, self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise RecorderUnavailable(f"{type(e).__name__}: {str(e)[:200]}") from e

    async def _insert(self, sql: str, *args) -> bool:
        """True when the statement inserted a row, False when the key already existed."""
        status = await self._run(self._pool.execute(sql, *args))
        return _rows(status) == 1

    async def open_incident(
        self, incident_uid, target, source, detected_at, run_id=None
    ) -> bool:
        return await self._insert(
            "INSERT INTO incidents (incident_uid, run_id, target, source, detected_at)"
            " VALUES ($1, $2, $3, $4, $5) ON CONFLICT (incident_uid) DO NOTHING",
            incident_uid,
            run_id,
            target,
            source,
            detected_at,
        )

    async def close_incident(self, incident_uid, terminal_phase, reason) -> bool:
        status = await self._run(
            self._pool.execute(
                "UPDATE incidents SET terminal_phase = $2, reason = $3"
                " WHERE incident_uid = $1 AND terminal_phase IS NULL",
                incident_uid,
                terminal_phase,
                reason,
            )
        )
        return _rows(status) == 1

    async def record_transition(
        self, incident_uid, seq, from_phase, to_phase, at, stage, reason=None
    ) -> bool:
        return await self._insert(
            "INSERT INTO transitions (incident_uid, seq, from_phase, to_phase, at, stage, reason)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (incident_uid, seq) DO NOTHING",
            incident_uid,
            seq,
            from_phase,
            to_phase,
            at,
            stage,
            reason,
        )

    async def record_bundle(
        self,
        bundle_hash,
        incident_uid,
        schema_version,
        content,
        redaction_counts,
        collected_at,
    ) -> bool:
        return await self._insert(
            "INSERT INTO evidence_bundles (bundle_hash, incident_uid, schema_version, content,"
            " redaction_counts, collected_at)"
            " VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6) ON CONFLICT (bundle_hash) DO NOTHING",
            bundle_hash,
            incident_uid,
            schema_version,
            _j(content),
            _j(redaction_counts),
            collected_at,
        )

    async def record_proposal(
        self,
        proposal_hash,
        incident_uid,
        reasoner_mode,
        schema_valid,
        *,
        model_id=None,
        prompt_version=None,
        index_hash=None,
        retrieved_chunk_ids=(),
        raw_output=None,
        parsed_proposal=None,
        latency_ms=None,
        fallback_used=False,
        request_id=None,
        reject_reason=None,
    ) -> bool:
        """request_id and reject_reason are two columns added to `proposals` after ADR-031
        (05-schema.sql, ADD COLUMN IF NOT EXISTS). Until a database has them, they are left out
        of the INSERT, so a new image against an old volume still records everything else."""
        columns = (
            "proposal_hash, incident_uid, reasoner_mode, model_id, prompt_version, index_hash,"
            " retrieved_chunk_ids, raw_output, parsed_proposal, schema_valid, latency_ms,"
            " fallback_used"
        )
        values = "$1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12"
        args = [
            proposal_hash,
            incident_uid,
            reasoner_mode,
            model_id,
            prompt_version,
            index_hash,
            list(retrieved_chunk_ids),
            raw_output,
            _j(parsed_proposal),
            schema_valid,
            latency_ms,
            fallback_used,
        ]
        if await self._has_proposal_extras():
            columns += ", request_id, reject_reason"
            values += ", $13, $14"
            args += [request_id, reject_reason]
        return await self._insert(
            f"INSERT INTO proposals ({columns}) VALUES ({values})"
            " ON CONFLICT (proposal_hash) DO NOTHING",
            *args,
        )

    async def _has_proposal_extras(self) -> bool:
        if not self._extras:
            found = await self._run(
                self._pool.fetchval(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'proposals'"
                    " AND column_name IN ('request_id', 'reject_reason')"
                )
            )
            self._extras = int(found) == 2  # only a True answer is kept
        return self._extras

    async def record_gate_decision(
        self, incident_uid, stage, verdict, detail, at
    ) -> bool:
        return await self._insert(
            "INSERT INTO gate_decisions (incident_uid, stage, verdict, detail, at)"
            " VALUES ($1, $2, $3, $4::jsonb, $5) ON CONFLICT (incident_uid, stage) DO NOTHING",
            incident_uid,
            stage,
            verdict,
            _j(detail),
            at,
        )

    async def record_verification(
        self, incident_uid, criteria_id, observations, result, at
    ) -> bool:
        return await self._insert(
            "INSERT INTO verifications (incident_uid, criteria_id, observations, result, at)"
            " VALUES ($1, $2, $3::jsonb, $4, $5)"
            " ON CONFLICT (incident_uid, criteria_id) DO NOTHING",
            incident_uid,
            criteria_id,
            _j(observations),
            result,
            at,
        )

    async def begin_action(self, intent: ActionIntent) -> bool:
        """The write-ahead intent row. True: new. False: this key was already recorded (a retry)."""
        return await self._insert(
            "INSERT INTO actions (idempotency_key, incident_uid, action_id, parameters,"
            " previous_state, intent_at, action_class)"
            " VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, COALESCE($6, now()), $7)"
            " ON CONFLICT (idempotency_key) DO NOTHING",
            intent.idempotency_key,
            intent.incident_uid,
            intent.action_id,
            _j(intent.parameters),
            _j(intent.previous_state),
            intent.intent_at,
            intent.action_class,
        )

    async def complete_action(
        self, idempotency_key, request_at, audit_id, http_status, result
    ) -> bool:
        """Complete the intent row once. False: already completed, or no such intent."""
        status = await self._run(
            self._pool.execute(
                "UPDATE actions SET request_at = $2, audit_id = $3, http_status = $4, result = $5"
                " WHERE idempotency_key = $1 AND request_at IS NULL",
                idempotency_key,
                request_at,
                audit_id,
                http_status,
                result,
            )
        )
        return _rows(status) == 1

    async def count_actions(self, target: str, since: datetime.datetime) -> int:
        """Actions recorded for this target since `since` (the breaker's count).

        Counted by intent time, so an intent row without a completion counts: the request may
        have gone out.
        """
        return int(
            await self._run(
                self._pool.fetchval(
                    "SELECT count(*) FROM actions a JOIN incidents i USING (incident_uid)"
                    " WHERE i.target = $1 AND a.intent_at >= $2",
                    target,
                    since,
                )
            )
        )

    async def last_action_at(self, target: str) -> datetime.datetime | None:
        """The latest intent time for this target (the cool-down's clock), or None."""
        return await self._run(
            self._pool.fetchval(
                "SELECT max(a.intent_at) FROM actions a JOIN incidents i USING (incident_uid)"
                " WHERE i.target = $1",
                target,
            )
        )


def _rows(status: str) -> int:
    """The row count of an asyncpg command tag: 'INSERT 0 1', 'UPDATE 1'."""
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def connect(
    *,
    host: str,
    port: int = 5432,
    database: str = "nexus_recorder",
    user: str = "nexus_writer",
    password: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> PostgresRecorder:
    """A `PostgresRecorder` over a small asyncpg pool. asyncpg is imported here, not at module load."""
    import asyncpg

    try:
        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            min_size=1,
            max_size=4,
            timeout=timeout_s,
            command_timeout=timeout_s,
        )
    except Exception as e:
        raise RecorderUnavailable(f"{type(e).__name__}: {str(e)[:200]}") from e
    return PostgresRecorder(pool, timeout_s)
