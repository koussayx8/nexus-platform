"""escalation.suggestedChange and escalation.runbookGap (ADR-044): optional strings, shown capped on
the Incident, never applied: an escalation with a suggestion still mutates nothing."""

from fakes_act import proposal_doc
from nexus_operator import proposal
from rig import Rig, ended

YAML = "readinessProbe:\n  httpGet:\n    path: /ready\n    port: 8000\n"


def escalation(**extra):
    doc = proposal_doc("escalate")
    doc["escalation"] |= extra
    return doc


def test_the_fields_are_optional_strings():
    assert proposal.validate(proposal_doc("escalate")) == []
    assert (
        proposal.validate(escalation(suggestedChange=YAML, runbookGap="no probe path"))
        == []
    )
    errors = proposal.validate(
        escalation(suggestedChange={"kind": "patch"}, runbookGap=3)
    )
    assert "escalation.suggestedChange must be a string" in errors
    assert "escalation.runbookGap must be a string" in errors


def test_a_suggestion_is_shown_and_never_applied():
    rig = Rig()
    rig.proposed(
        escalation(suggestedChange=YAML, runbookGap="rb-bad-rollout: no probe path")
    )
    rig.run()
    assert ended(rig) == ("Escalated", "proposed_escalation")
    shown = rig.status()["proposal"]["escalation"]
    assert shown["suggestedChange"] == YAML and shown["runbookGap"].startswith(
        "rb-bad-rollout"
    )
    assert rig.api.requests == []  # nothing was sent to the cluster, dry-run or real
    assert rig.status().get("execution") is None


def test_long_text_is_cut_on_the_incident_only():
    caps = proposal.ESCALATION_TEXT
    doc = escalation(suggestedChange="y" * 10000, runbookGap="g" * 5000)
    rig = Rig()
    rig.proposed(doc)
    rig.run()
    shown = rig.status()["proposal"]["escalation"]
    assert shown["suggestedChange"] == "y" * caps["suggestedChange"] + " [cut]"
    assert shown["runbookGap"] == "g" * caps["runbookGap"] + " [cut]"
    assert (
        len(doc["escalation"]["suggestedChange"]) == 10000
    )  # the proposal itself is untouched
