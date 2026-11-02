"""I11 and the state machine (spec §7, D9): the act path only takes legal edges, and Verifying
leads only to Resolved or Compensating. The Compensator is the scale-back already in act.py; this
file is the evidence that it covers the gap."""

import itertools

import pytest

from fakes_act import FakeMetrics, proposal_doc
from nexus_operator.act import LEGAL, PHASE_SEQ
from rig import Rig, verify

PHASES = [
    *PHASE_SEQ,
    "Recorded",
    "Recommended",
    "Dismissed",
    "Resolved",
    "Escalated",
    "Blocked",
    "Rejected",
    "Expired",
]


def edges_of(rig):
    return {(t[2], t[3]) for t in rig.rec.transitions if t[2] is not None}


def test_verifying_leads_only_to_resolved_or_compensating():
    assert LEGAL["Verifying"] == {"Resolved", "Compensating"}
    assert LEGAL["Compensating"] == {"Escalated"}


@pytest.mark.parametrize(
    "old, new",
    [
        (old, new)
        for old, new in itertools.product(LEGAL, PHASES)
        if new not in LEGAL[old]
    ],
)
def test_an_illegal_move_is_refused_and_writes_nothing(old, new):
    rig = Rig()
    rig.proposed(proposal_doc())
    rig.api.set_status("inc", {"phase": old})
    before = rig.inc()["metadata"]["resourceVersion"]
    assert rig.call(rig.act._move(rig.inc(), new, "x")) is None
    assert rig.inc()["metadata"]["resourceVersion"] == before
    assert rig.status()["phase"] == old
    assert rig.rec.transitions == []


def test_a_terminal_incident_has_no_legal_move():
    assert not set(LEGAL) & {
        "Resolved",
        "Escalated",
        "Blocked",
        "Recommended",
        "Dismissed",
    }
    rig = Rig()
    rig.proposed(proposal_doc())
    rig.api.set_status("inc", {"phase": "Resolved"})
    assert rig.call(rig.act._move(rig.inc(), "Executing")) is None


def test_every_edge_the_scenarios_take_is_in_the_state_machine():
    scenarios = [
        ({}, proposal_doc("scale_deployment", 3), FakeMetrics()),  # Resolved
        (
            {},
            proposal_doc("scale_deployment", 3),
            FakeMetrics(p95=0.9),
        ),  # Compensating
        ({}, proposal_doc(), FakeMetrics()),  # restart, Resolved
        (
            {},
            proposal_doc(),
            FakeMetrics(p95=0.9),
        ),  # restart, Compensating, no scale-back
        ({"level": "1"}, proposal_doc(), FakeMetrics()),  # Recommended
        (
            {},
            proposal_doc("scale_deployment", 9),
            FakeMetrics(),
        ),  # Blocked at the gate
    ]
    seen = set()
    for kwargs, doc, metrics in scenarios:
        rig = Rig(metrics=metrics, **kwargs)
        rig.proposed(doc, level=int(kwargs.get("level", 3)))
        rig.run(ticks=80, seconds=10)
        edges = edges_of(rig)
        assert all(new in LEGAL[old] for old, new in edges), edges
        seen |= edges
    assert ("Verifying", "Resolved") in seen and ("Verifying", "Compensating") in seen
    verifying_to = {new for old, new in seen if old == "Verifying"}
    assert verifying_to == {"Resolved", "Compensating"}


def test_a_failed_scale_is_always_compensated_before_it_is_handed_off():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig, seconds=250)
    assert rig.status()["phase"] == "Compensating"
    rig.run()
    path = [t[3] for t in rig.rec.transitions]
    assert path == ["Executing", "Verifying", "Compensating", "Escalated"]
    assert rig.api.replicas == 2  # the previous count, restored by a gated request


def test_the_compensator_never_runs_for_a_restart():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    rig.proposed(proposal_doc())
    rig.run(ticks=80, seconds=10)
    assert [t[3] for t in rig.rec.transitions][-2:] == ["Compensating", "Escalated"]
    assert all(r["intent"].action_class == "executed" for r in rig.rec.actions.values())
