"""The write-ahead sequence (spec §14, I14): intent before the request, fail closed, Audit-Id kept.
In-memory recorder; the database behaviour is in test_recorder_writer.py."""

import asyncio

import pytest

from nexus_operator.recorder import (
    ActionIntent,
    ApiResult,
    RecorderUnavailable,
    execute_recorded,
)

INTENT = ActionIntent("k-1", "u-1", "scale-out", {"replicas": 2}, "runtime")


class MemRecorder:
    def __init__(self, fail_begin=False, fail_complete=False):
        self.log = []
        self.rows = {}
        self.fail_begin, self.fail_complete = fail_begin, fail_complete

    async def begin_action(self, intent):
        if self.fail_begin:
            raise RecorderUnavailable("down")
        if intent.idempotency_key in self.rows:
            return False
        self.log.append("intent")
        self.rows[intent.idempotency_key] = {}
        return True

    async def complete_action(self, key, request_at, audit_id, http_status, result):
        if self.fail_complete:
            raise RecorderUnavailable("down")
        self.log.append("complete")
        self.rows[key] = {"audit_id": audit_id, "status": http_status, "result": result}
        return True


def execute(rec, send):
    return asyncio.run(execute_recorded(rec, INTENT, send))


def test_intent_is_recorded_before_the_request_and_the_audit_id_after():
    rec = MemRecorder()

    async def send():
        rec.log.append("request")
        return ApiResult(200, "audit-1", {"kind": "Scale"})

    out = execute(rec, send)
    assert rec.log == ["intent", "request", "complete"]
    assert rec.rows["k-1"] == {
        "audit_id": "audit-1",
        "status": 200,
        "result": "applied",
    }
    assert out.result.audit_id == "audit-1" and not out.completion_pending


def test_recorder_down_means_no_request():
    rec = MemRecorder(fail_begin=True)
    sent = []

    async def send():
        sent.append(1)
        return ApiResult(200, "a", None)

    with pytest.raises(RecorderUnavailable):
        execute(rec, send)
    assert sent == []


def test_a_denial_is_recorded_with_its_audit_id_and_message():
    rec = MemRecorder()

    async def send():
        return ApiResult(
            403, "audit-2", {"message": "admission webhook denied: autonomy floor"}
        )

    out = execute(rec, send)
    assert rec.rows["k-1"]["status"] == 403
    assert rec.rows["k-1"]["audit_id"] == "audit-2"
    assert rec.rows["k-1"]["result"].startswith("denied: admission webhook denied")
    assert not out.result.ok


def test_a_replayed_key_never_sends_twice():
    rec = MemRecorder()
    sent = []

    async def send():
        sent.append(1)
        return ApiResult(200, "a", None)

    execute(rec, send)
    out = execute(rec, send)
    assert sent == [1] and out.replayed and out.result is None


def test_completion_failure_after_the_request_is_pending_not_an_error():
    rec = MemRecorder(fail_complete=True)

    async def send():
        return ApiResult(200, "audit-3", None)

    out = execute(rec, send)
    assert out.completion_pending and out.result.audit_id == "audit-3"


def test_a_transport_failure_is_recorded_as_an_outcome():
    rec = MemRecorder()

    async def send():
        raise OSError("connection reset")

    out = execute(rec, send)
    assert out.result is None and "connection reset" in out.transport_error
    assert rec.rows["k-1"]["status"] is None
    assert rec.rows["k-1"]["result"].startswith("transport_error")
