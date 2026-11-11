"""The act path on fakes: Safety Gate stages 1-7, write-ahead, Audit-Id, Kill Switch, execution,
verification and compensation. Live checks (a real API server, Kyverno, the PDB) are pending."""

import asyncio
import datetime

import pytest

from fakes import T0
from fakes_act import (
    DEP,
    NS,
    FakeMetrics,
    proposal_doc,
    res,
)
from nexus_operator.breaker import (
    COOLDOWN,
    OVERALL_LIMIT,
    PER_TARGET_LIMIT,
    breaker_open,
    in_cooldown,
)
from nexus_operator.executor import classify
from nexus_operator.recorder import ActionRecord
from rig import TERMINAL, Rig, ended, verify  # noqa: F401


# ---------------------------------------------------------------- Safety Gate, stages 1-7
@pytest.mark.parametrize(
    "doc, want",
    [
        (proposal_doc(schema="proposal/v2"), ("Blocked", "schema_invalid")),
        (proposal_doc(unexpected="x"), ("Blocked", "schema_invalid")),
        (proposal_doc(confidence="high"), ("Blocked", "schema_invalid")),
        (proposal_doc("rollback"), ("Blocked", "not_in_catalogue")),
        (proposal_doc("delete_pod"), ("Blocked", "not_in_catalogue")),
        (proposal_doc("scale_deployment", 0), ("Blocked", "bounds_exceeded")),
        (proposal_doc("scale_deployment", 6), ("Blocked", "bounds_exceeded")),
        (proposal_doc("scale_deployment", 40), ("Blocked", "bounds_exceeded")),
        (proposal_doc("scale_deployment"), ("Blocked", "bounds_exceeded")),
        (proposal_doc("restart_workload", 3), ("Blocked", "bounds_exceeded")),
    ],
)
def test_malformed_or_out_of_envelope_proposals_never_reach_the_api(doc, want):
    rig = Rig()
    rig.proposed(doc)
    rig.run()
    assert ended(rig) == want
    assert rig.api.requests == []
    assert rig.rec.actions == {}


def test_change_of_more_than_two_is_out_of_bounds():
    rig = Rig(replicas=1)
    rig.proposed(proposal_doc("scale_deployment", 4))
    rig.run()
    assert ended(rig) == ("Blocked", "bounds_exceeded")
    assert rig.api.requests == []


@pytest.mark.parametrize(
    "ns, name",
    [("nexus-data", DEP), ("nexus-prod", DEP), (NS, "dependency-db")],
)
def test_redirected_target_is_a_mismatch(ns, name):
    doc = proposal_doc()
    doc["action"]["target"] = {"namespace": ns, "kind": "Deployment", "name": name}
    rig = Rig()
    rig.proposed(doc)
    rig.run()
    assert ended(rig) == ("Blocked", "target_mismatch")
    assert rig.api.requests == []


def test_first_failing_stage_wins():
    # out of bounds (stage 3) and kill switch halted (stage 5): stage 3 reports
    rig = Rig(killswitch={"data": {"state": "halted"}})
    rig.proposed(proposal_doc("scale_deployment", 6))
    rig.run()
    assert ended(rig) == ("Blocked", "bounds_exceeded")
    assert rig.status()["gate"]["firstFailingStage"] == 3
    assert [g[1:] for g in rig.rec.gates] == [(1, "pass"), (2, "pass"), (3, "fail")]


def test_autonomy_precheck_reads_the_live_label():
    rig = Rig(level="1")  # the label dropped since intake recorded L3
    rig.proposed(proposal_doc(), level=3)
    rig.run()
    assert ended(rig) == ("Blocked", "autonomy_denied")
    assert rig.api.requests == []


@pytest.mark.parametrize("ks", [{"data": {"state": "halted"}}, {"data": {}}, None])
def test_kill_switch_halted_or_missing_blocks(ks):
    rig = Rig(killswitch=ks)
    rig.proposed(proposal_doc())
    rig.run()
    assert ended(rig) == ("Blocked", "killswitch")
    assert rig.api.requests == []


def test_kill_switch_halted_stops_a_restart_between_two_evictions():
    rig = Rig(replicas=3)
    rig.api.auto_ready = False
    rig.proposed(proposal_doc())
    rig.tick()  # Proposed -> Executing
    rig.tick()  # first eviction
    assert len(rig.api.mutations()) == 1
    rig.api.configmaps["nexus-killswitch"] = {"data": {"state": "halted"}}
    rig.api.heal()
    rig.run()
    assert ended(rig) == ("Blocked", "killswitch")
    assert len(rig.api.mutations()) == 1


def test_rollout_in_progress_fails_the_precondition():
    rig = Rig()
    rig.api.rolling = True
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert ended(rig) == ("Blocked", "precondition_failed")
    assert rig.api.requests == []


def test_restart_needs_two_ready_replicas():
    rig = Rig(replicas=2)
    rig.api.pods[0]["status"]["conditions"][0]["status"] = "False"
    rig.proposed(proposal_doc())
    rig.run()
    assert ended(rig) == ("Blocked", "precondition_failed")


def test_dry_run_denial_blocks_and_sends_only_the_dry_run():
    rig = Rig()
    rig.api.refuse = lambda verb, args, dry: (
        res(403, "a1", "denied by policy") if dry else None
    )
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert ended(rig) == ("Blocked", "dry_run_denied")
    assert rig.api.mutations() == []
    assert len(rig.api.requests) == 1


def test_dry_run_transport_error_fails_closed():
    rig = Rig()
    rig.api.raise_on = lambda *a: TimeoutError("slow") if a[2] else None
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert ended(rig) == ("Blocked", "dry_run_denied")
    assert rig.api.mutations() == []


def test_the_gate_reads_only_this_incidents_proposal_row():
    """ADR-039: an Incident whose hash matches only another Incident's row waits, then times out;
    before, the Gate read the other Incident's row (2026-11-07, six nexus-prod Incidents)."""
    rig = Rig()
    rig.proposed(proposal_doc(), name="first")
    rig.proposed(proposal_doc(), name="inc")
    rig.inc()["status"]["proposal"]["hash"] = "sha256:first"  # same content, same hash
    del rig.rec.proposals[("uid-inc", "sha256:inc")]  # its own row was never written
    rig.tick()
    assert rig.status()["phase"] == "Proposed"
    rig.tick(seconds=31)
    assert ended(rig) == ("Escalated", "gate_timeout")
    assert ("uid-first", "sha256:first") in rig.rec.proposals


def test_the_gate_times_out_into_escalated():
    rig = Rig()
    rig.proposed(proposal_doc())
    del rig.rec.proposals[("uid-inc", "sha256:inc")]  # the proposal never arrives
    rig.tick(seconds=31)
    assert ended(rig) == ("Escalated", "gate_timeout")


# ---------------------------------------------------------------- levels and non-mutating ends
def test_l1_recommends_and_sends_nothing():
    rig = Rig(level="1")
    rig.proposed(proposal_doc(), level=1)
    rig.run()
    assert ended(rig) == ("Recommended", "level_recommend")
    assert rig.api.requests == []


def test_l1_still_validates_the_proposal():
    rig = Rig(level="1")
    rig.proposed(proposal_doc("scale_deployment", 40), level=1)
    rig.run()
    assert ended(rig) == ("Blocked", "bounds_exceeded")


def test_l2_stays_escalated_and_sends_nothing():
    rig = Rig(level="2")
    rig.proposed(proposal_doc(), level=2)
    rig.run()
    assert ended(rig) == ("Escalated", "autonomy_denied")
    assert rig.api.requests == []


def test_escalate_and_no_action_and_abstain():
    for doc, level, want in [
        (proposal_doc("escalate"), 3, ("Escalated", "proposed_escalation")),
        (proposal_doc("escalate"), 1, ("Escalated", "proposed_escalation")),
        (proposal_doc("no_action"), 3, ("Dismissed", "no_action_required")),
        (proposal_doc("no_action"), 2, ("Dismissed", "no_action_required")),
        (proposal_doc("no_action"), 1, ("Recommended", "level_recommend")),
        (proposal_doc(abstain=True), 3, ("Escalated", "abstain")),
    ]:
        rig = Rig(level=str(level))
        rig.proposed(doc, level=level)
        rig.run()
        assert ended(rig) == want
        assert rig.api.requests == []


def test_escalation_hand_off_carries_the_recommendation():
    rig = Rig()
    rig.proposed(proposal_doc("escalate"))
    rig.run()
    assert rig.status()["proposal"]["recommendation"]["kind"] == "investigate"


# ---------------------------------------------------------------- advisory checks off
def test_advisory_off_leaves_the_decision_to_the_enforcement_plane():
    rig = Rig(advisory=False)
    rig.api.refuse = lambda verb, args, dry: (
        res(403, "a7", 'admission webhook "nexus-scale-bounds" denied the request: K2')
        if args[2] == 40
        else None
    )
    rig.proposed(
        proposal_doc("scale_deployment", 40)
    )  # out of bounds, nothing advisory stops it
    rig.run()
    assert ended(rig) == ("Blocked", "admission_denied")
    assert rig.api.mutations() == [("scale", (NS, DEP, 40), False)]
    assert rig.api.replicas == 2
    assert [g[2] for g in rig.rec.gates] == ["pass"]  # skipped stages are not recorded


def test_advisory_off_still_needs_a_parseable_proposal():
    rig = Rig(advisory=False)
    rig.proposed(proposal_doc(schema="nope"))
    rig.run()
    assert ended(rig) == ("Blocked", "schema_invalid")
    assert rig.api.requests == []


# ---------------------------------------------------------------- write-ahead and Audit-Id
def test_intent_is_written_before_the_request_and_audit_id_is_kept():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    assert rig.rec.order == ["intent:inc-1", "request:scale", "complete:inc-1"]
    row = rig.rec.actions["inc-1"]
    assert (row["audit_id"], row["http_status"], row["result"]) == (
        "audit-2",
        200,
        "applied",
    )
    intent = row["intent"]
    assert rig.status()["execution"]["auditIds"] == [
        "audit-2"
    ]  # audit-1 was the dry-run
    assert intent.previous_state == {"replicas": 2}
    assert intent.action_id == "scale_deployment" and intent.parameters == {
        "replicas": 3
    }
    assert intent.action_class == "executed"


def test_recorder_down_blocks_before_any_request():
    rig = Rig()
    rig.rec.fail_intent = True
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert ended(rig) == ("Blocked", "recorder_unavailable")
    assert rig.api.mutations() == []
    assert rig.api.replicas == 2


def test_recorder_down_blocks_a_restart_too():
    rig = Rig()
    rig.rec.fail_intent = True
    rig.proposed(proposal_doc())
    rig.run()
    assert ended(rig) == ("Blocked", "recorder_unavailable")
    assert rig.api.mutations() == []
    assert len(rig.api.pods) == 2 and {p["metadata"]["uid"] for p in rig.api.pods} == {
        "pod-uid-1",
        "pod-uid-2",
    }


def test_completion_write_failure_keeps_the_audit_id_and_never_resends():
    rig = Rig()
    rig.rec.fail_complete = True
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)  # Executing -> request sent -> Verifying
    s = rig.status()
    assert s["phase"] == "Verifying"
    assert s["execution"]["completion"]["auditId"] == "audit-2"
    rig.rec.fail_complete = False
    rig.tick()
    assert len(rig.api.mutations()) == 1
    assert rig.rec.actions["inc-1"]["audit_id"] == "audit-2"


@pytest.mark.parametrize(
    "out, phase, reason",
    [
        (
            res(403, "a", 'User "x" cannot patch resource "deployments/scale"'),
            "Blocked",
            "rbac_denied",
        ),
        (
            res(403, "a", 'admission webhook "k" denied the request'),
            "Blocked",
            "admission_denied",
        ),
        (
            res(500, "a", 'failed calling webhook "k": connection refused'),
            "Blocked",
            "admission_denied",
        ),
        (
            res(429, "a", "Cannot evict pod: would violate the PDB"),
            "Blocked",
            "pdb_denied",
        ),
        (res(503, "a", "unavailable"), "Escalated", "execution_timeout"),
    ],
)
def test_enforcement_plane_answers_are_classified_and_recorded(out, phase, reason):
    rig = Rig()
    rig.api.refuse = lambda verb, args, dry: None if dry else out
    rig.proposed(proposal_doc())
    rig.run()
    assert ended(rig) == (phase, reason)
    assert rig.rec.actions["inc-1"]["audit_id"] == "a"
    assert rig.rec.actions["inc-1"]["http_status"] == out.status
    assert rig.rec.actions["inc-1"]["result"].startswith(("applied", "denied"))
    assert len(rig.api.mutations()) == 1  # a refusal is final: no retry


def test_a_request_that_raises_is_recorded_and_escalated():
    rig = Rig()
    rig.api.raise_on = lambda verb, args, dry: None if dry else TimeoutError("slow")
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert ended(rig) == ("Escalated", "execution_timeout")
    assert rig.rec.actions["inc-1"]["result"].startswith("transport_error")


def test_classify_maps_the_three_enforcement_layers():
    assert classify(res(200, "a"))[0] == "admitted"
    assert classify(res(403, "a", "forbidden")) == ("denied", "rbac_denied")
    assert classify(res(403, "a", "admission webhook x denied")) == (
        "denied",
        "admission_denied",
    )
    assert classify(res(429, "a")) == ("denied", "pdb_denied")


# ---------------------------------------------------------------- execution and resume
def test_a_restart_evicts_one_pod_at_a_time_and_waits_for_ready():
    rig = Rig(replicas=2)
    rig.api.auto_ready = False
    rig.proposed(proposal_doc())
    rig.tick()  # Proposed -> Executing
    rig.tick()  # first eviction
    assert len(rig.api.mutations()) == 1
    for _ in range(3):
        rig.tick(seconds=5)  # the replacement is not Ready: no second eviction
    assert len(rig.api.mutations()) == 1
    rig.api.heal()
    rig.tick(seconds=5)
    assert len(rig.api.mutations()) == 2
    rig.tick()  # nothing left to evict
    assert rig.status()["phase"] == "Verifying"
    assert rig.status()["execution"]["idempotencyKey"] == "inc-2"
    assert len(rig.status()["execution"]["auditIds"]) == 2
    assert {p["metadata"]["uid"] for p in rig.api.pods}.isdisjoint(
        {"pod-uid-1", "pod-uid-2"}
    )


def test_resume_does_not_repeat_a_scale_that_already_took_effect():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.tick()  # -> Executing
    pending = {"key": "inc-1", "kind": "scale", "replicas": 3, "previous": 2}
    rig.api.set_status("inc", {"execution": {"seq": 1, "pending": pending}})
    asyncio.run(rig.rec.begin_action(rig.act._intent(rig.inc(), pending)))
    rig.api.replicas = (
        3  # the request landed, then the operator died before storing the result
    )
    rig.tick()
    assert rig.api.mutations() == []
    assert rig.status()["phase"] == "Verifying"
    assert rig.status()["execution"].get("pending") is None


def test_resume_does_not_repeat_an_eviction_that_already_took_effect():
    rig = Rig()
    rig.proposed(proposal_doc())
    rig.tick()
    rig.api.pods = rig.api.pods[1:]  # pod-uid-1 is gone
    rig.api._new_pod(ready=True)
    pending = {
        "key": "inc-1",
        "kind": "evict",
        "pod": "sample-api-1",
        "uid": "pod-uid-1",
    }
    rig.api.set_status("inc", {"execution": {"seq": 1, "pending": pending}})
    asyncio.run(rig.rec.begin_action(rig.act._intent(rig.inc(), pending)))
    rig.tick()
    assert [m for m in rig.api.mutations() if m[1][2] == "pod-uid-1"] == []
    assert "pod-uid-1" in rig.status()["execution"]["evicted"]


def test_resume_sends_an_unapplied_request_under_the_next_key():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.tick()
    pending = {"key": "inc-1", "kind": "scale", "replicas": 3, "previous": 2}
    rig.api.set_status("inc", {"execution": {"seq": 1, "pending": pending}})
    rig.tick()
    assert rig.api.mutations() == [("scale", (NS, DEP, 3), False)]
    assert list(rig.rec.actions) == ["inc-2"]  # a recorded key is never sent again


def test_executing_times_out_into_escalated():
    rig = Rig(replicas=2)
    rig.api.auto_ready = False
    rig.proposed(proposal_doc())
    rig.tick()
    rig.tick()  # first eviction; the replacement never becomes Ready
    rig.tick(seconds=301)
    assert ended(rig) == ("Escalated", "execution_timeout")


def test_every_transition_is_recorded_with_its_phase_sequence():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    got = [(t[1], t[2], t[3]) for t in rig.rec.transitions]
    assert got == [(4, "Proposed", "Executing"), (5, "Executing", "Verifying")]


# ---------------------------------------------------------------- verification
def test_scale_is_verified_against_the_raw_baseline_for_two_minutes():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    assert rig.status()["phase"] == "Verifying"
    rig.tick(seconds=10)
    rig.tick(seconds=100)
    assert rig.status()["phase"] == "Verifying"  # not yet two minutes of good readings
    verify(rig)
    assert ended(rig) == ("Resolved", "remediation_verified")
    assert rig.rec.verifications[("uid-inc", "scale_v1")][0] == "met"


def test_scale_that_leaves_p95_above_baseline_is_compensated():
    rig = Rig(metrics=FakeMetrics(p95=0.5))  # raw p95 is ten times the baseline
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig, seconds=250)
    assert rig.status()["phase"] == "Compensating"
    rig.run()
    assert ended(rig) == ("Escalated", "verification_failed")
    assert rig.api.replicas == 2  # scaled back to the recorded previous count
    comp = rig.rec.actions["inc-c1"]
    assert comp["intent"].action_class == "compensation" and comp["audit_id"]


def test_a_clear_alert_is_not_a_recovery():
    # the verifier never consults an alert: only the raw p95 against the recorded baseline decides
    rig = Rig(metrics=FakeMetrics(p95=0.2))
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig, seconds=250)
    rig.run()
    assert rig.status()["reason"] == "verification_failed"


def test_cpu_per_pod_at_the_limit_fails_scale_verification():
    rig = Rig(metrics=FakeMetrics(cpu=2.7))  # 0.9 per pod of a 1 core limit
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig, seconds=250)
    rig.run()
    assert ended(rig) == ("Escalated", "verification_failed")


def test_no_baseline_never_verifies():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3), baseline=None)
    rig.run(ticks=2)
    verify(rig, seconds=250)
    rig.run()
    assert ended(rig) == ("Escalated", "verification_failed")


def test_no_metrics_source_never_verifies():
    rig = Rig()
    rig.act.verifier.metrics = None
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig, seconds=250)
    rig.run()
    assert ended(rig) == ("Escalated", "verification_failed")


def test_restart_is_verified_after_sixty_stable_seconds():
    rig = Rig()
    rig.proposed(proposal_doc())
    rig.run(ticks=4)
    assert rig.status()["phase"] == "Verifying"
    rig.tick(seconds=5)
    rig.tick(seconds=30)
    assert rig.status()["phase"] == "Verifying"
    rig.tick(seconds=40)
    assert ended(rig) == ("Resolved", "remediation_verified")
    assert rig.rec.verifications[("uid-inc", "restart_v1")][0] == "met"


def test_a_new_container_restart_resets_the_restart_hold():
    rig = Rig()
    rig.proposed(proposal_doc())
    rig.run(ticks=4)
    rig.tick(seconds=5)
    rig.api.pods[0]["status"]["containerStatuses"][0]["restartCount"] = (
        1  # a crash loop
    )
    rig.tick(seconds=40)
    rig.tick(seconds=40)
    assert rig.status()["phase"] == "Verifying"


def test_restart_failure_escalates_without_compensation():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    rig.proposed(proposal_doc())
    rig.run(ticks=4)
    verify(rig, seconds=200, step=10)
    rig.run()
    assert ended(rig) == ("Escalated", "verification_failed")
    assert all(not k.endswith("c1") for k in rig.rec.actions)


# ---------------------------------------------------------------- compensation is itself gated
def failed_scale(rig):
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig, seconds=250)
    assert rig.status()["phase"] == "Compensating"


def test_compensation_under_a_halted_kill_switch_is_blocked():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    failed_scale(rig)
    rig.api.configmaps["nexus-killswitch"] = {"data": {"state": "halted"}}
    n = len(rig.api.mutations())
    rig.run()
    assert ended(rig) == ("Escalated", "compensation_blocked")
    assert len(rig.api.mutations()) == n
    assert rig.api.replicas == 3


def test_compensation_refused_by_the_enforcement_plane_is_blocked():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    failed_scale(rig)
    rig.api.refuse = lambda verb, args, dry: res(403, "x", "forbidden")
    rig.run()
    assert ended(rig) == ("Escalated", "compensation_blocked")


def test_compensation_error_is_compensation_failed():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    failed_scale(rig)
    rig.api.raise_on = lambda verb, args, dry: None if dry else TimeoutError("slow")
    rig.run()
    assert ended(rig) == ("Escalated", "compensation_failed")


def test_compensation_times_out():
    rig = Rig(metrics=FakeMetrics(p95=0.9))
    failed_scale(rig)
    rig.tick(seconds=121)
    assert ended(rig) == ("Escalated", "compensation_failed")


# ---------------------------------------------------------------- Circuit Breaker, cool-down
def row(
    incident, minutes_ago, now, *, target=f"{NS}/{DEP}", action="scale_deployment", **kw
):
    return ActionRecord(
        incident,
        action,
        target,
        now - datetime.timedelta(minutes=minutes_ago),
        kw.pop("result", "applied"),
        kw.pop("action_class", "executed"),
    )


NOW = T0 + datetime.timedelta(hours=2)
T = f"{NS}/{DEP}"


def test_breaker_opens_at_two_per_target_in_fifteen_minutes():
    rows = [row("a", 14, NOW), row("b", 8, NOW)]
    assert PER_TARGET_LIMIT == 2
    assert breaker_open(rows, NOW, 0, T)
    assert breaker_open(rows[:1], NOW, 0, T) is None
    assert breaker_open([row("a", 16, NOW), row("b", 8, NOW)], NOW, 0, T) is None


def test_breaker_opens_at_six_overall_in_an_hour():
    rows = [
        row(f"i{i}", 50 - i, NOW, target=f"{NS}/w{i}") for i in range(OVERALL_LIMIT)
    ]
    assert breaker_open(rows, NOW, 0, f"{NS}/other")
    assert breaker_open(rows[:-1], NOW, 0, f"{NS}/other") is None


def test_breaker_counts_actions_not_requests_and_skips_denied_and_compensation():
    rows = [
        row("a", 10, NOW),
        row("a", 9, NOW),
    ]  # two evictions of one restart: one action
    rows += [
        row("b", 5, NOW, result="denied"),
        row("c", 5, NOW, action_class="compensation"),
    ]
    assert breaker_open(rows, NOW, 0, T) is None


def test_breaker_ignores_rows_before_the_epoch():
    rows = [row("a", 14, NOW), row("b", 8, NOW)]
    epoch = int((NOW - datetime.timedelta(minutes=10)).timestamp())
    assert breaker_open(rows, NOW, epoch, T) is None


def test_cooldown_is_five_minutes_on_the_same_target_by_another_incident():
    assert COOLDOWN == datetime.timedelta(minutes=5)
    rows = [row("a", 4, NOW)]
    assert in_cooldown(rows, NOW, 0, T, "b")
    assert not in_cooldown(rows, NOW, 0, T, "a")  # its own action is not "induced"
    assert not in_cooldown([row("a", 6, NOW)], NOW, 0, T, "b")
    assert not in_cooldown(rows, NOW, 0, f"{NS}/other", "b")


def test_open_breaker_and_cooldown_escalate_in_the_gate():
    rig = Rig()
    now = rig.clock()
    rig.rec.seed("x", T, 2, now)
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert ended(rig) == ("Escalated", "remediation_induced")
    assert rig.api.requests == []
    rig = Rig()
    now = rig.clock()
    rig.rec.seed("x", T, 10, now)
    rig.rec.seed("y", T, 7, now)
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert ended(rig) == ("Escalated", "circuit_open")
    assert rig.api.requests == []


# ---------------------------------------------------------------- the Loop drives the act path
def test_the_loop_steps_incidents_in_act_phases_and_survives_a_failure():
    from nexus_operator.reconcile import Loop, Timing

    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.proposed(proposal_doc("scale_deployment", 3), name="other")
    calls = []
    real = rig.act.step

    async def step(incident):
        calls.append(incident["metadata"]["name"])
        if incident["metadata"]["name"] == "inc":
            raise RuntimeError("boom")
        await real(incident)

    rig.act.step = step
    loop = Loop(rig.api, Timing(), rig.act)
    assert asyncio.run(loop.tick()) == 2
    assert sorted(calls) == ["inc", "other"]
    assert rig.status("other")["phase"] == "Executing"


# ---------------------------------------------------------------- the recorder as the act path uses it
def test_incident_row_is_opened_and_closed_with_its_terminal_phase():
    rig = Rig()
    rig.proposed(proposal_doc("no_action"))
    rig.run()
    assert rig.rec.incidents["uid-inc"]["target"] == f"{NS}/{DEP}"
    assert rig.rec.incidents["uid-inc"]["terminal"] == (
        "Dismissed",
        "no_action_required",
    )


def test_a_verification_is_recorded_once_per_criteria_not_per_tick():
    rig = Rig()
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run(ticks=2)
    verify(rig)
    assert list(rig.rec.verifications) == [("uid-inc", "scale_v1")]


def test_a_missing_proposal_hash_waits_then_escalates():
    rig = Rig()
    rig.proposed(proposal_doc())
    rig.api.set_status("inc", {"proposal": {"hash": None}})
    rig.tick()
    assert rig.status()["phase"] == "Proposed"
    rig.tick(seconds=31)
    assert ended(rig) == ("Escalated", "gate_timeout")
    assert rig.api.requests == []


def test_the_placeholder_recorder_means_nothing_is_ever_sent():
    from nexus_operator.recorder import UnavailableRecorder

    rig = Rig()
    rig.act.recorder = rig.act.gate.recorder = rig.act.executor.recorder = (
        UnavailableRecorder()
    )
    rig.proposed(proposal_doc("scale_deployment", 3))
    rig.run()
    assert rig.api.mutations() == []
    assert rig.api.replicas == 2
