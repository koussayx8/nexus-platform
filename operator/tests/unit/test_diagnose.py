"""The Evidence Collector and the Diagnosing step: a detected Incident reaches Proposed through R1,
and nothing unredacted reaches storage (Q1)."""

import json

import pytest

from fakes import T0
from fakes_act import DEP, NS, FakeMetrics
from nexus_operator import act as act_module
from nexus_operator import proposal
from nexus_operator.evidence import EvidenceCollector
from nexus_operator.model import iso
from rig import Rig, ended


def detected(rig, level=3, name="inc", status=None):
    rig.api.put(
        name,
        created=T0,
        status={"phase": "Detected", "autonomyLevel": level, **(status or {})},
    )
    return name


def gray_failure():
    return FakeMetrics(latencyP95=4.0, cpu=0.2)


def test_a_detected_incident_reaches_proposed_through_r1():
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    detected(rig)
    rig.tick()
    assert rig.status()["phase"] == "Diagnosing"
    rig.tick()
    s = rig.status()
    assert s["phase"] == "Proposed"
    assert s["proposal"]["actionId"] == "restart_workload"
    assert s["proposal"]["faultClass"] == "gray_failure"
    assert s["reasoner"] == {"mode": "rules", "fallbackUsed": False}
    assert s["evidence"]["baseline"]["latencyP95"] == {"mean": 0.05, "stddev": 0.01}
    # the proposal and the bundle are in the recorder, under the hashes the status carries
    doc = rig.rec.proposals[s["proposal"]["hash"]]
    assert proposal.validate(doc) == [] and doc["action"]["id"] == "restart_workload"
    assert s["evidence"]["bundleHash"] in rig.rec.bundles
    assert rig.rec.incidents["uid-inc"]["target"] == f"{NS}/{DEP}"


def test_the_whole_path_runs_detected_to_resolved_at_l3():
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    detected(rig)
    rig.run(ticks=60, seconds=10)
    assert ended(rig) == ("Resolved", "remediation_verified")
    got = [(t[1], t[2], t[3]) for t in rig.rec.transitions]
    assert got == [
        (0, None, "Detected"),
        (1, "Detected", "Diagnosing"),
        (2, "Diagnosing", "Proposed"),
        (4, "Proposed", "Executing"),
        (5, "Executing", "Verifying"),
        (7, "Verifying", "Resolved"),
    ]
    assert rig.rec.incidents["uid-inc"]["terminal"] == (
        "Resolved",
        "remediation_verified",
    )


def test_a_repair_that_does_not_hold_is_escalated_after_diagnosis():
    rig = Rig(collector=True, metrics=FakeMetrics(p95=0.9, latencyP95=4.0))
    detected(rig)
    rig.run(ticks=60, seconds=10)
    assert ended(rig) == ("Escalated", "verification_failed")


def test_l1_recommends_the_rules_engines_proposal():
    rig = Rig(collector=True, level="1", metrics=FakeMetrics(latencyP95=4.0))
    detected(rig, level=1)
    rig.run()
    assert ended(rig) == ("Recommended", "level_recommend")
    assert rig.api.mutations() == []


def test_a_rollout_escalates_through_the_rules_engine():
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    rig.api.rolling = True
    detected(rig)
    rig.run()
    assert ended(rig) == ("Escalated", "proposed_escalation")
    assert rig.status()["proposal"]["recommendation"]["kind"] == "git_revert"


@pytest.mark.parametrize("level", [0])
def test_l0_is_left_to_the_reconciler(level):
    rig = Rig(collector=True)
    detected(rig, level=level)
    rig.tick()
    assert rig.status()["phase"] == "Detected"


def test_without_a_collector_the_incident_stays_detected():
    rig = Rig()
    detected(rig)
    rig.tick()
    assert rig.status()["phase"] == "Detected"


def test_a_pending_intake_handler_is_waited_for():
    rig = Rig(collector=True)
    detected(rig, status={"kopf": {"progress": {"intake_level": {"started": iso(T0)}}}})
    rig.tick()
    assert rig.status()["phase"] == "Detected"


def test_an_unreadable_target_retries_then_escalates_evidence_error():
    rig = Rig(collector=True)
    detected(rig)
    rig.tick()  # Diagnosing
    rig.api.deployments_missing = True
    rig.tick(seconds=5)
    assert rig.status()["phase"] == "Diagnosing"
    rig.tick(seconds=61)
    assert ended(rig) == ("Escalated", "evidence_error")


def test_a_recorder_that_stays_down_ends_blocked_recorder_unavailable():
    rig = Rig(collector=True)
    detected(rig)
    rig.tick()
    rig.rec.fail_all = True
    rig.tick(seconds=5)
    assert rig.status()["phase"] == "Diagnosing"
    rig.tick(seconds=61)
    assert rig.status()["phase"] == "Blocked"
    assert rig.status()["reason"] == "recorder_unavailable"
    assert rig.api.requests == []


def test_the_bundle_carries_the_spec_fields_and_limits():
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    for i in range(60):
        rig.api.events_list.append(
            {
                "involvedObject": {"name": f"{DEP}-{i}"},
                "reason": "BackOff",
                "type": "Warning",
                "count": 2,
                "lastTimestamp": iso(rig.clock()),
            }
        )
    rig.api.events_list.append(
        {
            "involvedObject": {"name": DEP},
            "reason": "Old",
            "type": "Normal",
            "lastTimestamp": "2026-10-02T08:00:00Z",
        }
    )
    rig.api.events_list.append(
        {
            "involvedObject": {"name": "other"},
            "reason": "X",
            "lastTimestamp": iso(rig.clock()),
        }
    )
    rig.api.logs_current = {
        p["metadata"]["name"]: "\n".join(f"line {i}" for i in range(500))
        for p in rig.api.pods
    }
    collector = EvidenceCollector(rig.api, rig.metrics, rig.clock)
    detected(rig)
    raw, baseline = rig.call(collector.collect(rig.inc()))
    assert raw["schema"] == "evidence/v1"
    assert raw["workload"]["desired"] == 2 and raw["workload"]["ready"] == 2
    assert raw["workload"]["currentRevision"] == {
        "replicaSet": f"{DEP}-7d9",
        "imageDigest": "sha256:" + "a" * 64,
    }
    assert raw["workload"]["previousRevisions"] == [f"{DEP}-6c1"]
    assert len(raw["events"]) == 50 and {e["reason"] for e in raw["events"]} == {
        "BackOff"
    }
    assert len(raw["logs"]["current"]) <= 200 * 3
    assert raw["metrics"]["zscores"]["latencyP95"] == 4.0
    assert (
        raw["gitops"]["application"] == "sample-api-dev"
        and raw["gitops"]["sync"] == "Synced"
    )
    assert baseline["latencyP95"]["mean"] == 0.05


def test_no_metrics_source_gives_null_zscores_and_a_null_baseline():
    rig = Rig(collector=True)
    rig.act.collector.metrics = None
    detected(rig)
    raw, baseline = rig.call(rig.act.collector.collect(rig.inc()))
    assert set(raw["metrics"]["zscores"].values()) == {None}
    assert baseline["latencyP95"] == {"mean": None, "stddev": None}


# ---------------------------------------------------------------- Q1: nothing unredacted is stored
SECRETS = [
    "ghp_" + "Z9y8X7w6V5" * 4,
    "AKIAQWERTYUIOPASDFGH",
    "alice.admin@example.com",
    "10.42.7.19",
    "hunter2hunter2",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "s3cr3tPassw0rd",
]


def poison(rig):
    """Secrets in every free-text place the collector reads."""
    rig.api.logs_current = {
        p["metadata"]["name"]: (
            f"token {SECRETS[0]} used\nuser {SECRETS[2]} from {SECRETS[3]}\n"
            f"DB_PASSWORD={SECRETS[4]}\npostgres://a:{SECRETS[6]}@db:5432/x"
        )
        for p in rig.api.pods
    }
    rig.api.logs_previous = {
        p["metadata"]["name"]: f"aws {SECRETS[1]} jwt {SECRETS[5]}"
        for p in rig.api.pods
    }
    rig.api.events_list = [
        {
            "involvedObject": {"name": f"{DEP}-1"},
            "reason": f"Failed password={SECRETS[4]}",
            "type": "Warning",
            "lastTimestamp": iso(rig.clock()),
        }
    ]
    rig.api.application["status"]["sync"]["revision"] = f"rev {SECRETS[2]}"
    rig.api.pods[0]["status"]["containerStatuses"][0]["state"] = {
        "waiting": {"reason": f"CrashLoop {SECRETS[0]}"}
    }


def everything_stored(rig) -> str:
    """Every value that reached the recorder, and every status the operator wrote."""
    stored = [
        rig.rec.bundles,
        rig.rec.proposals,
        rig.rec.transitions,
        rig.rec.gates,
        rig.rec.incidents,
        rig.api.writes,
        rig.api.incidents,
    ]
    return json.dumps(stored, default=str)


def test_no_unredacted_field_reaches_storage():
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    poison(rig)
    detected(rig)
    rig.run()
    blob = everything_stored(rig)
    assert rig.rec.bundles, "the bundle was stored"
    for secret in SECRETS:
        assert secret not in blob, secret
    [(_, content, counts)] = rig.rec.bundles.values()
    assert counts["token"] >= 1 and counts["email"] >= 1 and counts["ip"] >= 1
    assert content["redaction"]["counts"] == counts
    # the stored hash is the hash of the stored content
    body = {k: v for k, v in content.items() if k != "hash"}
    assert content["hash"] == proposal.hash_of(body)
    assert rig.status()["evidence"]["redactions"] == sum(counts.values())


def test_the_rules_engine_receives_only_the_redacted_bundle(monkeypatch):
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    poison(rig)
    seen = []
    real = act_module.rules.propose
    monkeypatch.setattr(
        act_module.rules, "propose", lambda b: (seen.append(json.dumps(b)), real(b))[1]
    )
    detected(rig)
    rig.run(ticks=3)
    assert len(seen) == 1
    for secret in SECRETS:
        assert secret not in seen[0], secret


def test_the_leak_check_is_not_vacuous(monkeypatch):
    """With redaction switched off the same check finds the secrets: the test above can fail."""
    monkeypatch.setattr(act_module, "redact_bundle", lambda b: (b, {"token": 0}))
    rig = Rig(collector=True, metrics=FakeMetrics(latencyP95=4.0))
    poison(rig)
    detected(rig)
    rig.run(ticks=3)
    blob = everything_stored(rig)
    assert any(secret in blob for secret in SECRETS)
