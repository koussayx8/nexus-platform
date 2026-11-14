"""Strict xfail tests for docs/reviews/2026-11-10-authority-boundary.md.

Each test states the behaviour the review argues for and fails today; the finding's id is in the
reason. They are strict: when a fix lands, the test passes, pytest reports XPASS as a failure, and
the marker is removed in that fix's PR. Nothing here changes the operator.
"""

import json
import time

import pytest

from fakes_act import DEP, NS, FakeMetrics, proposal_doc
from nexus_operator import proposal, redact
from nexus_operator.gate import selector_of
from rig import Rig, ended


def review(finding: str):
    return pytest.mark.xfail(strict=True, reason=f"authority-boundary review {finding}")


# ---------------------------------------------------------------- A1: quadratic redaction
# Fixed (redact._RUNS): a normal test now. The 1 MB lines are in test_redact_linear.py.
def test_redacting_one_long_log_line_takes_bounded_time():
    line = "a." * 4000  # 8,000 characters; a container may log far longer lines
    started = time.perf_counter()
    redact.redact_text(line, {})
    assert time.perf_counter() - started < 0.5


# ---------------------------------------------------------------- A2: a hang that stays hung
@review("A2: restart_v1 never reads the in-flight signal that ADR-039's branch acts on")
def test_a_restart_is_not_verified_while_requests_still_pile_up():
    # A full hang that the restart did not clear: the p95 of the few completed requests is flat,
    # in-flight Z is still 80 and the completed rate is still far below its baseline.
    rig = Rig(metrics=FakeMetrics(p95=0.05, inflight=80.0, requestRate=-62.0))
    rig.proposed(proposal_doc())
    rig.run(ticks=60)
    assert ended(rig) != ("Resolved", "remediation_verified")


# ---------------------------------------------------------------- A3: response lost
@review("A3: a transport error after the request took effect ends without verification")
def test_a_scale_that_took_effect_is_verified_even_when_its_response_is_lost():
    rig = Rig()
    applied = rig.api.patch_scale

    async def lost_response(namespace, name, replicas, dry_run=False):
        out = await applied(namespace, name, replicas, dry_run)
        if not dry_run:
            raise TimeoutError("the response never arrived")
        return out

    rig.api.patch_scale = lost_response
    rig.proposed(proposal_doc("scale_deployment", 4))
    rig.run()
    assert rig.api.replicas == 4  # the request did take effect
    assert ("uid-inc", "scale_v1") in rig.rec.verifications


# ---------------------------------------------------------------- A4: poison values
@review("A4: a non-finite confidence passes proposal/v1 and cannot be stored as JSON")
@pytest.mark.parametrize("raw", ["NaN", "Infinity", "1e999"])
def test_a_proposal_that_validates_is_strict_json(raw):
    doc = proposal_doc()
    doc["confidence"] = json.loads(raw)  # what json.loads gives for the Reasoner's text
    assert proposal.validate(doc) != []


# ---------------------------------------------------------------- A5: non-idempotent redaction
@review("A5: one redaction pass leaves an address that the second pass finds")
def test_redaction_is_idempotent_on_overlapping_addresses():
    out, counts = redact.redact_bundle({"logs": {"current": ["::999.1.1.1.2"]}})
    assert redact.unredacted(out | {"redaction": {"counts": counts}}) == []


# ---------------------------------------------------------------- A6: recorded verdict
@review(
    "A6: the first gate verdict stays recorded when a later run of the gate decides"
)
def test_the_recorded_gate_decisions_are_the_ones_that_were_acted_on():
    rig = Rig()
    rig.api.rolling = True  # stage 6 fails: a rollout is in progress
    rig.proposed(proposal_doc("scale_deployment", 3))
    left = {"conflicts": 1}

    def conflict(name):
        if left["conflicts"]:
            left["conflicts"] -= 1
            rig.api.incidents[name]["metadata"]["resourceVersion"] = "moved"

    rig.api.before_write = conflict  # the status write of the denial loses a 409
    rig.tick()
    rig.api.rolling = False  # by the next tick the rollout is over
    rig.run()
    assert rig.api.mutations() == [("scale", (NS, DEP, 3), False)]
    assert ("uid-inc", 6, "fail") not in rig.rec.gates


# ---------------------------------------------------------------- A7: hash binding
@review("A7: the Gate never recomputes the hash of the document it reads")
def test_a_stored_document_that_does_not_hash_to_the_status_hash_is_refused():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))  # stored under sha256:inc
    stored = rig.rec.proposals[("uid-inc", "sha256:inc")]
    assert proposal.hash_of(stored) != "sha256:inc"
    rig.run()
    assert rig.api.mutations() == []
    assert ended(rig)[0] == "Blocked"


# ---------------------------------------------------------------- A8: nested unknown keys
@review("A8: proposal/v1 is strict at the top level only")
@pytest.mark.parametrize(
    "where", ["action", "hypothesis", "escalation", "recommendation"]
)
def test_unknown_nested_keys_fail_the_schema(where):
    doc = proposal_doc("escalate")
    target = {
        "action": doc["action"],
        "hypothesis": doc["hypothesis"],
        "escalation": doc["escalation"],
        "recommendation": doc["escalation"]["recommendation"],
    }[where]
    target["injected"] = {"anything": ["the", "Reasoner", "likes"]}
    assert proposal.validate(doc) != []


# ---------------------------------------------------------------- A9: level change mid-restart
@review(
    "A9: only the Kill Switch is read again between two evictions (K1 is the M3 twin)"
)
def test_lowering_the_namespace_level_stops_a_restart_between_two_evictions():
    rig = Rig(replicas=2)
    rig.proposed(proposal_doc())
    rig.tick()  # the gate passed at L3 -> Executing
    rig.tick()  # first eviction
    rig.api.namespaces[NS] = {"nexus.io/autonomy-level": "1"}
    rig.run()
    assert len(rig.api.mutations()) == 1


# ---------------------------------------------------------------- A10: resume and the Kill Switch
@review("A10: a resumed pending request is sent again without reading the Kill Switch")
def test_a_resumed_request_is_not_sent_while_the_kill_switch_is_halted():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.tick()  # -> Executing
    pending = {"key": "inc-1", "kind": "scale", "replicas": 3, "previous": 2}
    rig.api.set_status("inc", {"execution": {"seq": 1, "pending": pending}})
    rig.api.configmaps["nexus-killswitch"] = {"data": {"state": "halted"}}
    rig.tick()
    assert rig.api.mutations() == []


# ---------------------------------------------------------------- A11: selector
@review("A11: a Deployment without matchLabels selects every pod in the namespace")
def test_a_selector_without_match_labels_is_not_the_empty_selector():
    deployment = {
        "spec": {
            "selector": {
                "matchExpressions": [{"key": "app", "operator": "In", "values": [DEP]}]
            }
        }
    }
    assert selector_of(deployment) != ""


# ---------------------------------------------------------------- A14: compensation bounds
@review("A14: compensation restores the recorded count even when it is outside [1, 5]")
def test_compensation_never_sends_a_replica_count_outside_the_catalogue_bounds():
    rig = Rig(replicas=0, metrics=FakeMetrics(p95=9.0))  # p95 never returns to baseline
    rig.proposed(proposal_doc("scale_deployment", 2))  # 0 -> 2 is within the step bound
    rig.run(ticks=80)
    sent = [args[2] for verb, args, dry in rig.api.requests if verb == "scale"]
    assert sent and all(1 <= n <= 5 for n in sent)


# ---------------------------------------------------------------- A15: check-then-act
@review("A15: the cool-down reads intent rows that a passing gate has not written yet")
def test_two_incidents_on_one_target_do_not_both_act_in_the_same_minute():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3), name="a")
    rig.proposed(proposal_doc("scale_deployment", 3), name="b")
    for _ in range(3):
        rig.tick("a")
        rig.tick("b")
    acted = {key.rsplit("-", 1)[0] for key in rig.rec.actions}
    assert len(acted) == 1
