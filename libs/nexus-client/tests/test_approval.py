"""Approval pinning: what is written equals what was shown, inside K5's bounds (spec §11, §16A)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus_client import approval as ap
from nexus_client.errors import StaleProposal, WrongPhase
from nexus_client.models import Incident

NOW = datetime(2026, 10, 14, 9, 13, 0, tzinfo=UTC)


def inc(**over: Any) -> Incident:
    d: dict[str, Any] = {
        "metadata": {"name": "inc-1", "resourceVersion": "7"},
        "spec": {"source": {"type": "detector"},
                 "target": {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"},
                 "detectedAt": "2026-10-14T09:12:03Z"},
        "status": {"phase": "AwaitingApproval",
                   "proposal": {"hash": "sha256:ab12", "actionId": "scale_deployment",
                                "parameters": {"replicas": 4}}},
    }
    for k, v in over.items():
        section, key = k.split("__")
        d[section][key] = v
    return Incident.model_validate(d)


@pytest.mark.parametrize("text,seconds", [("15m", 900), ("10m", 600), ("90s", 90), ("1m", 60)])
def test_parse_ttl(text: str, seconds: int) -> None:
    assert ap.parse_ttl(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["16m", "901s", "1h", "0m", "-5m", "abc", "", "15"])
def test_parse_ttl_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        ap.parse_ttl(text)


def test_approved_pins_exactly_the_proposal() -> None:
    i = inc()
    a = ap.build_approval(i, ttl=timedelta(minutes=15), now=NOW, decision="approved")
    p = i.status.proposal
    assert p is not None
    assert a["decision"] == "approved"
    assert a["proposalHash"] == p.hash
    assert a["actionId"] == p.actionId
    assert a["parameters"] == p.parameters == {"replicas": 4}


def test_approval_never_carries_the_stamped_fields() -> None:
    a = ap.build_approval(inc(), ttl=timedelta(minutes=15), now=NOW, decision="approved")
    assert "approvedBy" not in a and "approvedAt" not in a


@pytest.mark.parametrize("ttl", [timedelta(minutes=15), timedelta(minutes=10), timedelta(seconds=30)])
def test_expiry_is_inside_k5s_15_minute_bound(ttl: timedelta) -> None:
    a = ap.build_approval(inc(), ttl=ttl, now=NOW, decision="approved")
    expires = datetime.strptime(a["expiresAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert expires <= NOW + timedelta(minutes=15)
    assert expires <= NOW + ttl


def test_a_ttl_beyond_the_maximum_is_clamped_not_trusted() -> None:
    a = ap.build_approval(inc(), ttl=timedelta(hours=2), now=NOW, decision="approved")
    expires = datetime.strptime(a["expiresAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert expires <= NOW + timedelta(minutes=15)


def test_rejected_writes_hash_and_reason_only() -> None:
    a = ap.build_approval(inc(), ttl=timedelta(minutes=15), now=NOW, decision="rejected", reason="no")
    assert a == {"decision": "rejected", "proposalHash": "sha256:ab12", "reason": "no"}


def test_unknown_decision() -> None:
    with pytest.raises(ValueError):
        ap.build_approval(inc(), ttl=timedelta(minutes=1), now=NOW, decision="maybe")


def test_check_decidable_ok() -> None:
    ap.check_decidable(inc())


def test_check_decidable_wrong_phase() -> None:
    with pytest.raises(WrongPhase, match="Resolved"):
        ap.check_decidable(inc(status__phase="Resolved"))


def test_check_decidable_no_proposal() -> None:
    with pytest.raises(WrongPhase, match="no proposal"):
        ap.check_decidable(inc(status__proposal=None))


def test_check_decidable_already_decided() -> None:
    with pytest.raises(WrongPhase, match="already decided"):
        ap.check_decidable(inc(spec__approval={"decision": "rejected"}))


def test_same_proposal_detects_a_changed_hash() -> None:
    other = inc(status__proposal={"hash": "sha256:ffff", "actionId": "scale_deployment"})
    ap.check_same_proposal(inc(), inc())
    with pytest.raises(StaleProposal, match="sha256:ab12 -> sha256:ffff"):
        ap.check_same_proposal(inc(), other)


def test_same_proposal_detects_a_vanished_proposal() -> None:
    with pytest.raises(StaleProposal):
        ap.check_same_proposal(inc(), inc(status__proposal=None))
