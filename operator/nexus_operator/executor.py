"""The Action Executor (spec §4, §14): the only code that sends a mutating request.

One request per call, in the write-ahead order of §14, which recorder.execute_recorded runs:
  1. the caller has already put the idempotency key in status.execution (the pending record);
  2. begin_action commits the `actions` row — if that fails, NO request is sent;
  3. the request goes to the API server, which returns an Audit-Id even for a denial;
  4. complete_action stores the Audit-Id, the status and the outcome. A failure here does not undo
     the request: `unrecorded` is set so the caller keeps it in status and retries (§17).
An idempotency key that is already recorded is never sent again (`replayed`): the caller decides
from the real cluster state, and a request that did not take effect goes out under a new key.

The executor never decides anything. The outcomes it classifies are only labels for what the
Trusted Enforcement Plane answered: RBAC and Kyverno are both a 403 (told apart by the message),
the PodDisruptionBudget is a 429, and a Kyverno webhook that cannot be reached is a 5xx.
"""

import dataclasses

from .mutations import Mutations
from .recorder import (
    ActionIntent,
    ActRecorder,
    ApiResult,
    RecorderUnavailable,
    execute_recorded,
)


@dataclasses.dataclass
class Executed:
    result: str  # admitted | denied | error | replayed | recorder_unavailable
    reason: str | None = None  # reason code for a refusal
    audit_id: str | None = None
    http_status: int | None = None
    detail: str = ""
    outcome: str = ""  # the text stored in actions.result
    unrecorded: bool = False  # the completion write failed; retry it from status


def classify(out: ApiResult) -> tuple[str, str | None]:
    """(result, reason) for a response of the Trusted Enforcement Plane."""
    if out.ok:
        return "admitted", None
    text = out.message.lower()
    if out.status == 403:
        return "denied", "admission_denied" if "webhook" in text else "rbac_denied"
    if out.status == 429:
        return "denied", "pdb_denied"
    if out.status >= 500 and "webhook" in text:
        return "denied", "admission_denied"  # Kyverno unreachable: fail closed
    return "error", "execution_timeout"


class Executor:
    def __init__(self, mutations: Mutations, recorder: ActRecorder, clock) -> None:
        self.mutations = mutations
        self.recorder = recorder
        self.clock = clock

    async def run(self, intent: ActionIntent, send) -> Executed:
        """Write the intent, then await `send()` (an ApiResult), then complete the row."""
        try:
            done = await execute_recorded(self.recorder, intent, send, self.clock)
        except RecorderUnavailable as e:
            return Executed(
                "recorder_unavailable", "recorder_unavailable", detail=str(e)
            )
        if done.replayed:
            return Executed("replayed", detail="idempotency key already recorded")
        if done.result is None:
            return Executed(
                "error",
                "execution_timeout",
                detail=done.transport_error or "",
                outcome=f"transport_error: {done.transport_error}",
                unrecorded=done.completion_pending,
            )
        result, reason = classify(done.result)
        outcome = "applied" if done.result.ok else f"denied: {done.result.message}"
        return Executed(
            result,
            reason,
            done.result.audit_id,
            done.result.status,
            done.result.message,
            outcome,
            done.completion_pending,
        )

    async def scale(
        self, intent: ActionIntent, namespace: str, name: str, replicas: int
    ):
        return await self.run(
            intent, lambda: self.mutations.patch_scale(namespace, name, replicas)
        )

    async def evict(self, intent: ActionIntent, namespace: str, pod: str, uid: str):
        return await self.run(intent, lambda: self.mutations.evict(namespace, pod, uid))
