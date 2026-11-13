"""What the act path needs from the recorder beyond the Writer (spec §4, §9, §14; ADR-031).

The Writer's `count_actions` counts rows per target, and the breaker counts executed actions:
a restart that evicts three pods is three rows and one action, a denied request changed nothing,
a compensation must stay possible, and the 6-per-hour limit spans every target. So the act path
reads the recent rows once and counts them itself (breaker.py). The Gate also reads the stored
proposal by the Incident's uid and its hash (ADR-039). Both are added here, inside the package, without touching the Writer:

    ActRecorder            the Recorder protocol plus recent_actions and get_proposal
    PostgresActRecorder    PostgresRecorder plus those two reads (one SELECT each)
    ReconnectingRecorder   the Writer the operator holds: connects on demand, fails closed
    health_loop            SELECT 1 every 30 s on its own connection, so recorder_up stays current
    UnavailableRecorder    every call raises RecorderUnavailable: used until the Writer is wired in
                           (the runtime image has no asyncpg pin yet, ADR-031), so every mutation
                           ends Blocked / recorder_unavailable
"""

import asyncio
import dataclasses
import datetime
import json
import logging
import pathlib
from typing import Any, Protocol

from ..metrics import METRICS, RECORDER_ERRORS
from .writer import PostgresRecorder, Recorder, RecorderUnavailable, connect

logger = logging.getLogger("nexus.recorder")

HEALTH_PERIOD_S = 30.0
PROBE_TIMEOUT_S = 5.0


@dataclasses.dataclass(frozen=True)
class ActionRecord:
    """One recorded action request, as the breaker counts it. target is `namespace/name`."""

    incident_uid: str
    action_id: str
    target: str
    intent_at: datetime.datetime
    result: str | None
    action_class: str  # executed | compensation


class ActRecorder(Recorder, Protocol):
    async def recent_actions(self, since: datetime.datetime) -> list[ActionRecord]: ...

    async def get_proposal(self, incident_uid: str, proposal_hash: str) -> Any | None:
        """This Incident's stored parsed proposal; the raw output when it did not parse; None if
        this Incident has no row with that hash (ADR-039: never another Incident's row)."""
        ...


class PostgresActRecorder(PostgresRecorder):
    async def recent_actions(self, since: datetime.datetime) -> list[ActionRecord]:
        rows = await self._run(
            self._pool.fetch(
                "SELECT a.incident_uid, a.action_id, i.target, a.intent_at, a.result,"
                " a.action_class FROM actions a JOIN incidents i USING (incident_uid)"
                " WHERE a.intent_at >= $1",
                since,
            )
        )
        return [
            ActionRecord(
                r["incident_uid"],
                r["action_id"],
                r["target"],
                r["intent_at"],
                r["result"],
                r["action_class"],
            )
            for r in rows
        ]

    async def get_proposal(self, incident_uid: str, proposal_hash: str) -> Any | None:
        row = await self._run(
            self._pool.fetchrow(
                "SELECT parsed_proposal, raw_output FROM proposals"
                " WHERE incident_uid = $1 AND proposal_hash = $2",
                incident_uid,
                proposal_hash,
            )
        )
        if row is None:
            return None
        parsed = row["parsed_proposal"]
        if parsed is None:
            return row["raw_output"] or ""
        return json.loads(parsed) if isinstance(parsed, str) else parsed


async def connect_act(**kwargs) -> PostgresActRecorder:
    """connect() (same arguments), returning the recorder with the act path's two reads."""
    base = await connect(**kwargs)
    return PostgresActRecorder(base._pool, base._timeout)


class UnavailableRecorder:
    """Placeholder until the Writer is wired in: every call fails, so nothing is ever sent."""

    async def _fail(self, *_, **__):
        raise RecorderUnavailable("no Flight Recorder Writer is configured")

    open_incident = close_incident = record_transition = record_bundle = _fail
    record_proposal = record_gate_decision = record_verification = _fail
    begin_action = complete_action = count_actions = last_action_at = _fail
    recent_actions = get_proposal = _fail


class ReconnectingRecorder:
    """The Writer as the operator holds it: connects when first needed and again after a failure.

    The database may be down when the operator starts, or go down later. Neither stops the
    operator: while there is no connection every call raises RecorderUnavailable, which ends an
    incident Blocked / recorder_unavailable before any request (§17, fail closed). The password
    is read from its mounted file at each connection attempt, so a rotated Secret is picked up.
    asyncpg's own pool re-opens broken connections; a failed call drops the pool so the next call
    starts from a fresh connect.
    """

    _METHODS = frozenset(
        [
            "open_incident",
            "close_incident",
            "record_transition",
            "record_bundle",
            "record_proposal",
            "record_gate_decision",
            "record_verification",
            "begin_action",
            "complete_action",
            "count_actions",
            "last_action_at",
            "recent_actions",
            "get_proposal",
        ]
    )

    def __init__(
        self, *, host: str, port: int, database: str, user: str, password_file: str
    ):
        self._args = {"host": host, "port": port, "database": database, "user": user}
        self._password_file = password_file
        self._inner: PostgresActRecorder | None = None
        self._lock = asyncio.Lock()
        self.up = False  # for the recorder_up metric

    async def _recorder(self) -> PostgresActRecorder:
        async with self._lock:
            if self._inner is None:
                try:
                    password = (
                        await asyncio.to_thread(
                            pathlib.Path(self._password_file).read_text, "utf-8"
                        )
                    ).strip()
                except OSError as e:
                    raise RecorderUnavailable(
                        f"password file: {type(e).__name__}"
                    ) from e
                if not password:
                    raise RecorderUnavailable("password file is empty")
                self._inner = await connect_act(**self._args, password=password)
            return self._inner

    async def _drop(self) -> None:
        async with self._lock:
            inner, self._inner = self._inner, None
        self.up = False
        if inner is not None:
            try:
                await inner._pool.close()
            except Exception:  # noqa: BLE001 - closing a broken pool must not mask the failure
                logger.warning("closing the recorder pool failed")

    def __getattr__(self, name: str):
        if name not in self._METHODS:
            raise AttributeError(name)

        async def call(*args, **kwargs):
            try:
                result = await getattr((await self._recorder()), name)(*args, **kwargs)
            except RecorderUnavailable:
                METRICS.inc(RECORDER_ERRORS)
                await self._drop()
                raise
            self.up = True
            return result

        return call

    async def close(self) -> None:
        await self._drop()

    async def probe(self, timeout_s: float = PROBE_TIMEOUT_S) -> bool:
        """SELECT 1 on a short-lived connection of its own; sets `up` and counts a failure.

        It takes neither the lock nor a pool slot and never drops the pool, so a slow or dead
        database cannot delay the act path through it; the act path's own calls still decide when
        to reconnect. The password is read from its file at each probe, like a connection attempt.
        """
        try:
            password = (
                await asyncio.to_thread(
                    pathlib.Path(self._password_file).read_text, "utf-8"
                )
            ).strip()
            if not password:
                raise RecorderUnavailable("password file is empty")
            await asyncio.wait_for(self._select_one(password, timeout_s), timeout_s)
        except Exception as e:  # noqa: BLE001 - any failure means the recorder is not up
            METRICS.inc(RECORDER_ERRORS)
            self.up = False
            logger.warning("recorder health check failed: %s", type(e).__name__)
            return False
        self.up = True
        return True

    async def _select_one(self, password: str, timeout_s: float) -> None:
        import asyncpg

        conn = await asyncpg.connect(
            **self._args,
            password=password,
            timeout=timeout_s,
            command_timeout=timeout_s,
        )
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()


async def health_loop(
    recorder: ReconnectingRecorder, period_s: float = HEALTH_PERIOD_S
) -> None:
    """Probe the recorder every `period_s` until cancelled. A failed probe never ends the loop."""
    while True:
        await recorder.probe()
        await asyncio.sleep(period_s)
