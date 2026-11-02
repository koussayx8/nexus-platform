"""approve / reject: pinning, confirmation, staleness and every exit code they own (spec §16A)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import FakeApi, incident_dict
from nexus_client.errors import Conflict, Denied, Unreachable

from nexusctl import exitcodes
from nexusctl.commands.approvals import run_decide

NOW = datetime(2026, 10, 14, 9, 13, 0, tzinfo=UTC)
TTL = timedelta(minutes=15)


def decide(api: FakeApi, name: str = "inc-s2-run0042", decision: str = "approved",
           typed: str = "sample-api", reason: str | None = None) -> tuple[int, list[str], list[str]]:
    out: list[str] = []
    asked: list[str] = []

    def prompt(text: str) -> str:
        asked.append(text)
        return typed

    rc = run_decide(api, name, decision=decision, ttl=TTL, reason=reason, prompt=prompt,
                    now=lambda: NOW, out=out.append)
    return rc, out, asked


def test_approve_writes_the_pinned_values_with_the_resource_version() -> None:
    api = FakeApi(incident_dict())
    rc, out, asked = decide(api)
    assert rc == exitcodes.OK
    ((name, body, rv),) = api.patches
    assert name == "inc-s2-run0042" and rv == "100"
    assert body["decision"] == "approved"
    assert body["proposalHash"] == "sha256:ab12"
    assert body["actionId"] == "scale_deployment"
    assert body["parameters"] == {"replicas": 4}
    assert "approvedBy" not in body and "approvedAt" not in body
    assert "sample-api" in asked[0]
    final = out[-1]
    assert "stamped by approver-1" in final and "Audit-Id 8c2f1e" in final


def test_the_screen_shows_what_is_pinned() -> None:
    api = FakeApi(incident_dict())
    _, out, _ = decide(api)
    screen = out[0]
    assert "scale_deployment" in screen and "'replicas': 4" in screen
    assert "sha256:ab12" in screen and "nexus-dev/sample-api" in screen and "EXPIRES" in screen
    # trusted fields come before the untrusted box
    assert screen.index("EXPIRES") < screen.index("UNTRUSTED")


def test_a_wrong_confirmation_writes_nothing() -> None:
    api = FakeApi(incident_dict())
    rc, out, _ = decide(api, typed="sample")
    assert rc == exitcodes.ERROR and api.patches == []
    assert "nothing was written" in out[-1]


def test_an_empty_confirmation_writes_nothing() -> None:
    api = FakeApi(incident_dict())
    rc, _, _ = decide(api, typed="")
    assert rc == exitcodes.ERROR and api.patches == []


def test_a_changed_hash_aborts_with_exit_4() -> None:
    api = FakeApi(incident_dict())

    def change(item: dict[str, Any]) -> None:
        item["status"]["proposal"]["hash"] = "sha256:ffff"
        item["metadata"]["resourceVersion"] = "101"

    api.after_first_get = change
    rc, out, _ = decide(api)
    assert rc == exitcodes.STALE and api.patches == []
    assert "sha256:ab12 -> sha256:ffff" in out[-1]


def test_a_409_between_reread_and_write_is_also_exit_4() -> None:
    api = FakeApi(incident_dict())
    api.patch_error = Conflict("the object has been modified")
    rc, out, _ = decide(api)
    assert rc == exitcodes.STALE
    assert "nothing was written" in out[-1]


@pytest.mark.parametrize("phase", ["Resolved", "Proposed", "Escalated", "Executing"])
def test_wrong_phase_is_exit_5(phase: str) -> None:
    api = FakeApi(incident_dict(phase=phase))
    rc, _, asked = decide(api)
    assert rc == exitcodes.WRONG_PHASE and api.patches == [] and asked == []


def test_already_decided_is_exit_5() -> None:
    api = FakeApi(incident_dict(approval={"decision": "approved", "proposalHash": "sha256:ab12"}))
    rc, out, _ = decide(api)
    assert rc == exitcodes.WRONG_PHASE and "already decided" in out[-1]


def test_not_found_is_exit_3() -> None:
    rc, _, _ = decide(FakeApi(), name="inc-missing")
    assert rc == exitcodes.NOT_FOUND


def test_denied_by_admission_is_exit_2_and_prints_the_policy_message() -> None:
    api = FakeApi(incident_dict())
    api.patch_error = Denied("K5 nexus-incident-approval: only group nexus-approvers may set spec.approval", "77aa")
    rc, out, _ = decide(api)
    assert rc == exitcodes.DENIED
    assert "K5 nexus-incident-approval" in out[-1] and "77aa" in out[-1]


def test_a_denial_message_cannot_carry_escape_sequences() -> None:
    api = FakeApi(incident_dict())
    api.patch_error = Denied("\x1b[2Jfake screen\nAPPROVED")
    _, out, _ = decide(api)
    assert "\x1b" not in out[-1] and "\n" not in out[-1]


def test_unreachable_is_exit_9() -> None:
    api = FakeApi(incident_dict())
    api.patch_error = Unreachable("connection refused")
    rc, _, _ = decide(api)
    assert rc == exitcodes.UNREACHABLE


def test_reject_writes_rejected_with_hash_and_reason_only() -> None:
    api = FakeApi(incident_dict())
    rc, _, _ = decide(api, decision="rejected", reason="not now")
    assert rc == exitcodes.OK
    ((_, body, _),) = api.patches
    assert body == {"decision": "rejected", "proposalHash": "sha256:ab12", "reason": "not now"}


def test_a_hypothesis_cannot_forge_the_trusted_fields_of_the_screen() -> None:
    evil = "ok\x1b[2J\x1b[H\nDECISION    APPROVED by cluster-admin\nEXPIRES     never"
    api = FakeApi(incident_dict(hypothesis=evil))
    _, out, _ = decide(api)
    trusted, _, untrusted = out[0].partition("UNTRUSTED")
    assert "\x1b" not in out[0]
    assert trusted.count("DECISION") == 1 and trusted.count("EXPIRES") == 1
    assert "\nDECISION" not in untrusted and "\nEXPIRES" not in untrusted
