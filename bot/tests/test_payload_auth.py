"""Sender check (decision 3) and the button payload (51 bytes, regex)."""

import pytest
from nexus_client.models import Incident

from nexus_bot import payload
from nexus_bot.auth import Sender, log_ids, sender_of
from tests.fakes import HASH, OWNER_ID, UID, incident_dict, message, press

ALLOW = {OWNER_ID: "koussay-approver"}


def test_allowlisted_private_message_is_handled() -> None:
    assert sender_of(message("/status"), ALLOW) == Sender(OWNER_ID, "koussay-approver")


def test_allowlisted_private_press_is_handled() -> None:
    assert sender_of(press("e:" + "0" * 16 + ":" + "0" * 32), ALLOW) == Sender(OWNER_ID, "koussay-approver")


@pytest.mark.parametrize("update", [
    message("/status", user=999),                                       # another ID
    message("/status", chat=-100123, chat_type="group"),                # group chat
    message("/status", chat=-100123, chat_type="supergroup"),
    message("/status", chat=-100999, chat_type="channel"),              # channel
    message("/status", chat=555, chat_type="private"),                  # chat id != from id
    message("/status", is_bot=True),                                    # a bot with the owner's ID
    press("x", user=999),
    press("x", chat=-100123, chat_type="group"),
    {"update_id": 3, "message": {"text": "/status", "chat": {"id": OWNER_ID, "type": "private"}}},  # no from
    {"update_id": 4, "callback_query": {"id": "c", "from": {"id": OWNER_ID}}},                     # no message
    {"update_id": 5, "message": {"from": {"id": str(OWNER_ID)}, "chat": {"id": str(OWNER_ID), "type": "private"}}},
    {"update_id": 6, "channel_post": {"chat": {"id": OWNER_ID, "type": "channel"}}},
])
def test_everything_else_is_dropped(update: dict) -> None:  # type: ignore[type-arg]
    assert sender_of(update, ALLOW) is None


def test_log_ids_carry_no_text() -> None:
    ids = log_ids(message("secret words", user=999))
    assert "secret" not in str(ids) and ids["from_id"] == 999


def test_payload_round_trip_and_size() -> None:
    inc = Incident.model_validate(incident_dict())
    for letter in "are":
        data = payload.encode(letter, inc)
        assert data is not None and len(data.encode()) == 51
        p = payload.decode(data)
        assert p == payload.Press(letter, UID.replace("-", "")[:16], HASH[7:39])


@pytest.mark.parametrize("data", [
    None, 5, "", "a", "x:0123456789abcdef:" + "0" * 32, "a:0123456789ABCDEF:" + "0" * 32,
    "a:0123456789abcde:" + "0" * 32, "a:0123456789abcdef:" + "0" * 31, "a:0123456789abcdef:" + "0" * 33,
    "a:0123456789abcdef:" + "0" * 32 + "\n", "a:0123456789abcdef:" + "g" * 32, " a:0123456789abcdef:" + "0" * 32,
    "a:0123456789abcdef:" + "0" * 32 + ":x",
])
def test_decode_rejects_malformed(data: object) -> None:
    assert payload.decode(data) is None


def test_no_button_without_uid_or_well_formed_hash() -> None:
    assert payload.encode("a", Incident.model_validate(incident_dict(uid="not-a-uid"))) is None
    assert payload.encode("a", Incident.model_validate(incident_dict(proposal_hash="sha256:ab12"))) is None
