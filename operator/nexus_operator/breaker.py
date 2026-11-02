"""Circuit Breaker and cool-down (spec §4, §9), counted from the Flight Recorder rows.

The rows come from ActRecorder.recent_actions (recorder/act.py), one per request.

Counting is by executed action — one (incident, action) pair — not by request, so a restart that
evicts three pods counts once. Compensations are not counted: a failed repair must stay undoable.
Rows older than breakerEpoch (Unix seconds) are ignored; the epoch is set by each run reset.
Both checks are advisory: nexus's authoritative limits are RBAC, Kyverno and the PDB.
"""

import datetime

from .recorder import ActionRecord

PER_TARGET_LIMIT, PER_TARGET_WINDOW = 2, datetime.timedelta(minutes=15)
OVERALL_LIMIT, OVERALL_WINDOW = 6, datetime.timedelta(hours=1)
COOLDOWN = datetime.timedelta(minutes=5)


def _executed(rows: list[ActionRecord], epoch: int) -> dict[tuple, ActionRecord]:
    """The earliest row of each executed (incident, action), epoch-filtered."""
    floor = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    out: dict[tuple, ActionRecord] = {}
    for r in rows:
        if r.action_class != "executed" or r.intent_at < floor:
            continue
        if (r.result or "").startswith("denied"):
            continue  # nothing changed
        key = (r.incident_uid, r.action_id)
        if key not in out or r.intent_at < out[key].intent_at:
            out[key] = r
    return out


def lookback(now: datetime.datetime) -> datetime.datetime:
    """The oldest row any check reads."""
    return now - max(OVERALL_WINDOW, PER_TARGET_WINDOW, COOLDOWN)


def breaker_open(
    rows: list[ActionRecord], now: datetime.datetime, epoch: int, target: str
) -> str | None:
    """A description of the limit that is reached, or None while the breaker is closed."""
    actions = _executed(rows, epoch).values()
    overall = [a for a in actions if now - a.intent_at <= OVERALL_WINDOW]
    if len(overall) >= OVERALL_LIMIT:
        return f"{len(overall)} actions in the last hour (limit {OVERALL_LIMIT})"
    mine = [
        a
        for a in overall
        if a.target == target and now - a.intent_at <= PER_TARGET_WINDOW
    ]
    if len(mine) >= PER_TARGET_LIMIT:
        return f"{len(mine)} actions on {target} in 15 min (limit {PER_TARGET_LIMIT})"
    return None


def in_cooldown(
    rows: list[ActionRecord],
    now: datetime.datetime,
    epoch: int,
    target: str,
    incident_uid: str,
) -> bool:
    """An action by another incident on this target started within the last 5 min."""
    return any(
        a.incident_uid != incident_uid
        and a.target == target
        and now - a.intent_at <= COOLDOWN
        for a in _executed(rows, epoch).values()
    )
