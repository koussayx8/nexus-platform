"""Who may press (ADR-040 decision 3).

An update is handled only when its sender's numeric ID is allowlisted and the chat is that
sender's private chat with the bot. Group chats, channels, bots and updates with no sender are
dropped. Nothing here reads message text.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Sender:
    user_id: int
    approver: str  # the username the bot impersonates for this sender (K5 stamps it as approvedBy)


def _ids(update: dict[str, Any]) -> tuple[Any, Any]:
    """(from, chat) of a message or a callback query; (None, None) for anything else."""
    if isinstance(update.get("message"), dict):
        m = update["message"]
        return m.get("from"), m.get("chat")
    if isinstance(update.get("callback_query"), dict):
        cq = update["callback_query"]
        msg = cq.get("message")
        return cq.get("from"), msg.get("chat") if isinstance(msg, dict) else None
    return None, None


def sender_of(update: dict[str, Any], allowlist: dict[int, str]) -> Sender | None:
    frm, chat = _ids(update)
    if not isinstance(frm, dict) or not isinstance(chat, dict):
        return None
    uid = frm.get("id")
    if type(uid) is not int or frm.get("is_bot") is True:
        return None
    if chat.get("type") != "private" or chat.get("id") != uid:
        return None
    approver = allowlist.get(uid)
    return Sender(uid, approver) if approver else None


def log_ids(update: dict[str, Any]) -> dict[str, Any]:
    """IDs only, for the log line of a dropped update: never text, never names."""
    frm, chat = _ids(update)
    return {
        "update_id": update.get("update_id"),
        "from_id": frm.get("id") if isinstance(frm, dict) else None,
        "chat_id": chat.get("id") if isinstance(chat, dict) else None,
        "chat_type": chat.get("type") if isinstance(chat, dict) else None,
    }
