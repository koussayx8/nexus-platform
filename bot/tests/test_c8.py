"""C8 in the bot (ADR-041): off without its config or token, on with both, its results in the chat,
and a C8 error never stops the bot. Offline: git on file:// fixture repositories (the library's own
fixtures), GitHub in memory, Telegram a recording fake."""

import importlib.util
import logging
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from nexus_revert.service import Service

from nexus_bot.main import C8_PREFIX, _loop, c8_message, c8_notifier, c8_service
from nexus_bot.telegram import TelegramClient, TelegramError
from tests.fakes import OWNER_ID, FakeTransport

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "ghp_" + "Q" * 36  # shaped like a classic token, not one
NOW = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)  # the library fixtures' clock
REVERT_YAML = """upstream: koussayx8/nexus-platform
machineAccount: belhouchetkoussay
prMode: pull_request
pollSeconds: 30
branches:
  nexus-dev:
    branch: experiment/dev-state
    overlay: overlays/dev
    application: sample-api-dev
    allow: [overlays/dev/kustomization.yaml, overlays/dev/sample-api*.yaml]
    exclude: [overlays/dev/namespace.yaml]
"""


def _fixtures() -> ModuleType:
    """libs/nexus-revert/tests/fixtures.py, loaded by path (its package name `tests` is the bot's)."""
    name = "nexus_revert_test_fixtures"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "libs/nexus-revert/tests/fixtures.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


class Reader:
    def __init__(self, *items: dict[str, Any], fail: int = 0) -> None:
        self.items, self.fail, self.calls = list(items), fail, 0

    def list_raw(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("boom")
        return self.items


def env_for(tmp: Path, *, config: str | None = REVERT_YAML, token: str | None = TOKEN) -> dict[str, str]:
    cfg, tok = tmp / "revert.yaml", tmp / "github" / "token"
    if config is not None:
        cfg.write_text(config)
    if token is not None:
        tok.parent.mkdir(exist_ok=True)
        tok.write_text(token + "\n")
    return {"NEXUS_REVERT_CONFIG": str(cfg), "NEXUS_REVERT_TOKEN_FILE": str(tok),
            "NEXUS_REVERT_SCRATCH": str(tmp)}


def no_recover(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Service, "recover", lambda self: 0)  # no GitHub call at start in these tests


# --- off unless both files are present ------------------------------------------------------------

@pytest.mark.parametrize(("config", "token", "says"), [
    (None, None, "the revert config and the GitHub token file absent"),
    (REVERT_YAML, None, "the GitHub token file absent"),
    (None, TOKEN, "the revert config absent"),
])
def test_c8_is_off_without_its_config_or_token(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                               config: str | None, token: str | None, says: str) -> None:
    built: list[int] = []
    with caplog.at_level(logging.INFO, "nexus_bot"):
        svc = c8_service(lambda _t: None, env_for(tmp_path, config=config, token=token),
                         reader_factory=lambda: built.append(1) or Reader())  # type: ignore[func-returns-value]
    assert svc is None and built == []  # nothing is built, no cluster or GitHub client
    assert [r.getMessage() for r in caplog.records] == [f"C8 off: {says}"]


def test_the_default_paths_are_the_pod_mounts() -> None:
    from nexus_revert.config import DEFAULT_CONFIG, DEFAULT_TOKEN_FILE

    assert DEFAULT_CONFIG == "/etc/nexus-bot/revert.yaml"
    assert DEFAULT_TOKEN_FILE == "/var/run/secrets/nexus-bot/github/token"


def test_an_unusable_config_or_token_turns_c8_off_and_never_logs_the_token(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    for i, (config, token) in enumerate([("machineAccount: [not, a, name]\n", TOKEN), (REVERT_YAML, "short"),
                                         ("{{ not yaml", TOKEN)]):
        d = tmp_path / str(i)
        d.mkdir()
        with caplog.at_level(logging.INFO, "nexus_bot"):
            assert c8_service(lambda _t: None, env_for(d, config=config, token=token),
                              reader_factory=Reader) is None
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 3 and all(m.startswith("C8 off: ") for m in msgs)
    assert all(TOKEN not in m and TOKEN[4:20] not in m for m in caplog.text.splitlines())


def test_a_failing_start_turns_c8_off_and_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    def reader() -> Reader:
        raise RuntimeError("no in-cluster config")

    with caplog.at_level(logging.INFO, "nexus_bot"):
        assert c8_service(lambda _t: None, env_for(tmp_path), reader_factory=reader) is None
    monkeypatch.setattr(Service, "recover", lambda self: (_ for _ in ()).throw(OSError("dns")))
    with caplog.at_level(logging.INFO, "nexus_bot"):
        assert c8_service(lambda _t: None, env_for(tmp_path), reader_factory=Reader) is None
    assert [r.getMessage() for r in caplog.records] == ["C8 off: cannot start: RuntimeError",
                                                        "C8 off: cannot start: OSError"]


def test_c8_is_on_with_both_and_uses_its_configured_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    no_recover(monkeypatch)
    with caplog.at_level(logging.INFO, "nexus_bot"):
        svc = c8_service(lambda _t: None, env_for(tmp_path), reader_factory=Reader)
    assert svc is not None
    assert svc.cfg.machine == "belhouchetkoussay" and svc.cfg.mode == "pull_request"
    assert svc.cfg.poll_seconds == 30 and svc.scratch == tmp_path
    assert svc.cfg.token == TOKEN and TOKEN not in caplog.text and TOKEN not in repr(svc.cfg)
    assert caplog.records[-1].getMessage() == (
        "C8 on: 1 branch(es), mode pull_request, every 30s, 0 open revert pull request(s) recovered")


# --- the chat ---------------------------------------------------------------------------------------

def test_notifier_sends_one_sanitised_message_to_every_chat(caplog: pytest.LogCaptureFixture) -> None:
    t = FakeTransport()
    t.fail["sendMessage"] = TelegramError("sendMessage: HTTP 403: Forbidden")
    notify = c8_notifier(TelegramClient(t), [OWNER_ID, 777])
    with caplog.at_level(logging.INFO, "nexus_bot"):
        notify("inc-x: no revert pull request (check 2): \x1b[2Jnot on\nthe branch")  # a failed send does not raise
    assert [p["chat_id"] for m, p in t.calls] == [OWNER_ID, 777]
    line = C8_PREFIX + "inc-x: no revert pull request (check 2): not on the branch"
    shown = "⛔ C8: no revert PR\nIncident: inc-x\nno revert pull request (check 2): not on the branch"
    assert all(p["text"] == shown and "parse_mode" not in p for _m, p in t.calls)
    quote = [e for e in t.calls[0][1]["entities"] if e["type"] == "blockquote"]
    assert quote == [{"type": "blockquote", "offset": 35, "length": 51}]  # ⛔ is one UTF-16 unit
    assert line in caplog.text and caplog.text.count("send failed") == 2


@pytest.mark.parametrize(("text", "title"), [
    ("inc-a: revert pull request #7 opened against experiment/dev-state (reverts 0123456789ab, chosen by "
     "rule A1); NEXUS will not merge it", "🟠 C8: revert PR opened"),
    ("inc-a: revert pull request #7 merged by koussayx8, merge commit " + "c" * 40, "🟢 C8: revert PR merged"),
    ("inc-a: revert pull request #7 closed, not merged", "⚪ C8: revert PR closed"),
    ("inc-a: revert pull request #7 still open after 1 day, 0:00:00; no longer watched",
     "⚪ C8: revert PR no longer watched"),
    ("inc-a: no revert pull request (check 3): merged by someone", "⛔ C8: no revert PR"),
    ("inc-a: revert of 0123456789ab (chosen by rule A1) pushed to the fork; open the pull request: "
     "https://github.com/x/y/compare/a...b", "🟠 C8: open the revert PR"),
    ("something else: revert pull request #7 merged by x", "⚪ C8 revert"),
])
def test_the_c8_title_comes_from_c8s_own_wording_at_the_start(text: str, title: str) -> None:
    msg = c8_message(text).build()
    assert msg.text.split("\n")[0] == title
    assert [e["type"] for e in msg.entities if e["type"] not in ("bold",)] == ["blockquote"]
    # the C8 text is untrusted: inside the blockquote, and no bold over any of it
    (q,) = [e for e in msg.entities if e["type"] == "blockquote"]
    (u_off, u_len), = msg.untrusted
    assert q["offset"] <= u_off and u_off + u_len <= q["offset"] + q["length"]
    assert all(e["offset"] + e["length"] <= u_off for e in msg.entities if e["type"] == "bold")


def test_the_chat_shows_a_pull_request_opened_and_a_refusal_with_its_check(tmp_path: Path) -> None:
    fx = _fixtures()
    repos = fx.Repos(tmp_path)
    bad = repos.commit("feat: S3", {"overlays/dev/sample-api-patch.yaml": "readiness: broken\n"})
    gh = fx.FakeGitHub(repos)
    t = FakeTransport()
    cfg = repos.config()
    reader = Reader(fx.incident(bad), fx.incident("f" * 40, name="inc-0a1b2c3d-1764585601"))
    svc = Service(cfg, reader, gh.client(), lambda w: repos.git(w, cfg),
                  c8_notifier(TelegramClient(t), [OWNER_ID]), now=lambda: NOW, scratch=tmp_path)
    svc.tick()
    texts = [m["text"] for m in t.sent()]
    assert len(texts) == 2 and texts[0].startswith("🟠 C8: revert PR opened\n")
    assert texts[1].startswith("⛔ C8: no revert PR\nIncident: inc-0a1b…5601\n")
    assert f"revert pull request #100 opened against experiment/dev-state (reverts {bad[:12]}" in texts[0]
    assert texts[0].endswith("NEXUS will not merge it")
    assert "\nno revert pull request (check 2)" in texts[1]
    assert not any(fx.TOKEN in x for x in texts)
    # the owner merges; the next tick tells the chat (a notification only, ADR-041 decision 10)
    gh.pulls[100] |= {"state": "closed", "merged": True, "merged_by": {"login": "koussayx8"},
                      "merge_commit_sha": "c" * 40}
    svc.tick()
    assert t.sent()[-1]["text"] == (
        "🟢 C8: revert PR merged\nIncident: inc-0a1b…5600\n"
        f"revert pull request #100 merged by koussayx8, merge commit {'c' * 40}")


# --- a C8 error never stops the bot -----------------------------------------------------------------

def test_the_c8_loop_survives_an_exception_and_keeps_ticking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    no_recover(monkeypatch)
    reader = Reader(fail=2)
    svc = c8_service(lambda _t: None, env_for(tmp_path), reader_factory=lambda: reader)
    assert svc is not None
    stop = threading.Event()

    def tick() -> object:
        if reader.calls >= 4:
            stop.set()
        return svc.tick()

    with caplog.at_level(logging.INFO, "nexus_bot"):
        _loop(tick, 0, "c8", stop, backoff=0)
    assert reader.calls >= 4  # two failed ticks, then it went on
    assert [r.getMessage() for r in caplog.records].count("c8: RuntimeError") == 2


def test_a_c8_thread_that_always_fails_leaves_the_poll_loop_running() -> None:
    stop = threading.Event()
    polls: list[int] = []

    def c8_tick() -> object:
        raise RuntimeError("C8 is broken")

    def poll() -> object:
        polls.append(1)
        if len(polls) >= 20:
            stop.set()
        return 0

    th = threading.Thread(target=_loop, args=(c8_tick, 0, "c8", stop), kwargs={"backoff": 0}, daemon=True)
    th.start()
    _loop(poll, 0, "poll", stop)
    th.join(5)
    assert len(polls) == 20 and not th.is_alive()
