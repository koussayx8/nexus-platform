"""The Reasoner Client against a fake Reasoner over real HTTP (aiohttp on 127.0.0.1): success,
timeout, 422, provider errors, unparseable and invalid answers, and the refusal to send a bundle
that is not redacted. The Reasoner is untrusted: every answer is checked, none is believed."""

import asyncio
import copy
import json

import pytest
from aiohttp import web

from fakes_act import proposal_doc
from nexus_operator import proposal
from nexus_operator.reasoner_client import (
    MAX_RESPONSE_BYTES,
    ReasonerClient,
    check_redacted,
)
from nexus_operator.redact import redact_bundle

TARGET = {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"}
PLANTED = "ghp_" + "Q1w2E3r4T5" * 4


def make_bundle(extra_log: str = "all quiet") -> dict:
    raw = {
        "schema": "evidence/v1",
        "incident": {"uid": "u1", "target": TARGET},
        "workload": {
            "desired": 2,
            "ready": 2,
            "updated": 2,
            "rolloutInProgress": False,
        },
        "logs": {"current": [extra_log], "previous": []},
        "metrics": {"zscores": {"latencyP95": 4.0}},
    }
    redacted, counts = redact_bundle(raw)
    bundle = redacted | {"redaction": {"counts": counts}}
    bundle["hash"] = proposal.hash_of(bundle)
    return bundle


def good_answer(**meta) -> dict:
    return {
        "proposal": proposal_doc("restart_workload"),
        "metadata": {
            "mode": "llm",
            "model": "openai/gpt-oss-120b",
            "corpusHash": "sha256:idx",
            "promptVersion": "p-1",
            "retrievedChunks": ["rb-gray#1"],
            "latencyMs": 812,
            **meta,
        },
    }


class Server:
    """A fake Reasoner: `handler(request, body)` returns a web.Response; requests are recorded."""

    def __init__(self, handler):
        self.handler, self.requests = handler, []

    async def start(self) -> str:
        async def route(request: web.Request) -> web.Response:
            body = await request.read()
            self.requests.append((request.headers.copy(), body))
            return await self.handler(request, body)

        app = web.Application()
        app.router.add_post("/v1/propose", route)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        await self.runner.cleanup()


def run(handler, call, timeout_s=2.0, **kw):
    """call(client, server) runs against a started fake Reasoner."""

    async def go():
        server = Server(handler)
        url = await server.start()
        client = ReasonerClient(
            url, mode="llm", model="openai/gpt-oss-120b", timeout_s=timeout_s, **kw
        )
        try:
            return await call(client, server)
        finally:
            await client.close()
            await server.stop()

    return asyncio.run(go())


def json_response(body, status=200):
    return web.Response(
        status=status, text=json.dumps(body), content_type="application/json"
    )


async def ok(request, body):
    return json_response(good_answer())


def test_success_sends_only_the_redacted_bundle_and_a_context():
    bundle = make_bundle()

    async def call(client, server):
        result = await client.propose(bundle, incident_uid="u1", level=3)
        [(headers, body)] = server.requests
        return result, headers, json.loads(body)

    result, headers, sent = run(ok, call, effort="low")
    assert result.ok and result.reason is None
    assert proposal.validate(result.doc) == []
    assert set(sent) == {"bundle", "context"}
    assert sent["bundle"] == bundle
    assert sent["context"] == {
        "incidentUid": "u1",
        "level": "L3",
        "catalogueVersion": "1",
        "mode": "llm",
        "model": "openai/gpt-oss-120b",
        "reasoningEffort": "low",
    }
    assert headers["X-Request-Id"] == result.request_id
    assert (
        result.meta["model"] == "openai/gpt-oss-120b"
        and result.meta["corpusHash"] == "sha256:idx"
    )
    assert (
        result.meta["retrievedChunks"] == ["rb-gray#1"]
        and result.latency_ms is not None
    )


def test_a_secret_in_the_bundle_is_redacted_before_it_can_be_sent():
    bundle = make_bundle(f"token {PLANTED}")
    assert PLANTED not in json.dumps(bundle)

    async def call(client, server):
        await client.propose(bundle, incident_uid="u1", level=3)
        return server.requests[0][1].decode()

    assert PLANTED not in run(ok, call)


def test_timeout_is_one_attempt_with_no_retry():
    async def slow(request, body):
        await asyncio.sleep(1.0)
        return json_response(good_answer())

    async def call(client, server):
        result = await client.propose(make_bundle(), incident_uid="u1", level=3)
        await asyncio.sleep(0.2)
        return result, len(server.requests)

    result, n = run(slow, call, timeout_s=0.1)
    assert (result.ok, result.reason, n) == (False, "timeout", 1)
    assert result.doc is None


def test_422_keeps_the_reasons_and_the_models_raw_output():
    async def refuse(request, body):
        return json_response(
            {
                "error": "invalid_proposal",
                "detail": "action.id must be a string",
                "raw": '{"action": 7}',
                "metadata": {"mode": "llm", "model": "m", "retrievedChunks": ["rb#1"]},
            },
            422,
        )

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(refuse, call)
    assert (r.ok, r.reason, r.status) == (False, "rejected", 422)
    assert r.raw == '{"action": 7}' and r.errors == ["action.id must be a string"]
    assert r.meta["rejection"] == "invalid_proposal" and r.meta["model"] == "m"


def test_422_without_a_readable_body_is_still_a_rejection():
    async def refuse(request, body):
        return web.Response(status=422, text="nope")

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(refuse, call)
    assert (r.ok, r.reason, r.raw) == (False, "rejected", None)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_provider_errors_are_failures_with_the_status(status):
    async def fail(request, body):
        return json_response({"error": "model_unavailable"}, status)

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(fail, call)
    assert (r.ok, r.reason, r.status) == (False, f"http_{status}", status)


def test_a_schema_invalid_answer_is_refused_and_kept_raw():
    bad = good_answer()
    bad["proposal"]["action"]["parameters"] = {"image": "evil:latest"}

    async def answer(request, body):
        return json_response(bad)

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(answer, call)
    assert (r.ok, r.reason) == (False, "schema_invalid")
    assert (
        r.errors and "evil:latest" in r.raw
    )  # kept for the Flight Recorder, never acted on


def test_an_answer_without_a_proposal_is_refused():
    async def answer(request, body):
        return json_response({"metadata": {}})

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(answer, call)
    assert (r.ok, r.reason) == (False, "schema_invalid")


def test_unparseable_and_oversized_answers_are_refused():
    async def text(request, body):
        return web.Response(text="<html>not json</html>")

    async def huge(request, body):
        return web.Response(text="x" * (MAX_RESPONSE_BYTES + 10))

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    assert run(text, call).reason == "unparseable"
    assert run(huge, call).reason == "unparseable"


def test_an_unreachable_reasoner_is_a_transport_failure():
    async def go():
        client = ReasonerClient(
            "http://127.0.0.1:1", mode="llm", model="m", timeout_s=1
        )
        try:
            return await client.propose(make_bundle(), incident_uid="u1", level=3)
        finally:
            await client.close()

    r = asyncio.run(go())
    assert (r.ok, r.reason) == (False, "transport")


def test_an_out_of_envelope_proposal_passes_the_client_to_reach_the_gate():
    doc = proposal_doc("scale_deployment", 40)

    async def answer(request, body):
        return json_response({"proposal": doc, "metadata": {}})

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(answer, call)
    assert r.ok and r.doc["action"]["parameters"] == {"replicas": 40}


def test_the_metadata_is_reduced_to_short_scalars():
    meta = {
        "model": "m" * 500,
        "latencyMs": 5,
        "evil": {"nested": "x"},
        "retrievedChunks": ["a", 3, {"b": 1}] + ["c"] * 40,
        "promptVersion": ["not", "a", "string"],
    }

    async def answer(request, body):
        return json_response(good_answer(**meta))

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(answer, call)
    assert len(r.meta["model"]) == 200 and r.meta["latencyMs"] == 5
    assert "evil" not in r.meta and "promptVersion" not in r.meta
    assert len(r.meta["retrievedChunks"]) == 20 and set(r.meta["retrievedChunks"]) <= {
        "a",
        "c",
    }


# ---------------------------------------------------------------- an unredacted bundle is refused
def forged(secret_in_log: str) -> dict:
    """A bundle that CLAIMS to be redacted - counts and a correct hash - but still holds a secret."""
    raw = make_bundle()
    raw["logs"] = {"current": [secret_in_log], "previous": []}
    raw["hash"] = proposal.hash_of({k: v for k, v in raw.items() if k != "hash"})
    return raw


def tampered() -> dict:
    b = make_bundle()
    b["logs"]["current"].append(f"token {PLANTED}")  # edited after hashing
    return b


def no_counts() -> dict:
    b = make_bundle()
    del b["redaction"]
    return b


def wrong_schema() -> dict:
    return make_bundle() | {"schema": "evidence/v2"}


@pytest.mark.parametrize(
    "bundle, why",
    [
        (forged(f"token {PLANTED}"), "unredacted content found: token"),
        (forged("mail alice.admin@example.com"), "unredacted content found: email"),
        (forged("peer 10.42.7.19"), "unredacted content found: ip"),
        (tampered(), "hash does not match"),
        (no_counts(), "redaction.counts missing"),
        (wrong_schema(), "not an evidence/v1 bundle"),
        ({"schema": "evidence/v1"}, "redaction.counts missing"),
        ("a string", "not an evidence/v1 bundle"),
    ],
)
def test_an_unredacted_bundle_is_refused_and_nothing_is_sent(bundle, why):
    async def call(client, server):
        result = await client.propose(bundle, incident_uid="u1", level=3)
        return result, len(server.requests)

    result, sent = run(ok, call)
    assert (result.ok, result.reason, sent) == (False, "not_redacted", 0)
    assert why in result.errors[0]
    assert (
        PLANTED not in result.errors[0] and "alice" not in result.errors[0]
    )  # never repeats a value


def test_the_operators_own_redacted_bundle_passes_the_check():
    assert (
        check_redacted(
            make_bundle(f"token {PLANTED} for alice.admin@example.com at 10.1.2.3")
        )
        is None
    )
    b = make_bundle()
    assert check_redacted(copy.deepcopy(b)) is None
