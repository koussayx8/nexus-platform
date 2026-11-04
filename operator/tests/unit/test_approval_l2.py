"""The operator's L2 states (spec §7, §9 stage 8, §16A): AwaitingApproval, approval validation,
then Executing or Rejected / Expired / Blocked. Fakes only: K3 and K5 are simulated by the fake
cluster's `refuse` hook and the CLI tests in platform/policies/tests; live checks are pending."""

import datetime

import pytest

from fakes_act import proposal_doc, res
from nexus_operator import approval, config
from nexus_operator.model import iso, parse_time
from rig import Rig, ended

TTL = 900


def pin(rig, doc, **over):
    """A well-formed approval for the proposal, as nexusctl writes and K5 stamps it."""
    now = rig.clock()
    a = {
        "decision": "approved",
        "actionId": doc["action"]["id"],
        "parameters": dict(doc["action"]["parameters"]),
        "proposalHash": rig.status()["proposal"]["hash"],
        "expiresAt": iso(now + datetime.timedelta(seconds=TTL)),
        "approvedBy": "approver-1",
        "approvedAt": iso(now),
    }
    return a | over


def approve(rig, doc, name="inc", **over):
    rig.inc(name)["spec"]["approval"] = pin(rig, doc, **over)


def waiting(doc=None, **kw):
    rig = Rig(level="2", **kw)
    doc = doc or proposal_doc("scale_deployment", 4)
    rig.proposed(doc, level=2)
    rig.tick()
    assert rig.status()["phase"] == "AwaitingApproval"
    return rig, doc


# ---------------------------------------------------------------- entering AwaitingApproval
def test_gate_stages_4_to_6_run_before_waiting_and_the_dry_run_does_not():
    rig, _ = waiting()
    stages = {g[1] for g in rig.rec.gates}
    assert {1, 2, 3, 4, 5, 6} <= stages and 7 not in stages
    assert rig.api.requests == []


@pytest.mark.parametrize("level", ["0", "1", "3"])
def test_a_namespace_that_is_not_l2_never_waits(level):
    rig = Rig(level=level)
    rig.proposed(
        proposal_doc("scale_deployment", 4), level=2
    )  # the Incident says L2, the label differs
    rig.tick()
    assert ended(rig) == ("Blocked", "autonomy_denied")


def test_l2_gate_failures_end_before_waiting():
    rig = Rig(level="2")
    rig.proposed(
        proposal_doc("scale_deployment", 5), level=2
    )  # 2 -> 5 exceeds the step of 2
    rig.tick()
    assert ended(rig) == ("Blocked", "bounds_exceeded")


# ---------------------------------------------------------------- waiting
def test_no_decision_keeps_waiting_then_expires_at_approval_ttl():
    rig, _ = waiting()
    rig.tick(seconds=TTL - 5)
    assert rig.status()["phase"] == "AwaitingApproval"
    rig.tick(seconds=10)
    assert ended(rig) == ("Expired", "approval_expired")
    assert rig.api.requests == []


def test_a_custom_approval_ttl_is_honoured():
    rig = Rig(level="2")
    rig.act.approval_ttl_s = 60
    rig.proposed(proposal_doc("scale_deployment", 4), level=2)
    rig.tick()
    rig.tick(seconds=61)
    assert ended(rig) == ("Expired", "approval_expired")


# ---------------------------------------------------------------- approved
def test_a_valid_approval_is_revalidated_executed_and_verified():
    rig, doc = waiting()
    approve(rig, doc)
    rig.run()
    assert ended(rig)[0] == "Resolved"
    assert rig.api.replicas == 4
    dry, real = [r for r in rig.api.requests if r[2]], rig.api.mutations()
    assert len(dry) == 1 and len(real) == 1  # the dry-run came after the approval, once


def test_the_dry_run_after_approval_is_where_k3_is_previewed():
    rig, doc = waiting()
    rig.api.refuse = lambda verb, args, dry: (
        res(403, "a1", 'admission webhook "nexus-l2-approval" denied the request: K3')
        if dry
        else None
    )
    approve(rig, doc)
    rig.run()
    assert ended(rig) == ("Blocked", "dry_run_denied")
    assert rig.api.mutations() == []


def test_the_kill_switch_is_checked_again_after_approval():
    rig, doc = waiting()
    rig.api.configmaps["nexus-killswitch"] = {"data": {"state": "halted"}}
    approve(rig, doc)
    rig.tick()
    assert ended(rig) == ("Blocked", "killswitch")
    assert rig.api.requests == []


def test_a_level_changed_after_the_proposal_blocks_the_execution():
    rig, doc = waiting()
    rig.api.namespaces["nexus-dev"]["nexus.io/autonomy-level"] = "1"
    approve(rig, doc)
    rig.tick()
    assert ended(rig) == ("Blocked", "autonomy_denied")
    assert rig.api.requests == []


@pytest.mark.parametrize(
    "over, why",
    [
        ({"proposalHash": "sha256:other"}, "hash"),
        ({"proposalHash": ""}, "hash"),
        ({"actionId": "restart_workload", "parameters": {}}, "action"),
        ({"parameters": {"replicas": 5}}, "replicas"),
        ({"approvedBy": ""}, "unstamped"),
        ({"approvedAt": ""}, "unstamped"),
        ({"expiresAt": ""}, "no expiry"),
        ({"expiresAt": "tomorrow"}, "bad expiry"),
        ({"decision": "maybe"}, "decision"),
    ],
)
def test_an_approval_that_does_not_pin_the_proposal_is_blocked_unsent(over, why):
    rig, doc = waiting()
    approve(rig, doc, **over)
    rig.tick()
    assert ended(rig) == ("Blocked", "approval_rejected"), why
    assert rig.api.requests == []


def test_an_expiry_beyond_the_ttl_is_blocked_even_if_k5_were_bypassed():
    rig, doc = waiting()
    far = iso(rig.clock() + datetime.timedelta(seconds=TTL + 600))
    approve(rig, doc, expiresAt=far)
    rig.tick()
    assert ended(rig) == ("Blocked", "approval_rejected")
    assert rig.api.requests == []


def test_an_expired_approval_ends_expired_unsent():
    rig, doc = waiting()
    approve(rig, doc)
    rig.tick(seconds=TTL + 1)
    assert ended(rig) == ("Expired", "approval_expired")
    assert rig.api.requests == []


def test_a_rejection_ends_rejected_unsent():
    rig, _ = waiting()
    rig.inc()["spec"]["approval"] = {"decision": "rejected", "reason": "wrong target"}
    rig.tick()
    assert ended(rig) == ("Rejected", "approval_rejected")
    assert rig.api.requests == []


def test_restart_needs_no_parameters_and_is_approved_the_same_way():
    rig, doc = waiting(proposal_doc("restart_workload"), replicas=3)
    approve(rig, doc)
    rig.run()
    assert ended(rig)[0] in ("Resolved", "Escalated")
    assert rig.api.mutations()


def test_every_l2_transition_is_recorded():
    rig, doc = waiting()
    approve(rig, doc)
    rig.run()
    seq = [(t[1], t[2], t[3]) for t in rig.rec.transitions]
    assert (2, "Proposed", "AwaitingApproval") in seq or (
        3,
        "Proposed",
        "AwaitingApproval",
    ) in seq
    assert (4, "AwaitingApproval", "Executing") in seq
    assert 7 in {
        g[1] for g in rig.rec.gates
    }  # the dry-run is recorded once, after approval


# ---------------------------------------------------------------- the pure check and the config key
def inc(approval_, entered="2026-01-01T00:00:00Z", h="sha256:h"):
    return {
        "spec": {"approval": approval_} if approval_ is not None else {},
        "status": {
            "proposal": {"hash": h},
            "timestamps": {"awaitingapproval": entered},
        },
    }


def test_check_is_pure_and_pending_without_a_decision():
    doc = proposal_doc("scale_deployment", 4)
    at = parse_time("2026-01-01T00:05:00Z")
    assert approval.check(inc(None), doc, at, TTL).state == "pending"
    assert (
        approval.check(inc(None), doc, parse_time("2026-01-01T00:15:01Z"), TTL).state
        == "expired"
    )


def test_check_accepts_the_skew_between_k5_stamping_and_its_ceiling():
    doc = proposal_doc("scale_deployment", 4)
    a = {
        "decision": "approved",
        "actionId": "scale_deployment",
        "parameters": {"replicas": 4},
        "proposalHash": "sha256:h",
        "approvedBy": "a",
        "approvedAt": "2026-01-01T00:00:00Z",
        "expiresAt": "2026-01-01T00:15:03Z",
    }
    at = parse_time("2026-01-01T00:01:00Z")
    assert approval.check(inc(a), doc, at, TTL).state == "valid"
    assert (
        approval.check(
            inc(a | {"expiresAt": "2026-01-01T00:15:30Z"}), doc, at, TTL
        ).state
        == "invalid"
    )


def test_approval_ttl_config():
    cm = lambda **d: {"data": {"alertmanagerURL": "http://am:9093", **d}}
    assert config.parse(cm()).approval_ttl_s == 900
    assert config.parse(cm(approvalTTL="15m")).approval_ttl_s == 900
    assert config.parse(cm(approvalTTL="90s")).approval_ttl_s == 90
    assert config.parse(cm(approvalTTL="5m")).approval_ttl_s == 300
    for bad in ("16m", "0s", "1h", "fifteen", "-1m", "15"):
        with pytest.raises(config.ConfigError):
            config.parse(cm(approvalTTL=bad))
