"""The GitHub client against a fake API on 127.0.0.1; the module has no merge call at all."""

import inspect
import json
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from nexus_revert import github, prwatch, revert, service
from nexus_revert.github import GitHub, GitHubError, HttpTransport
from tests.fixtures import TOKEN, UPSTREAM


class Api(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, dict[str, str], Any]] = []
    status = 200

    def _reply(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        Api.seen.append((method, self.path, dict(self.headers), body))
        if Api.status != 200:
            return self._reply(Api.status, {"message": "Resource not accessible by personal access token"})
        if "/git/ref/heads/" in self.path:
            return self._reply(404, {"message": "Not Found"})
        if method == "POST":
            return self._reply(201, {"number": 9, "html_url": "x"})
        return self._reply(200, [])

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.fixture
def base() -> Iterator[str]:
    Api.seen, Api.status = [], 200
    srv = HTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_requests_and_headers(base: str) -> None:
    gh = GitHub(HttpTransport(TOKEN, base), UPSTREAM)
    assert gh.open_pulls("experiment/dev-state") == []
    assert gh.branch_exists("nexus-machine/nexus-platform", "nexus/revert-inc-1") is False
    assert gh.create_pull(title="t", head="m:nexus/revert-x", base="experiment/dev-state", body="b")["number"] == 9
    (m1, p1, h1, _), (_, p2, _, _), (m3, p3, _, b3) = Api.seen
    assert p1 == f"/repos/{UPSTREAM}/pulls?state=open&base=experiment%2Fdev-state&per_page=100"
    assert p2 == "/repos/nexus-machine/nexus-platform/git/ref/heads/nexus/revert-inc-1"
    assert (m3, p3) == ("POST", f"/repos/{UPSTREAM}/pulls")
    assert b3 == {"title": "t", "head": "m:nexus/revert-x", "base": "experiment/dev-state", "body": "b",
                  "maintainer_can_modify": False, "draft": False}
    assert h1["Authorization"] == f"Bearer {TOKEN}" and TOKEN not in p1


def test_refusal_carries_status_not_token(base: str) -> None:
    Api.status = 403
    gh = GitHub(HttpTransport(TOKEN, base), UPSTREAM)
    with pytest.raises(GitHubError) as e:
        gh.create_pull(title="t", head="m:b", base="x", body="b")
    assert e.value.status == 403 and TOKEN not in str(e.value) and e.value.__cause__ is None
    assert TOKEN not in repr(HttpTransport(TOKEN, base))


def test_unreachable_carries_no_token() -> None:
    gh = GitHub(HttpTransport(TOKEN, "http://127.0.0.1:1"), UPSTREAM)
    with pytest.raises(GitHubError) as e:
        gh.open_pulls("x")
    assert TOKEN not in str(e.value)


def test_a_path_outside_repos_is_refused_even_under_python_O() -> None:
    with pytest.raises(GitHubError, match="outside /repos/") as e:
        HttpTransport(TOKEN, "http://127.0.0.1:1").request("GET", "/user")
    assert e.value.status is None and TOKEN not in str(e.value)


@pytest.mark.parametrize("module", [github, prwatch, revert, service])
def test_no_merge_auto_merge_review_or_label_call_exists(module: object) -> None:
    src = inspect.getsource(module)  # type: ignore[arg-type]
    assert not re.search(r"/merge\b|auto_merge|automerge|enablePullRequestAutoMerge|/reviews|/labels|/comments", src)
    assert not re.search(r'"(PUT|PATCH|DELETE)"', src)
    assert "--force" not in src and "+refs/heads/nexus" not in src
