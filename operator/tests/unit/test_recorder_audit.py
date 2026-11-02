"""The Audit-Id client wrapper against a local HTTP server standing in for the API server."""

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from nexus_operator.kube import Credentials
from nexus_operator.recorder import AuditedClient


def serve(handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    return TestServer(app)


def client_for(server, tokens):
    it = iter(tokens)

    def creds():
        return Credentials(str(server.make_url("")).rstrip("/"), "/unused", next(it))

    import aiohttp

    return AuditedClient(aiohttp.ClientSession(), creds)


def test_audit_id_status_and_body_are_captured_and_the_token_is_read_per_request():
    seen = []

    async def handler(request):
        seen.append((request.method, request.path_qs, request.headers["Authorization"]))
        return web.json_response({"kind": "Scale"}, headers={"Audit-Id": "abc-123"})

    async def go():
        async with serve(handler) as server:
            c = client_for(server, ["t1", "t2"])
            try:
                a = await c.request("PATCH", "/x/scale", {"spec": {"replicas": 2}})
                b = await c.request("GET", "/x")
            finally:
                await c.close()
            return a, b

    a, _b = asyncio.run(go())
    assert (a.status, a.audit_id, a.body, a.ok) == (
        200,
        "abc-123",
        {"kind": "Scale"},
        True,
    )
    assert seen[0][2] == "Bearer t1" and seen[1][2] == "Bearer t2"


def test_dry_run_adds_the_query_parameter():
    seen = []

    async def handler(request):
        seen.append(request.query.get("dryRun"))
        return web.json_response({}, headers={"Audit-Id": "d-1"})

    async def go():
        async with serve(handler) as server:
            c = client_for(server, ["t"])
            try:
                return await c.request("PATCH", "/x/scale", {"spec": {}}, dry_run=True)
            finally:
                await c.close()

    result = asyncio.run(go())
    assert seen == ["All"] and result.audit_id == "d-1"


def test_a_denial_is_a_result_not_an_exception():
    async def handler(request):
        body = {
            "kind": "Status",
            "message": "admission webhook denied the request: killswitch",
        }
        return web.json_response(body, status=403, headers={"Audit-Id": "deny-1"})

    async def go():
        async with serve(handler) as server:
            c = client_for(server, ["t"])
            try:
                return await c.request("POST", "/x/eviction", {})
            finally:
                await c.close()

    r = asyncio.run(go())
    assert not r.ok and r.status == 403 and r.audit_id == "deny-1"
    assert "killswitch" in r.message


def test_a_missing_header_and_a_non_json_body_are_kept_as_they_are():
    async def handler(request):
        return web.Response(text="plain failure", status=502)

    async def go():
        async with serve(handler) as server:
            c = client_for(server, ["t"])
            try:
                return await c.request("GET", "/x")
            finally:
                await c.close()

    r = asyncio.run(go())
    assert (
        r.audit_id is None
        and r.body == "plain failure"
        and r.message == "plain failure"
    )
