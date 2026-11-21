"""The token never reaches argv, a URL or a log line; it is scoped to the fork for a push only."""

import base64
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from nexus_revert.gitcmd import GitError, SubprocessGit
from tests.fixtures import MACHINE, TOKEN


def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = subprocess.run

    def spy(argv: list[str], **kw: Any) -> Any:
        calls.append({"argv": list(argv), "env": dict(kw.get("env") or {})})
        return real(argv, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


def test_token_only_in_the_push_env_scoped_to_the_fork(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = recorded(monkeypatch)
    g = SubprocessGit(tmp_path, push_url="https://github.com/nexus-machine/nexus-platform.git", login=MACHINE, token=TOKEN)
    g(["init", "-q"])
    g(["status"], push_auth=True, check=False)
    plain, auth = calls
    for c in calls:
        assert all(TOKEN not in a for a in c["argv"])
        assert "--force" not in c["argv"]
    assert not any("GIT_CONFIG_VALUE" in k for k in plain["env"])
    assert auth["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/nexus-machine/nexus-platform.git.extraHeader"
    basic = base64.b64encode(f"{MACHINE}:{TOKEN}".encode()).decode()
    assert auth["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {basic}"
    for c in calls:
        assert c["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null" and c["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert "core.hooksPath=/dev/null" in c["argv"] and "protocol.allow=never" in c["argv"]
        assert "protocol.file.allow=always" not in c["argv"]


def test_errors_carry_no_token(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    g = SubprocessGit(tmp_path, push_url="https://127.0.0.1:1/x.git", login=MACHINE, token=TOKEN)
    g(["init", "-q"])
    with pytest.raises(GitError) as e:
        g(["push", "https://127.0.0.1:1/x.git", "HEAD:refs/heads/nexus/x"], push_auth=True)
    assert TOKEN not in str(e.value) and e.value.__cause__ is None
    assert TOKEN not in caplog.text and TOKEN not in repr(g)


def test_file_protocol_is_refused_without_the_test_switch(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "--bare"], cwd=src, check=True)
    work = tmp_path / "w"
    work.mkdir()
    g = SubprocessGit(work, push_url="https://github.com/x/y.git", login=MACHINE, token=TOKEN)
    g(["init", "-q"])
    with pytest.raises(GitError):
        g(["fetch", src.as_uri()])
