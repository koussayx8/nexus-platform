"""What the act path needs from the recorder beyond the Writer (spec §4, §9, §14; ADR-031).

The Writer's `count_actions` counts rows per target, and the breaker counts executed actions:
a restart that evicts three pods is three rows and one action, a denied request changed nothing,
a compensation must stay possible, and the 6-per-hour limit spans every target. So the act path
reads the recent rows once and counts them itself (breaker.py). The Gate also reads the stored
proposal by its hash. Both are added here, inside the package, without touching the Writer:

    ActRecorder            the Recorder protocol plus recent_actions and get_proposal
    PostgresActRecorder    PostgresRecorder plus those two reads (one SELECT each)
    UnavailableRecorder    every call raises RecorderUnavailable: used until the Writer is wired in
                           (the runtime image has no asyncpg pin yet, ADR-031), so every mutation
                           ends Blocked / recorder_unavailable
"""

import dataclasses
import datetime
import json
from typing import Any, Protocol

from .writer import PostgresRecorder, Recorder, RecorderUnavailable, connect


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

    async def get_proposal(self, proposal_hash: str) -> Any | None:
        """The stored parsed proposal; the raw output when it did not parse; None if no row."""
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

    async def get_proposal(self, proposal_hash: str) -> Any | None:
        row = await self._run(
            self._pool.fetchrow(
                "SELECT parsed_proposal, raw_output FROM proposals WHERE proposal_hash = $1",
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
