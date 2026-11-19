"""Pushes: one message per Incident and phase; restart does not resend old news."""

from datetime import timedelta

from nexus_bot.payload import decode
from nexus_bot.push import Pusher
from nexus_bot.telegram import TelegramClient, TelegramError
from tests.fakes import NOW, OWNER_ID, FakeCluster, FakeTransport, incident_dict

NAME = "inc-0123abcd-1764324000"


def pusher(c: FakeCluster, t: FakeTransport, chats: list[int] | None = None) -> Pusher:
    return Pusher(c, TelegramClient(t), chats or [OWNER_ID], now=lambda: NOW, catch_up=timedelta(minutes=15))


def test_awaiting_approval_is_pushed_once_with_three_buttons() -> None:
    c, t = FakeCluster(incident_dict()), FakeTransport()
    p = pusher(c, t)
    assert p.tick() == 1 and p.tick() == 0
    [msg] = t.sent()
    labels = [b["text"] for b in msg["reply_markup"]["inline_keyboard"][0]]
    assert labels == ["Approve", "Reject", "Explain"]
    assert all(decode(b["callback_data"]) for b in msg["reply_markup"]["inline_keyboard"][0])
    assert "scale_deployment" in msg["text"] and "UNTRUSTED: HYPOTHESIS" in msg["text"]


def test_each_push_phase_once_and_others_never() -> None:
    c, t = FakeCluster(incident_dict()), FakeTransport()
    p = pusher(c, t)
    p.tick()
    for phase in ["Executing", "Verifying", "Resolved"]:
        c.items[NAME]["status"]["phase"] = phase
        c.items[NAME]["status"]["timestamps"] = {"terminal": "2026-11-28T09:59:00Z"}
        p.tick()
        p.tick()
    texts = [m["text"].splitlines()[0] for m in t.sent()]
    assert texts == ["NEXUS AwaitingApproval (proposed_escalation)", "NEXUS Resolved (proposed_escalation)"]
    resolved = t.sent()[1]
    assert [b["text"] for b in resolved["reply_markup"]["inline_keyboard"][0]] == ["Explain"]


def test_restart_does_not_resend_old_news() -> None:
    old = incident_dict(phase="Resolved", stamp="2026-11-28T09:00:00Z")
    fresh = incident_dict(name="inc-fresh-1", uid="2" * 32, phase="Blocked", stamp="2026-11-28T09:55:00Z")
    c, t = FakeCluster(old, fresh), FakeTransport()
    assert pusher(c, t).tick() == 1
    assert t.sent()[0]["text"].startswith("NEXUS Blocked")


def test_an_old_phase_seen_later_in_a_run_is_pushed() -> None:
    c, t = FakeCluster(), FakeTransport()
    p = pusher(c, t)
    p.tick()
    c.items[NAME] = incident_dict(phase="Escalated", stamp="2026-11-28T08:00:00Z")
    assert p.tick() == 1


def test_failed_send_is_retried() -> None:
    c, t = FakeCluster(incident_dict()), FakeTransport()
    t.fail["sendMessage"] = TelegramError("sendMessage: timed out")
    p = pusher(c, t)
    assert p.tick() == 0
    del t.fail["sendMessage"]
    assert p.tick() == 1


def test_every_allowlisted_chat_gets_it() -> None:
    c, t = FakeCluster(incident_dict()), FakeTransport()
    assert pusher(c, t, [1, 2]).tick() == 2
    assert sorted(m["chat_id"] for m in t.sent()) == [1, 2]


def test_list_failure_sends_nothing() -> None:
    from nexus_client.errors import Unreachable

    c, t = FakeCluster(incident_dict()), FakeTransport()
    c.fail_list = Unreachable("down")
    assert pusher(c, t).tick() == 0 and t.sent() == []
