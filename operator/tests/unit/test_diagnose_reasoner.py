"""Diagnosing with a Reasoner configured (G19, spec §6 transitions 4-6, §17): the redacted bundle
goes out once, the answer is validated before the Gate, and any failure falls back to R1 once."""

import asyncio
import json

import pytest

from fakes import T0
from fakes_act import DEP, NS, FakeMetrics, proposal_doc
from nexus_operator import act as act_module
from nexus_operator import proposal
from nexus_operator.reasoner_client import ReasonerClient, Result
from nexus_operator.redact import CLASSES
from rig import Rig, ended
from test_diagnose import SECRETS, detected, poison
from test_reasoner_client import Server, good_answer, json_response

MODEL = "openai/gpt-oss-120b"


class FakeReasoner:
    """Stands in for ReasonerClient: records what it was given, answers from a script."""

    mode, model = "llm", MODEL

    def __init__(self, result=None, gate=None):
        self.calls: list[dict] = []
        self.result = result
        self.gate = gate  # an asyncio.Event the answer waits for

    async def propose(self, bundle, *, incident_uid, level):
        self.calls.append(
            {
                "bundle": json.loads(json.dumps(bundle)),
                "uid": incident_uid,
                "level": level,
            }
        )
        if self.gate is not None:
            await self.gate.wait()
        return self.result

    async def close(self):
        pass


def answer(doc=None, **meta) -> Result:
    d = doc or proposal_doc("restart_workload")
    return Result(
        True,
        "req-ok",
        doc=d,
        raw=json.dumps({"proposal": d}),
        meta={
            "mode": "llm",
            "model": MODEL,
            "corpusHash": "sha256:idx",
            "promptVersion": "p-1",
            "retrievedChunks": ["rb-gray#1"],
            "latencyMs": 900,
            **meta,
        },
        status=200,
        latency_ms=900,
    )


def failure(reason, **kw) -> Result:
    return Result(
        False, "req-bad", reason, latency_ms=30000 if reason == "timeout" else 40, **kw
    )


def run_until_proposed(rig, ticks=6):
    for _ in range(ticks):
        if rig.status()["phase"] not in ("Detected", "Diagnosing"):
            return
        rig.tick(seconds=1)


def make(reasoner, **kw):
    rig = Rig(
        collector=True, reasoner=reasoner, metrics=FakeMetrics(latencyP95=4.0), **kw
    )
    detected(rig)
    return rig


def rows(rig):
    return rig.rec.proposal_rows


def test_success_sends_the_redacted_bundle_once_and_records_every_field():
    reasoner = FakeReasoner(answer())
    rig = make(reasoner)
    poison(rig)
    run_until_proposed(rig)
    assert rig.status()["phase"] == "Proposed"
    [call] = reasoner.calls
    assert call["uid"] == "uid-inc" and call["level"] == 3
    sent = json.dumps(call["bundle"])
    assert all(secret not in sent for secret in SECRETS)
    assert call["bundle"]["hash"] == rig.status()["evidence"]["bundleHash"]
    assert call["bundle"]["redaction"]["counts"]["token"] >= 1
    [row] = rows(rig).values()
    assert row["mode"] == "llm" and row["model_id"] == MODEL and row["valid"] is True
    assert row["index_hash"] == "sha256:idx" and row["chunks"] == ["rb-gray#1"]
    assert row["prompt_version"] == "p-1" and row["fallback_used"] is False
    assert (
        row["request_id"] == "req-ok"
        and row["raw_output"].startswith("{")
        and row["latency_ms"] == 900
    )
    assert row["reject_reason"] is None
    s = rig.status()
    assert s["proposal"]["hash"] in rows(rig)
    assert s["reasoner"] == {
        "mode": "llm", "modelId": MODEL, "promptVersion": "p-1", "indexHash": "sha256:idx",
        "chunks": ["rb-gray#1"], "latencyMs": 900, "requestId": "req-ok", "fallbackUsed": False,
    }  # fmt: skip


def test_without_an_endpoint_nothing_changes():
    rig = make(None)
    run_until_proposed(rig)
    [row] = rows(rig).values()
    assert (
        row["mode"] == "rules"
        and row["fallback_used"] is False
        and row["request_id"] is None
    )
    assert rig.status()["reasoner"] == {"mode": "rules", "fallbackUsed": False}


@pytest.mark.parametrize(
    "result, stored_reason",
    [
        (failure("timeout"), "timeout"),
        (failure("http_502"), "http_502"),
        (failure("http_500"), "http_500"),
        (failure("transport"), "transport"),
        (failure("unparseable", raw="<html>"), "unparseable"),
        (
            failure("rejected", raw='{"action": 7}', errors=["action.id must be a string"],
                    meta={"rejection": "invalid_proposal", "mode": "llm", "model": MODEL}),
            "rejected: action.id must be a string",
        ),
        (
            failure("schema_invalid", raw='{"proposal": {}}', errors=["schema must be proposal/v1"]),
            "schema_invalid: schema must be proposal/v1",
        ),
    ],
)  # fmt: skip
def test_any_failure_falls_back_to_r1_once_with_fallback_used(result, stored_reason):
    reasoner = FakeReasoner(result)
    rig = make(reasoner)
    run_until_proposed(rig)
    assert rig.status()["phase"] == "Proposed"
    assert len(reasoner.calls) == 1
    failed, used = sorted(rows(rig).values(), key=lambda r: r["fallback_used"])
    assert failed["valid"] is False and failed["fallback_used"] is False
    assert (
        failed["reject_reason"] == stored_reason and failed["request_id"] == "req-bad"
    )
    assert failed["raw_output"] == result.raw  # the 422's raw output is recorded
    assert (
        used["mode"] == "rules"
        and used["fallback_used"] is True
        and used["valid"] is True
    )
    assert used["request_id"] == "req-bad" and used["model_id"] is None
    assert (
        used["parsed"]["action"]["id"] == "restart_workload"
    )  # R1 on gray-failure evidence
    s = rig.status()
    assert s["reasoner"]["fallbackUsed"] is True and s["reasoner"]["mode"] == "rules"
    assert (
        s["proposal"]["hash"] in rows(rig)
        and rows(rig)[s["proposal"]["hash"]]["fallback_used"]
    )


def test_the_422_reason_and_raw_output_are_both_in_the_failed_row():
    result = failure("rejected", raw='{"x": 1}', errors=["model output is not JSON"],
                     meta={"rejection": "unparseable"})  # fmt: skip
    rig = make(FakeReasoner(result))
    run_until_proposed(rig)
    failed = next(r for r in rows(rig).values() if not r["fallback_used"])
    assert failed["raw_output"] == '{"x": 1}'
    assert failed["reject_reason"] == "rejected: model output is not JSON"


def test_invalid_twice_is_blocked(monkeypatch):
    reasoner = FakeReasoner(failure("schema_invalid", errors=["bad"], raw="{}"))
    rig = make(reasoner)
    monkeypatch.setattr(
        act_module.rules, "propose", lambda b: {"schema": "proposal/v1"}
    )
    run_until_proposed(rig)
    assert ended(rig) == ("Blocked", "schema_invalid")
    assert len(reasoner.calls) == 1
    used = next(r for r in rows(rig).values() if r["fallback_used"])
    assert used["valid"] is False and used["reject_reason"]
    assert rig.api.requests == []


def test_a_reasoner_that_returns_nothing_usable_never_reaches_the_gate_unvalidated(
    monkeypatch,
):
    bad = answer()
    bad.doc = {
        "schema": "proposal/v1",
        "action": {"id": "rm -rf"},
    }  # ok flag lies; the doc is junk
    rig = make(FakeReasoner(bad))
    monkeypatch.setattr(act_module.rules, "propose", lambda b: {"nope": 1})
    run_until_proposed(rig)
    assert ended(rig) == ("Blocked", "schema_invalid")


def test_an_out_of_envelope_proposal_reaches_the_gate_and_is_blocked_there():
    rig = make(FakeReasoner(answer(proposal_doc("scale_deployment", 40))))
    rig.run()
    assert ended(rig) == ("Blocked", "bounds_exceeded")
    assert rig.api.requests == []


def test_the_reasoner_is_asked_once_even_when_the_recorder_flaps():
    reasoner = FakeReasoner(answer())
    rig = make(reasoner)
    rig.tick()  # Diagnosing
    rig.rec.fail_all = True
    for _ in range(3):
        rig.tick(seconds=1)
    assert rig.status()["phase"] == "Diagnosing"
    rig.rec.fail_all = False
    run_until_proposed(rig)
    assert rig.status()["phase"] == "Proposed"
    assert len(reasoner.calls) == 1


def test_a_slow_reasoner_does_not_hold_up_the_tick():
    gate = asyncio.Event()
    reasoner = FakeReasoner(answer(), gate=gate)
    rig = make(reasoner)
    rig.tick()  # Detected -> Diagnosing
    rig.tick()  # collects, starts the call, returns
    for _ in range(3):
        rig.tick(seconds=1)
    assert rig.status()["phase"] == "Diagnosing" and len(reasoner.calls) == 1
    rig.loop.call_soon(gate.set)
    run_until_proposed(rig)
    assert rig.status()["phase"] == "Proposed" and len(reasoner.calls) == 1


def test_a_call_that_never_returns_ends_escalated_at_the_diagnosing_timeout():
    reasoner = FakeReasoner(answer(), gate=asyncio.Event())  # never set
    rig = make(reasoner)
    rig.tick()
    rig.tick()
    rig.tick(seconds=61)
    assert ended(rig) == ("Escalated", "reasoner_unavailable")


def test_a_crashing_client_is_a_transport_failure_and_falls_back():
    class Crash(FakeReasoner):
        async def propose(self, bundle, *, incident_uid, level):
            raise RuntimeError("bug")

    rig = make(Crash())
    run_until_proposed(rig)
    assert (
        rig.status()["phase"] == "Proposed"
        and rig.status()["reasoner"]["fallbackUsed"] is True
    )


def test_the_recorder_down_blocks_after_the_answer_not_before():
    reasoner = FakeReasoner(answer())
    rig = make(reasoner)
    rig.tick()
    rig.tick()
    rig.rec.fail_all = True
    rig.tick(seconds=61)
    assert ended(rig) == ("Blocked", "recorder_unavailable")
    assert rig.api.requests == []


# ---------------------------------------------------------------- real HTTP end to end
def with_real_client(handler, test):
    async def start():
        server = Server(handler)
        return server, await server.start()

    async def stop(server):
        await server.stop()

    # the fake Reasoner lives on the rig's loop, so the client's request is served between ticks
    holder = {}

    def build(rig_loop):
        server, url = rig_loop.run_until_complete(start())
        holder["server"] = server
        return ReasonerClient(url, mode="llm", model=MODEL, timeout_s=2)

    return holder, build, stop


def tick_real(rig, n=40):
    for _ in range(n):
        rig.call(asyncio.sleep(0.02))
        if rig.status()["phase"] not in ("Detected", "Diagnosing"):
            return
        rig.tick(seconds=1)


def test_over_http_a_good_answer_becomes_the_proposal():
    async def good(request, body):
        return json_response(good_answer())

    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    server = Server(good)
    url = rig.call(server.start())
    rig.act.reasoner = ReasonerClient(url, mode="llm", model=MODEL, timeout_s=2)
    detected(rig)
    tick_real(rig)
    assert rig.status()["phase"] == "Proposed" and len(server.requests) == 1
    sent = json.loads(server.requests[0][1])
    assert sent["bundle"]["hash"] == rig.status()["evidence"]["bundleHash"]
    [row] = rows(rig).values()
    assert (
        row["request_id"] == server.requests[0][0]["X-Request-Id"]
        and row["mode"] == "llm"
    )
    rig.call(rig.act.reasoner.close())
    rig.call(server.stop())


def test_over_http_a_timeout_falls_back():
    async def slow(request, body):
        await asyncio.sleep(2)
        return json_response(good_answer())

    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    server = Server(slow)
    url = rig.call(server.start())
    rig.act.reasoner = ReasonerClient(url, mode="llm", model=MODEL, timeout_s=0.1)
    detected(rig)
    tick_real(rig)
    assert (
        rig.status()["phase"] == "Proposed"
        and rig.status()["reasoner"]["fallbackUsed"] is True
    )
    failed = next(r for r in rows(rig).values() if not r["fallback_used"])
    assert failed["reject_reason"] == "timeout"
    rig.call(rig.act.reasoner.close())
    rig.call(server.stop())


def test_an_unredacted_bundle_is_refused_and_nothing_is_sent(monkeypatch):
    """Redaction switched off: the bundle claims counts and carries a valid hash, but a second
    pass finds the secrets, so the client refuses, the Reasoner sees no request, and the Incident
    is escalated with nothing stored."""

    async def good(request, body):
        return json_response(good_answer())

    monkeypatch.setattr(
        act_module, "redact_bundle", lambda b: (b, {c: 0 for c in CLASSES})
    )
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    poison(rig)
    server = Server(good)
    url = rig.call(server.start())
    rig.act.reasoner = ReasonerClient(url, mode="llm", model=MODEL, timeout_s=2)
    detected(rig)
    tick_real(rig)
    assert ended(rig) == ("Escalated", "evidence_error")
    assert server.requests == []
    assert rig.rec.bundles == {} and rows(rig) == {}
    blob = json.dumps([rig.rec.transitions, rig.api.incidents], default=str)
    assert all(secret not in blob for secret in SECRETS)
    rig.call(rig.act.reasoner.close())
    rig.call(server.stop())


def test_metrics_count_the_calls_and_the_fallbacks():
    from nexus_operator import metrics

    calls = lambda r: metrics.METRICS.value(metrics.REASONER_CALLS, result=r)
    before = (
        calls("ok"),
        calls("timeout"),
        metrics.METRICS.value(metrics.REASONER_FALLBACKS),
    )
    for result in (answer(), failure("timeout")):
        rig = make(FakeReasoner(result))
        run_until_proposed(rig)
    assert calls("ok") == before[0] + 1 and calls("timeout") == before[1] + 1
    assert metrics.METRICS.value(metrics.REASONER_FALLBACKS) == before[2] + 1


def test_check_the_target_names_in_the_fixture():
    assert (NS, DEP, T0) and proposal.validate(proposal_doc()) == []
