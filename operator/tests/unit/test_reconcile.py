import asyncio
import datetime

import pytest
from fakes import T0, FakeApi

from nexus_operator import hooks
from nexus_operator.model import iso
from nexus_operator.reconcile import Timing, decide, level_of, reconcile

TIMING = Timing(detected_timeout_s=20, reconcile_s=5, handler_timeout_s=10)


def s(seconds: float) -> datetime.datetime:
    return T0 + datetime.timedelta(seconds=seconds)


def incident(status=None, created=T0):
    obj = {
        "metadata": {
            "name": "inc",
            "creationTimestamp": iso(created),
            "resourceVersion": "1",
        }
    }
    if status is not None:
        obj["status"] = status
    return obj


def progress(started: datetime.datetime) -> dict:
    return {"kopf": {"progress": {"intake_level": {"started": started.isoformat()}}}}


@pytest.mark.parametrize(
    "labels, want",
    [
        ({"nexus.io/autonomy-level": "0"}, 0),
        ({"nexus.io/autonomy-level": "1"}, 1),
        ({"nexus.io/autonomy-level": "3"}, 3),
        ({"nexus.io/autonomy-level": "4"}, 0),
        ({"nexus.io/autonomy-level": "x"}, 0),
        ({}, 0),
        (None, 0),
    ],
)
def test_level_missing_or_invalid_is_l0(labels, want):
    ns = None if labels is None else {"metadata": {"labels": labels}}
    assert level_of(ns) == want


def test_no_phase_becomes_detected_at_creation():
    assert decide(incident(), s(3), TIMING) == {
        "phase": "Detected",
        "timestamps": {"detected": iso(T0)},
    }


def test_l0_after_intake_is_recorded():
    got = decide(incident({"phase": "Detected", "autonomyLevel": 0}), s(4), TIMING)
    assert got == {
        "phase": "Recorded",
        "reason": "level_observe",
        "timestamps": {"terminal": iso(s(4))},
    }


def test_l1_waits_then_escalates_at_the_timeout():
    inc = incident({"phase": "Detected", "autonomyLevel": 1})
    assert decide(inc, s(19), TIMING) is None
    assert decide(inc, s(20), TIMING) == {
        "phase": "Escalated",
        "reason": "evidence_error",
        "timestamps": {"terminal": iso(s(20))},
    }


def test_failed_intake_escalates_at_the_timeout():
    inc = incident({"phase": "Detected"})  # no autonomyLevel: intake failed for good
    assert decide(inc, s(19), TIMING) is None
    assert decide(inc, s(20), TIMING)["phase"] == "Escalated"


@pytest.mark.parametrize("phase", ["Recorded", "Escalated", "Resolved", "Blocked"])
def test_terminal_is_never_written(phase):
    assert decide(incident({"phase": phase, "autonomyLevel": 0}), s(99), TIMING) is None


def test_no_terminal_while_progress_is_pending():
    inc = incident({"phase": "Detected", "autonomyLevel": 0, **progress(s(1))})
    assert decide(inc, s(5), TIMING) is None  # L0 waits for Kopf
    assert decide(inc, s(15), TIMING) is None  # pending 14 s <= bound 15 s


def test_pending_handler_bound_escalates():
    # Pending since T0: at the timeout (20 s) it has been pending 20 s > 10 + 5.
    inc = incident({"phase": "Detected", "autonomyLevel": 1, **progress(T0)})
    assert decide(inc, s(20), TIMING)["phase"] == "Escalated"
    # Pending since 10 s: at 20 s only 10 s; the loop waits until the bound passes.
    late = incident({"phase": "Detected", "autonomyLevel": 1, **progress(s(10))})
    assert decide(late, s(20), TIMING) is None
    assert decide(late, s(25), TIMING) is None
    assert decide(late, s(26), TIMING)["phase"] == "Escalated"


def test_purged_progress_is_not_pending():
    inc = incident(
        {
            "phase": "Detected",
            "autonomyLevel": 0,
            "kopf": {"progress": {}, "last-handled-configuration": "{}"},
        }
    )
    assert decide(inc, s(5), TIMING)["phase"] == "Recorded"


def test_p6_event_order_with_a_fake_clock():
    """P6 in CI: Detected first, no terminal before the timeout, then Escalated (point 3)."""
    api = FakeApi()
    api.put("b", created=T0)
    events = []
    for tick in range(0, 40, 5):
        at = s(tick + 0.4)
        if tick == 5:
            api.set_status("b", {"autonomyLevel": 1})  # intake done
        inc = asyncio.run(api.get_incident("b"))
        written = asyncio.run(reconcile(api, inc, TIMING, clock=lambda at=at: at))
        if written:
            events.append((tick + 0.4, written["phase"]))
    assert [p for _, p in events] == ["Detected", "Escalated"]
    assert events[0][0] < 20
    assert 20 <= events[1][0] <= 20 + TIMING.reconcile_s


def test_409_rereads_and_keeps_both_effects():
    """P5: the intake patch lands between the loop's read and write."""
    api = FakeApi()
    api.put("c", created=T0, status={"phase": "Detected"})
    inc = asyncio.run(api.get_incident("c"))
    fired = []

    def intake_lands(name):
        if not fired:
            fired.append(name)
            api.set_status(name, {"autonomyLevel": 0})

    api.before_write = intake_lands
    # Read before intake: the loop sees no level, so at 20 s it would escalate.
    written = asyncio.run(reconcile(api, inc, TIMING, clock=lambda: s(21)))
    end = api.incidents["c"]["status"]
    assert fired == ["c"]
    assert (
        written["phase"] == "Recorded"
    )  # recomputed from the re-read, not the stale read
    assert end["autonomyLevel"] == 0 and end["phase"] == "Recorded"
    assert len(api.writes) == 1


def test_gives_up_after_three_conflicts():
    api = FakeApi()
    api.put("c", created=T0, status={"phase": "Detected", "autonomyLevel": 0})
    api.before_write = lambda name: api.set_status(name, {"autonomyLevel": 0})
    inc = asyncio.run(api.get_incident("c"))
    assert asyncio.run(reconcile(api, inc, TIMING, clock=lambda: s(5))) is None
    assert api.writes == []


def test_terminal_incident_gets_no_write():
    api = FakeApi()
    api.put("r", created=T0, status={"phase": "Recorded", "reason": "level_observe"})
    rv = api.incidents["r"]["metadata"]["resourceVersion"]
    inc = asyncio.run(api.get_incident("r"))
    assert asyncio.run(reconcile(api, inc, TIMING, clock=lambda: s(60))) is None
    assert api.writes == [] and api.incidents["r"]["metadata"]["resourceVersion"] == rv


def test_hooks_are_unset_in_production():
    assert hooks.before_intake is None and hooks.before_status_write is None
