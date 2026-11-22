"""The update loop end to end with both fakes: drop, command, press, offset."""

from nexus_bot.config import Config
from nexus_bot.main import Bot
from nexus_bot.payload import encode
from nexus_bot.telegram import TelegramClient
from tests.fakes import NOW, OWNER_ID, FakeCluster, FakeTransport, incident_dict, message, press

NAME = "inc-0123abcd-1764324000"


def make(*items: dict) -> tuple[Bot, FakeCluster, FakeTransport]:  # type: ignore[type-arg]
    c, t = FakeCluster(*items), FakeTransport()
    cfg = Config(allowlist={OWNER_ID: "koussay-approver"}, token="")
    return Bot(cfg, TelegramClient(t), c, c.approver_for, now=lambda: NOW), c, t


def approve_data() -> str:
    from nexus_client.models import Incident

    data = encode("a", Incident.model_validate(incident_dict()))
    assert data
    return data


def test_stranger_is_dropped_silently() -> None:
    bot, c, t = make(incident_dict())
    bot.handle(message("/status", user=999))
    bot.handle(press(approve_data(), user=999))
    assert t.calls == [] and c.patches == []


def test_group_press_by_the_owner_is_dropped() -> None:
    bot, c, t = make(incident_dict())
    bot.handle(press(approve_data(), chat=-100123, chat_type="group"))
    assert t.calls == [] and c.patches == []


def test_command_reply_goes_to_the_owner() -> None:
    bot, _c, t = make(incident_dict())
    bot.handle(message("/status"))
    [msg] = t.sent()
    assert msg["chat_id"] == OWNER_ID and "Open Incidents: 1" in msg["text"] and msg["entities"]


def test_approve_press_end_to_end() -> None:
    bot, c, t = make(incident_dict())
    bot.handle(press(approve_data()))
    methods = [m for m, _ in t.calls]
    assert methods == ["answerCallbackQuery", "editMessageReplyMarkup", "sendMessage"]
    assert c.items[NAME]["spec"]["approval"]["approvedBy"] == "koussay-approver"
    assert c.items[NAME]["spec"]["approval"]["reason"] == f"telegram:{OWNER_ID}"
    assert "By: koussay-approver" in t.sent()[0]["text"]


def test_malformed_payload_is_answered_and_writes_nothing() -> None:
    bot, c, t = make(incident_dict())
    bot.handle(press("a:approve-everything"))
    assert [m for m, _ in t.calls] == ["answerCallbackQuery"] and c.patches == []


def test_offset_advances_past_a_failing_update() -> None:
    bot, _c, t = make()
    t.results["getUpdates"] = [[message("/status", update_id=41), {"update_id": 42, "message": "not a dict"},
                                message("/pods nexus-dev", update_id=43)]]
    t.fail["sendMessage"] = RuntimeError("boom")  # every handler fails
    assert bot.poll_once() == 3 and bot.offset == 44
    del t.fail["sendMessage"]
    bot.poll_once()
    assert t.calls[-1] == ("getUpdates", {"timeout": 25, "allowed_updates": ["message", "callback_query"],
                                          "offset": 44})
