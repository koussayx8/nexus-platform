"""End-to-end through the Typer app: arguments, stdin confirmation and process exit codes."""

import pytest
from fakes import FakeApi, incident_dict
from typer.testing import CliRunner

from nexusctl import app as app_module
from nexusctl import exitcodes

runner = CliRunner()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi(incident_dict())
    monkeypatch.setattr(app_module, "_api_factory", lambda _c: fake)
    monkeypatch.setenv("NEXUSCTL_CONFIG", "/nonexistent/config.yaml")
    return fake


def test_list(api: FakeApi) -> None:
    r = runner.invoke(app_module.app, ["list"])
    assert r.exit_code == exitcodes.OK and "inc-s2-run0042" in r.output


def test_explain(api: FakeApi) -> None:
    r = runner.invoke(app_module.app, ["--no-color", "explain", "inc-s2-run0042"])
    assert r.exit_code == exitcodes.OK and "INCIDENT" in r.output and "UNTRUSTED" in r.output


def test_explain_not_found(api: FakeApi) -> None:
    assert runner.invoke(app_module.app, ["explain", "nope"]).exit_code == exitcodes.NOT_FOUND


def test_approve_with_the_typed_name(api: FakeApi) -> None:
    r = runner.invoke(app_module.app, ["approve", "inc-s2-run0042"], input="sample-api\n")
    assert r.exit_code == exitcodes.OK, r.output
    assert len(api.patches) == 1 and "Audit-Id" in r.output


def test_approve_has_no_non_interactive_bypass(api: FakeApi) -> None:
    r = runner.invoke(app_module.app, ["approve", "inc-s2-run0042"], input="")
    assert r.exit_code == exitcodes.ERROR and api.patches == []
    assert runner.invoke(app_module.app, ["approve", "inc-s2-run0042", "--yes"]).exit_code != 0
    assert runner.invoke(app_module.app, ["approve", "inc-s2-run0042", "--force"]).exit_code != 0


def test_approve_ttl_over_15m_is_refused_before_any_read(api: FakeApi) -> None:
    r = runner.invoke(app_module.app, ["approve", "inc-s2-run0042", "--ttl", "20m"], input="sample-api\n")
    assert r.exit_code != 0 and api.patches == [] and api.gets == 0


def test_reject_needs_a_reason(api: FakeApi) -> None:
    assert runner.invoke(app_module.app, ["reject", "inc-s2-run0042"]).exit_code != 0
    r = runner.invoke(app_module.app, ["reject", "inc-s2-run0042", "--reason", "wrong target"],
                      input="sample-api\n")
    assert r.exit_code == exitcodes.OK and api.patches[0][1]["decision"] == "rejected"


def test_bad_output_format(api: FakeApi) -> None:
    assert runner.invoke(app_module.app, ["-o", "xml", "list"]).exit_code != 0
