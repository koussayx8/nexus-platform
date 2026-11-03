"""The write-ahead sequence (spec §14, I14): intent row, then the request, then the completion.

    execute_recorded(recorder, intent, send)

1. `begin_action` commits the intent. If that fails the exception `RecorderUnavailable` propagates
   and `send` is never called: the caller ends the incident `Blocked` / `recorder_unavailable`.
2. `send` makes the Kubernetes request and returns an `ApiResult` (status and `Audit-Id`).
3. `complete_action` stores the Audit-Id and status. If that write fails the action already
   happened and is audited, so the outcome is returned with `completion_pending` set instead of
   raising (§17: "record later; completion retried from status"); reconciliation flags the
   incomplete row, which is not an unexplained request.

A retried call with the same idempotency key does not send twice: when the intent row already
exists the request may already have gone out, so `send` is not called and `replayed` is set.
The caller decides from the recorded row, never by re-sending.
"""

import dataclasses
from collections.abc import Awaitable, Callable

import aiohttp

from ..model import now
from .audit import ApiResult
from .writer import ActionIntent, Recorder, RecorderUnavailable


@dataclasses.dataclass(frozen=True)
class ActionOutcome:
    result: ApiResult | None  # None: replayed, or the transport failed
    replayed: bool = False
    transport_error: str | None = None
    completion_pending: bool = False


async def execute_recorded(
    recorder: Recorder,
    intent: ActionIntent,
    send: Callable[[], Awaitable[ApiResult]],
    clock=now,
) -> ActionOutcome:
    if not await recorder.begin_action(
        intent
    ):  # may raise RecorderUnavailable: no request
        return ActionOutcome(result=None, replayed=True)

    request_at = clock()
    try:
        result = await send()
    except (aiohttp.ClientError, OSError) as e:  # transport failure, incl. timeouts
        error = f"{type(e).__name__}: {str(e)[:200]}"
        return ActionOutcome(
            result=None,
            transport_error=error,
            completion_pending=not await _complete(
                recorder, intent, request_at, None, None, f"transport_error: {error}"
            ),
        )

    outcome = "applied" if result.ok else f"denied: {result.message}"
    return ActionOutcome(
        result=result,
        completion_pending=not await _complete(
            recorder, intent, request_at, result.audit_id, result.status, outcome
        ),
    )


async def _complete(recorder, intent, request_at, audit_id, status, outcome) -> bool:
    try:
        await recorder.complete_action(
            intent.idempotency_key, request_at, audit_id, status, outcome
        )
    except RecorderUnavailable:
        return False
    return True
