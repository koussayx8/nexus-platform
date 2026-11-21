"""The GitHub REST calls C8 needs, and nothing else (ADR-041 decisions 8, 9, 11; merge recognition).

There is no merge call, no auto-merge, no review, no label, no comment: a test reads this module's
source to keep it so. Every pull request is created with `maintainer_can_modify: false` and
`draft: false`. The token travels in the Authorization header only; no exception carries it.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

API = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Transport(Protocol):
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """Return the decoded JSON body, None for 404 on a GET, or raise GitHubError."""
        ...


class HttpTransport:
    def __init__(self, token: str, base: str = API, timeout_s: float = 20) -> None:
        self._token, self._base, self._timeout = token, base.rstrip("/"), timeout_s

    def __repr__(self) -> str:
        return f"HttpTransport(base={self._base!r})"

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/repos/"):
            raise GitHubError(f"{method}: refusing a path outside /repos/")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self._base}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "nexus-revert",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - fixed base
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None
            try:
                msg = str(json.loads(e.read()).get("message", ""))[:200]
            except (ValueError, OSError, AttributeError):
                msg = ""
            raise GitHubError(f"{method} {path}: HTTP {e.code} {msg}".replace(self._token, "<token>"),
                              e.code) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GitHubError(f"{method} {path}: {getattr(e, 'reason', None) or type(e).__name__}") from None
        return json.loads(raw) if raw else None


def _q(ref: str) -> str:
    return urllib.parse.quote(ref, safe="/")


class GitHub:
    def __init__(self, transport: Transport, upstream: str) -> None:
        self._t, self.upstream = transport, upstream

    def open_pulls(self, base: str) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"state": "open", "base": base, "per_page": 100})
        out = self._t.request("GET", f"/repos/{self.upstream}/pulls?{q}")
        return [p for p in out or [] if isinstance(p, dict)]

    def get_pull(self, number: int) -> dict[str, Any] | None:
        out = self._t.request("GET", f"/repos/{self.upstream}/pulls/{int(number)}")
        return out if isinstance(out, dict) else None

    def branch_exists(self, repo: str, branch: str) -> bool:
        return self._t.request("GET", f"/repos/{repo}/git/ref/heads/{_q(branch)}") is not None

    def create_pull(self, *, title: str, head: str, base: str, body: str) -> dict[str, Any]:
        out = self._t.request("POST", f"/repos/{self.upstream}/pulls", {
            "title": title, "head": head, "base": base, "body": body,
            "maintainer_can_modify": False, "draft": False,
        })
        if not isinstance(out, dict) or not isinstance(out.get("number"), int):
            raise GitHubError("create pull request: unexpected reply")
        return out


def compare_link(git_base: str, upstream: str, base: str, machine: str, branch: str) -> str:
    """Outcome C (ADR-041): the owner opens the pull request with one tap."""
    return f"{git_base}/{upstream}/compare/{_q(base)}...{machine}:{_q(branch)}?expand=1"
