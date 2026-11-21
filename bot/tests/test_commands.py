"""/status, /pods <ns>, /incident <name> (D21): fixed grammar, fixed namespaces, read-only."""

import pytest

from nexus_bot import commands
from tests.fakes import NOW, FakeCluster, incident_dict

NS = ("nexus-dev", "nexus-prod")


def run(c: FakeCluster, text: str) -> str:
    return commands.run(text, c, NS, lambda: NOW)


def test_status_shows_open_incidents_levels_and_killswitch() -> None:
    c = FakeCluster(incident_dict(), incident_dict(name="inc-done-1", uid="1" * 32, phase="Resolved"))
    out = run(c, "/status")
    assert "Open Incidents: 1" in out and "inc-0123abcd-1764324000" in out and "inc-done-1" not in out
    assert "Level nexus-dev: 2" in out and "Level nexus-prod: 1" in out and "Kill Switch: active" in out
    assert c.patches == []


def test_status_missing_killswitch_is_named() -> None:
    c = FakeCluster()
    c.state = None
    assert "missing (K4 denies)" in run(c, "/status@nexus_bot")


def test_pods_reads_an_allowed_namespace() -> None:
    out = run(FakeCluster(), "/pods nexus-dev")
    assert "deploy sample-api  2/3 ready" in out and "restarts 4" in out and "not ready" in out


@pytest.mark.parametrize("text", ["/pods", "/pods kube-system", "/pods nexus-system", "/pods nexus-dev extra",
                                  "/pods nexus-data"])
def test_pods_refuses_other_namespaces(text: str) -> None:
    assert run(FakeCluster(), text).startswith("usage: /pods")


def test_incident_explain() -> None:
    out = run(FakeCluster(incident_dict()), "/incident inc-0123abcd-1764324000")
    assert "UNTRUSTED: PROPOSAL" in out


@pytest.mark.parametrize("name", ["foo", "inc-", "inc-UPPER", "inc-../x", "inc-a b", "inc-" + "a" * 60])
def test_incident_name_regex(name: str) -> None:
    assert run(FakeCluster(), f"/incident {name}").startswith("usage: /incident")


def test_incident_not_found() -> None:
    assert run(FakeCluster(), "/incident inc-nope") == "Incident not found"


@pytest.mark.parametrize("text", ["hello", "/approve inc-x", "/delete", "", None, "/start", "/"])
def test_anything_else_is_the_usage_line(text: object) -> None:
    assert commands.run(text, FakeCluster(), NS, lambda: NOW) == commands.USAGE


def test_bot_suffix_and_case_are_ignored() -> None:
    assert "Open Incidents" in run(FakeCluster(), "/STATUS@nexus_bot")


def test_commands_have_no_write_path() -> None:
    # commands receive a Reader; the approver factory is never in reach
    import inspect

    assert "approver" not in inspect.getsource(commands)
