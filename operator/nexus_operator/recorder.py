"""The Flight Recorder Writer interface the act path codes against (spec §14; task 1 provides it).

The act path needs five things from the recorder, and nothing here knows PostgreSQL:
  write_intent     commit the `actions` row with its intent BEFORE the request (write-ahead rule)
  complete_action  complete that row with the returned Audit-Id, the HTTP status and the result
  record_*         idempotent appends: transitions, gate decisions, verifications
  actions_since    the executed actions the Circuit Breaker and the cool-down count (§4)
  get_proposal     the raw proposal/v1 document stored for an incident (§14 `proposals`)
Any failure raises RecorderUnavailable. The executor treats a failed intent write as "no request
is sent" (`Blocked`, `recorder_unavailable`): NEXUS never mutates what it cannot first record.
"""

import dataclasses
import datetime
from typing import Protocol


class RecorderUnavailable(Exception):
    """The Flight Recorder DB cannot take the write or answer the read."""


@dataclasses.dataclass(frozen=True)
class ActionRow:
    """One row of `actions` (§14), keyed by idempotency_key."""

    idempotency_key: str
    incident_uid: str
    action_id: str
    parameters: dict
    previous_state: dict
    intent_time: datetime.datetime
    target_namespace: str
    target_name: str
    request_time: datetime.datetime | None = None
    audit_id: str | None = None
    http_status: int | None = None
    result: str | None = None  # admitted | denied | error | dry_run
    action_class: str = "executed"  # executed | compensation


class Recorder(Protocol):
    async def write_intent(self, row: ActionRow) -> None: ...

    async def complete_action(
        self,
        idempotency_key: str,
        *,
        audit_id: str | None,
        http_status: int | None,
        result: str,
        request_time: datetime.datetime,
    ) -> None: ...

    async def record_transition(
        self,
        incident_uid: str,
        seq: int,
        *,
        from_phase: str | None,
        to_phase: str,
        stage: str,
        reason: str | None,
        at: datetime.datetime,
    ) -> None: ...

    async def record_gate_decision(
        self,
        incident_uid: str,
        stage: int,
        verdict: str,
        detail: str,
        at: datetime.datetime,
    ) -> None: ...

    async def record_verification(
        self,
        incident_uid: str,
        criteria_id: str,
        observations: dict,
        result: str,
        at: datetime.datetime,
    ) -> None: ...

    async def actions_since(self, since: datetime.datetime) -> list[ActionRow]: ...

    async def get_proposal(self, incident_uid: str) -> dict | None: ...


class UnavailableRecorder:
    """Placeholder until the Writer is wired: every call fails, so every mutation is blocked."""

    async def _fail(self, *_, **__):
        raise RecorderUnavailable("no Flight Recorder Writer is configured")

    write_intent = complete_action = record_transition = _fail
    record_gate_decision = record_verification = actions_since = get_proposal = _fail
