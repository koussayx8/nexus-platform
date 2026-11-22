"""The token never reaches argv, a URL or a log line; it is scoped to the fork for a push only."""

import base64
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from nexus_revert.gitcmd import EXPLANATION_MAX, GitError, SubprocessGit, explanation
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


SHA = "5a6d2ecf4db96f13a1ad5b94fc3b2269be0b3b07"
REFUSAL = (
    f"To https://github.com/{MACHINE}/nexus-platform.git\n"
    f" ! [remote rejected] {SHA} -> nexus/revert-inc-ac15ee36af8a7a62-1795335168 "
    "(refusing to allow a Personal Access Token to create or update workflow `.github/workflows/x.yml` without `workflow` scope)\n"
    f"error: failed to push some refs to 'https://github.com/{MACHINE}/nexus-platform.git'\n"
)


def stub_push_failure(monkeypatch: pytest.MonkeyPatch, stderr: str) -> None:
    def fake(argv: list[str], **kw: Any) -> Any:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake)


def test_a_refused_push_keeps_githubs_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_push_failure(monkeypatch, REFUSAL)
    g = SubprocessGit(tmp_path, push_url="https://github.com/x/y.git", login=MACHINE, token=TOKEN)
    with pytest.raises(GitError) as e:
        g(["push", "origin", "HEAD:refs/heads/nexus/x"], push_auth=True)
    msg = str(e.value)
    assert msg.startswith("git push exited 1: ")
    assert "refusing to allow a Personal Access Token" in msg and "without `workflow` scope" in msg
    assert "! [remote rejected]" in msg  # the reason comes first; the generic summary after it may be cut
    assert "To https://" not in msg  # only the explaining lines, not git's "To <url>" line
    assert len(msg.removeprefix("git push exited 1: ")) <= EXPLANATION_MAX


def test_the_explanation_never_carries_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    basic = base64.b64encode(f"{MACHINE}:{TOKEN}".encode()).decode()
    stub_push_failure(monkeypatch, f"remote: bad credentials {TOKEN}\n! [remote rejected] x -> y (Authorization: Basic {basic})\n"
                                   f"error: failed to push some refs to 'https://{TOKEN}@github.com/x/y.git'\n")
    g = SubprocessGit(tmp_path, push_url="https://github.com/x/y.git", login=MACHINE, token=TOKEN)
    with pytest.raises(GitError) as e:
        g(["push", "origin", "HEAD:refs/heads/nexus/x"], push_auth=True)
    assert TOKEN not in str(e.value) and basic not in str(e.value) and "<token>" in str(e.value)


def test_explanation_is_cut_to_300_and_falls_back_to_the_last_line() -> None:
    assert len(explanation("error: " + "x" * 500)) == EXPLANATION_MAX
    assert explanation("hint: a\nsomething odd\n") == "something odd"
    assert explanation("") == "no stderr"
    assert explanation("hint: ignore me\nfatal: not a git repository\n") == "fatal: not a git repository"
