"""Golden: the AwaitingApproval push, byte for byte as it was on dev at 854a716 (before the display
changes of 2026-11-22). Its text, entities, buttons and callback payloads are fixed here by hand;
unlike tests/snapshots/, nothing regenerates this file. A change to this message is a change to the
approval surface and needs the owner, not a snapshot update.

The second case gives the model a 40-hex commit SHA, a URL and runbook chunk ids: none of the new
trusted links, commit lines or buttons may reach this message.
"""

from datetime import timedelta
from typing import Any

from nexus_bot.push import Pusher
from nexus_bot.telegram import TelegramClient
from tests.fakes import NOW, OWNER_ID, FakeCluster, FakeTransport
from tests.test_messages import sample

KEY, H32 = "669532275a7b73d2", "14f1670a2b3c4d5e6f708192a3b4c5d6"
HEAD = ("🟠 Approval needed\nIncident: inc-6695…2289\nTarget: nexus-dev/sample-api\nLevel: L2\n"
        "Reason: proposed_escalation\nAction: scale_deployment\nParameters: replicas=4\n"
        "Hash: sha256:14f167…839f5e\nGate: requires_approval\nDetected: 09:56 UTC\n"
        "Approve pins this action, these parameters and this hash for at most 15 minutes (Kyverno K5).\n\n"
        "Model text: untrusted, never a decision\nProposal\nFault class: cpu_saturation\n")
ENTITIES = [("bold", 0, 18), ("bold", 19, 9), ("bold", 43, 7), ("bold", 72, 6), ("bold", 82, 7),
            ("bold", 110, 7), ("bold", 135, 11), ("bold", 158, 5), ("bold", 185, 5), ("bold", 209, 9),
            ("italic", 229, 93), ("blockquote", 324, None), ("bold", 324, 39), ("italic", 364, 8),
            ("bold", 373, 12), ("bold", 401, 11)]
KEYBOARD = {"inline_keyboard": [[
    {"text": "Approve", "callback_data": f"a:{KEY}:{H32}"},
    {"text": "Reject", "callback_data": f"r:{KEY}:{H32}"},
    {"text": "Explain", "callback_data": f"e:{KEY}:{H32}"},
]]}


def entities(quote_length: int) -> list[dict[str, Any]]:
    return [{"type": k, "offset": o, "length": quote_length if n is None else n} for k, o, n in ENTITIES]


def push(d: dict[str, Any]) -> dict[str, Any]:
    c, t = FakeCluster(d), FakeTransport()
    Pusher(c, TelegramClient(t), [OWNER_ID], now=lambda: NOW, catch_up=timedelta(minutes=60)).tick()
    [payload] = t.sent()
    return payload


def test_the_awaiting_approval_push_is_unchanged() -> None:
    assert push(sample()) == {
        "chat_id": OWNER_ID,
        "link_preview_options": {"is_disabled": True},
        "text": HEAD + "Hypothesis: CPU <b>saturation</b> on *sample-api*; `kubectl` [docs](hxxps://evil[.]example)",
        "entities": entities(168),
        "reply_markup": KEYBOARD,
    }


def test_no_link_commit_line_or_new_button_reaches_it() -> None:
    d = sample()
    d["status"]["proposal"]["hypothesis"] = ("Bad rollout from commit 0123456789abcdef0123456789abcdef01234567, "
                                             "see https://evil.example")
    d["status"]["reasoner"]["chunks"] = ["rb-cpu-saturation#2", "rb-bad-rollout#3"]
    assert push(d) == {
        "chat_id": OWNER_ID,
        "link_preview_options": {"is_disabled": True},
        "text": HEAD + "Hypothesis: Bad rollout from commit 0123456789abcdef0123456789abcdef01234567, "
                       "see hxxps://evil[.]example",
        "entities": entities(181),
        "reply_markup": KEYBOARD,
    }
