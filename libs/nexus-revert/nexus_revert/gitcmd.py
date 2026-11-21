"""Git, run defensively (ADR-041 "Commit validation", last paragraph).

Every call runs the system `git` with no shell, in one scratch directory, with no system or global
config, no hooks, no submodules, no prompts, no credential helper, and only https (plus file:// when
a test asks for it). The token is never in a URL, argv or a log line: for a push only, it is passed
as an `http.<fork URL>.extraHeader` through GIT_CONFIG_* environment variables, scoped to the fork.
"""

import base64
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class GitError(Exception):
    """A git call failed. The message is git's stderr tail, with the token removed."""


@dataclass(frozen=True)
class Result:
    rc: int
    out: str
    err: str


class Git(Protocol):
    def __call__(self, args: Sequence[str], *, push_auth: bool = False, check: bool = True) -> Result: ...


HARDENING = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "credential.helper=",
    "-c", "submodule.recurse=false",
    "-c", "protocol.allow=never",
    "-c", "protocol.https.allow=always",
    "-c", "commit.gpgSign=false",
    "-c", "advice.detachedHead=false",
    "-c", "init.defaultBranch=nexus-scratch",
)


class SubprocessGit:
    def __init__(self, workdir: Path, *, push_url: str, login: str, token: str,
                 allow_file: bool = False, timeout_s: float = 120) -> None:
        self._dir, self._push_url, self._login = workdir, push_url, login
        self._token, self._timeout = token, timeout_s
        self._extra = ("-c", "protocol.file.allow=always") if allow_file else ()

    def __repr__(self) -> str:
        return f"SubprocessGit(workdir={str(self._dir)!r})"

    def _env(self, push_auth: bool) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self._dir),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false",
            "SSH_ASKPASS": "/bin/false",
        }
        if push_auth:
            basic = base64.b64encode(f"{self._login}:{self._token}".encode()).decode()
            env |= {"GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"http.{self._push_url}.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}
        return env

    def _redact(self, text: str) -> str:
        if self._token:
            text = text.replace(self._token, "<token>")
            text = text.replace(base64.b64encode(f"{self._login}:{self._token}".encode()).decode(), "<token>")
        return text

    def __call__(self, args: Sequence[str], *, push_auth: bool = False, check: bool = True) -> Result:
        argv = ["git", *HARDENING, *self._extra, *args]
        try:
            p = subprocess.run(argv, cwd=self._dir, env=self._env(push_auth), capture_output=True,
                               text=True, timeout=self._timeout, check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GitError(f"git {args[0]}: {type(e).__name__}") from None
        res = Result(p.returncode, p.stdout, self._redact(p.stderr))
        if check and p.returncode != 0:
            tail = res.err.strip().splitlines()[-1:] or ["no stderr"]
            raise GitError(f"git {args[0]} exited {p.returncode}: {tail[0][:300]}")
        return res
