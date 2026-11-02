"""The Action Executor (spec §4, §14): the only code that sends a mutating request.

One request per call, in the write-ahead order of §14:
  1. the caller has already put the idempotency key in status.execution (the pending record);
  2. write_intent commits the `actions` row — if that fails, NO request is sent;
  3. the request goes to the API server, which returns an Audit-Id even for a denial;
  4. complete_action stores the Audit-Id, the status and the result. A failure here does not undo
     the request: the result is returned so the caller keeps it in status and retries (§17).
write_intent is idempotent on the key, so a resumed request rewrites nothing.

The executor never decides anything. The four outcomes it classifies are only labels for what the
Trusted Enforcement Plane answered: RBAC and Kyverno are both a 403 (told apart by the message),
the PodDisruptionBudget is a 429, and a Kyverno webhook that cannot be reached is a 5xx.
"""

import dataclasses
import datetime

from .kube import TRANSPORT_ERRORS, ApiError, KubeApi, Outcome
from .recorder import ActionRow, Recorder, RecorderUnavailable


@dataclasses.dataclass
class Executed:
    result: str  # admitted | denied | error | recorder_unavailable
    reason: str | None = None  # reason code for a refusal
    audit_id: str | None = None
    http_status: int | None = None
    detail: str = ""
    unrecorded: bool = False  # the completion write failed; retry it from status


def classify(out: Outcome) -> tuple[str, str | None]:
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
    def __init__(self, api: KubeApi, recorder: Recorder, clock) -> None:
        self.api = api
        self.recorder = recorder
        self.clock = clock

    async def run(self, row: ActionRow, request) -> Executed:
        """Write the intent, then await `request()` (an Outcome), then complete the row."""
        try:
            await self.recorder.write_intent(row)
        except RecorderUnavailable as e:
            return Executed(
                "recorder_unavailable", "recorder_unavailable", detail=str(e)
            )
        sent_at = self.clock()
        try:
            out: Outcome = await request()
        except (ApiError, *TRANSPORT_ERRORS) as e:
            done = Executed(
                "error", "execution_timeout", detail=f"{type(e).__name__}: {e}"
            )
            await self._complete(row, done, sent_at)
            return done
        result, reason = classify(out)
        done = Executed(result, reason, out.audit_id, out.status, out.message)
        await self._complete(row, done, sent_at)
        return done

    async def _complete(
        self, row: ActionRow, done: Executed, sent_at: datetime.datetime
    ):
        try:
            await self.recorder.complete_action(
                row.idempotency_key,
                audit_id=done.audit_id,
                http_status=done.http_status,
                result=done.result,
                request_time=sent_at,
            )
        except RecorderUnavailable:
            done.unrecorded = True

    async def scale(self, row: ActionRow, namespace: str, name: str, replicas: int):
        return await self.run(
            row, lambda: self.api.patch_scale(namespace, name, replicas)
        )

    async def evict(self, row: ActionRow, namespace: str, pod: str, uid: str):
        return await self.run(row, lambda: self.api.evict(namespace, pod, uid))
