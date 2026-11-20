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
        "schema": "evidence/v1.1",
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
    assert r.errors == ["model_unavailable"]


def test_a_provider_refusal_keeps_the_reasoners_error_and_detail():
    """2026-11-17: Groq refused every R3 call with 403; the Reasoner said so in a 502 body,
    and the operator used to drop it."""

    async def fail(request, body):
        return json_response({"error": "model_unavailable", "detail": "http_403"}, 502)

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(fail, call)
    assert (r.reason, r.errors) == ("http_502", ["model_unavailable: http_403"])


@pytest.mark.parametrize(
    "raw, errors",
    [
        ("<html>bad gateway</html>", []),
        (json.dumps(["model_unavailable"]), []),
        (json.dumps({"error": 7, "detail": None}), []),
        (json.dumps({"detail": "http_429"}), ["http_429"]),
        (
            json.dumps({"error": "x\nInjected: $(rm)", "detail": "d" * 500}),
            ["x?Injected: ??rm?: " + "d" * 100],
        ),
    ],
)
def test_the_error_body_is_untrusted_short_and_plain(raw, errors):
    async def fail(request, body):
        return web.Response(status=502, text=raw)

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(fail, call)
    assert (r.reason, r.errors) == ("http_502", errors)


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


@pytest.mark.parametrize(
    "usage, want",
    [
        (  # groq, as received: extra fields are dropped
            {"prompt_tokens": 1200, "completion_tokens": 150, "total_tokens": 1350,
             "queue_time": 0.01, "prompt_tokens_details": {"cached_tokens": 0}},
            {"promptTokens": 1200, "completionTokens": 150, "totalTokens": 1350},
        ),
        ({"prompt_eval_count": 700, "eval_count": 90},  # ollama: the total is the sum
         {"promptTokens": 700, "completionTokens": 90, "totalTokens": 790}),
        ({"prompt_tokens": True, "completion_tokens": -1, "total_tokens": "9"}, None),
        ({"total_tokens": 10**12}, None),
        ("1350 tokens", None),
        (None, None),
    ],
)  # fmt: skip
def test_usage_keeps_the_token_counts_only(usage, want):
    async def answer(request, body):
        return json_response(good_answer(usage=usage))

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(answer, call)
    assert r.ok and r.meta.get("usage") == want


def test_a_rejection_after_the_model_answered_keeps_its_usage():
    async def refuse(request, body):
        meta = {"mode": "llm", "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
        body = {
            "error": "invalid_proposal",
            "detail": "x",
            "raw": "{}",
            "metadata": meta,
        }
        return json_response(body, 422)

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(refuse, call)
    assert r.reason == "rejected"
    assert r.meta["usage"] == {
        "promptTokens": 5,
        "completionTokens": 2,
        "totalTokens": 7,
    }


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
        (wrong_schema(), "not an evidence/v1 or evidence/v1.1 bundle"),
        ({"schema": "evidence/v1"}, "redaction.counts missing"),
        ({"schema": "evidence/v1.1"}, "redaction.counts missing"),
        ("a string", "not an evidence/v1 or evidence/v1.1 bundle"),
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


def test_a_v1_bundle_still_passes_the_check():
    """evidence/v1.1 only adds metrics.zscores.inflight (ADR-039); a stored v1 bundle stays valid."""
    raw = make_bundle()
    raw = {k: v for k, v in raw.items() if k != "hash"} | {"schema": "evidence/v1"}
    raw["hash"] = proposal.hash_of(raw)
    assert check_redacted(raw) is None


SELECTION = {
    "pods": [{"pod": "sample-api-a", "lines": 200, "kept_error_lines": 3,
              "kept_recent_lines": 37, "dropped": 160}],
    "eventsShown": 20, "eventsTotal": 50,
}  # fmt: skip


def test_the_prompt_excerpt_is_kept():
    """ADR-042 decision 6: what prompt/v2 sent is recorded with each call."""

    async def answer(request, body):
        return json_response(good_answer(logSelection=SELECTION, promptTrimLevel=1,
                                         promptTokensEstimate=4870))  # fmt: skip

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(answer, call)
    assert r.ok
    assert r.meta["logSelection"] == SELECTION
    assert r.meta["promptTrimLevel"] == 1 and r.meta["promptTokensEstimate"] == 4870


def test_a_malformed_excerpt_keeps_only_clean_counts():
    junk = {
        "pods": [{"pod": "p" * 300, "lines": -1, "kept_error_lines": True,
                  "kept_recent_lines": "9", "dropped": 4, "extra": "x"}, "not-a-pod"]
        + [{"pod": None}] * 20,
        "eventsShown": 1.5, "eventsTotal": 7, "other": {"deep": 1},
    }  # fmt: skip

    async def answer(request, body):
        return json_response(good_answer(logSelection=junk, promptTrimLevel="1",
                                         promptTokensEstimate=10**9))  # fmt: skip

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(answer, call)
    sel = r.meta["logSelection"]
    assert sel["pods"][0] == {"pod": "p" * 100, "dropped": 4}
    assert len(sel["pods"]) == 9  # the first 10 entries, the string one dropped
    assert sel["eventsTotal"] == 7 and "eventsShown" not in sel and "other" not in sel
    assert "promptTrimLevel" not in r.meta and "promptTokensEstimate" not in r.meta


def test_a_502_with_the_excerpt_keeps_it():
    """The Reasoner sends the excerpt with model_unavailable: the call was built and sent."""

    async def refuse(request, body):
        meta = {"mode": "llm", "promptTrimLevel": 0, "promptTokensEstimate": 4100,
                "logSelection": SELECTION}  # fmt: skip
        return json_response({"error": "model_unavailable", "detail": "http_413",
                              "metadata": meta}, 502)  # fmt: skip

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(refuse, call)
    assert not r.ok and r.meta["promptTokensEstimate"] == 4100
    assert r.meta["logSelection"] == SELECTION


def test_a_422_prompt_too_large_keeps_the_request_estimate():
    """The request that was never sent: its estimate is the only record of its size."""

    async def refuse(request, body):
        meta = {"mode": "llm", "arm": "R3", "requestTokensEstimate": 7400}
        return json_response(
            {"error": "prompt_too_large", "detail": "x", "metadata": meta}, 422
        )

    async def call(client, server):
        return await client.propose(make_bundle(), incident_uid="u1", level=3)

    r = run(refuse, call)
    assert r.reason == "rejected" and r.meta["rejection"] == "prompt_too_large"
    assert r.meta["requestTokensEstimate"] == 7400


def test_the_model_config_name_is_kept_only_when_it_is_a_short_name():
    for given, kept in (
        ("r3-primary", "r3-primary"),
        ("x" * 65, None),
        ("a b", None),
        (7, None),
    ):

        async def answer(request, body, given=given):
            return json_response(good_answer(modelConfig=given))

        async def call(client, server):
            return await client.propose(make_bundle(), incident_uid="u1", level=3)

        r = run(answer, call)
        assert r.meta.get("modelConfig") == kept, given


def test_the_reasoning_trace_is_kept_as_text_and_capped():
    from nexus_operator.reasoner_client import REASONING_MAX_CHARS

    for given, length, want in (
        ("t" * 20000, 20000, ("t" * REASONING_MAX_CHARS, 20000)),
        ("short", None, ("short", 5)),
        ({"not": "text"}, 3, None),
        ("", 0, None),
    ):

        async def answer(request, body, given=given, length=length):
            meta = {"reasoning": given}
            if length is not None:
                meta["reasoningLength"] = length
            return json_response(good_answer(**meta))

        async def call(client, server):
            return await client.propose(make_bundle(), incident_uid="u1", level=3)

        r = run(answer, call)
        got = (
            (r.meta["reasoning"], r.meta["reasoningLength"])
            if "reasoning" in r.meta
            else None
        )
        assert got == want, (
            given if isinstance(given, str) and len(given) < 30 else type(given)
        )
