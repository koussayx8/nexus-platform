"""/status, /pods <ns>, /incident <name> (D21): fixed grammar, fixed namespaces, read-only."""

import pytest

from nexus_bot import commands
from tests.fakes import NOW, FakeCluster, incident_dict

NS = ("nexus-dev", "nexus-prod")


def run(c: FakeCluster, text: str) -> str:
    return commands.run(text, c, NS, lambda: NOW).message.build().text


def reply(c: FakeCluster, text: str) -> commands.Reply:
    return commands.run(text, c, NS, lambda: NOW)


def test_status_shows_open_incidents_levels_and_killswitch() -> None:
    c = FakeCluster(incident_dict(), incident_dict(name="inc-done-1", uid="1" * 32, phase="Resolved"))
    out = run(c, "/status")
    assert "Open Incidents: 1" in out and "🟠 inc-0123…4000 · AwaitingApproval" in out and "inc-done" not in out
    assert "Level nexus-dev: L2" in out and "Level nexus-prod: L1" in out and "Kill Switch: active" in out
    assert "As of 10:00 UTC" in out
    assert c.patches == []


def test_status_missing_killswitch_is_named() -> None:
    c = FakeCluster()
    c.state = None
    assert "missing (K4 denies)" in run(c, "/status@nexus_bot")


def test_pods_reads_an_allowed_namespace() -> None:
    out = run(FakeCluster(), "/pods nexus-dev")
    assert "sample-api: 2/3 ready" in out and "sample-api-7d9-abc: Running, ready, 4 restarts" in out
    assert "sample-api-7d9-def: Pending, not ready, 0 restarts" in out


def test_pods_with_no_argument_offers_the_two_namespaces() -> None:
    r = reply(FakeCluster(), "/pods")
    assert r.rows == [[("nexus-dev", "p:nexus-dev"), ("nexus-prod", "p:nexus-prod")]]


@pytest.mark.parametrize("text", ["/pods kube-system", "/pods nexus-system", "/pods nexus-dev extra",
                                  "/pods nexus-data"])
def test_pods_refuses_other_namespaces(text: str) -> None:
    assert run(FakeCluster(), text).startswith("usage: /pods")


def test_incident_explain() -> None:
    out = run(FakeCluster(incident_dict()), "/incident inc-0123abcd-1764324000")
    assert "Incident: inc-0123abcd-1764324000" in out and "Model text: untrusted, never a decision" in out


def test_incident_with_no_argument_lists_the_five_most_recent() -> None:
    items = [incident_dict(name=f"inc-{i:04d}aaaa-176432400{i}", uid=f"{i:x}" * 32) for i in range(1, 8)]
    for i, d in enumerate(items, start=1):
        d["metadata"]["creationTimestamp"] = f"2026-11-28T09:0{i}:00Z"
    r = reply(FakeCluster(*items), "/incident")
    text = r.message.build().text
    assert [row[0][1] for row in r.rows] == [f"i:{f'{i:x}' * 16}" for i in (7, 6, 5, 4, 3)]
    assert all(len(row) == 1 for row in r.rows)
    assert r.rows[0][0][0] == "🟠 inc-0007…4007"
    assert text.startswith("🗂 Recent Incidents") and "09:07 UTC" in text and "inc-0002" not in text


@pytest.mark.parametrize("name", ["foo", "inc-", "inc-UPPER", "inc-../x", "inc-a b", "inc-" + "a" * 60])
def test_incident_name_regex(name: str) -> None:
    assert run(FakeCluster(), f"/incident {name}").startswith("usage: /incident")


def test_incident_not_found() -> None:
    assert run(FakeCluster(), "/incident inc-nope") == "Incident not found"


@pytest.mark.parametrize("text", ["hello", "/approve inc-x", "/delete", "", None, "/start", "/"])
def test_anything_else_is_the_usage_line(text: object) -> None:
    r = commands.run(text, FakeCluster(), NS, lambda: NOW)
    assert r.message.build() == commands.usage().build() and r.rows == []


def test_bot_suffix_and_case_are_ignored() -> None:
    assert "Open Incidents" in run(FakeCluster(), "/STATUS@nexus_bot")


def test_commands_have_no_write_path() -> None:
    # commands receive a Reader; the approver factory is never in reach
    import inspect

    assert "approver" not in inspect.getsource(commands)
