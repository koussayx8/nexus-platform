"""Regression test for the #100 live-gate crash (M1b-8): the image runs as UID 10001, which has no
/etc/passwd entry, and Kopf names its peering identity with getpass.getuser() before any startup
handler runs. getpass reads LOGNAME, USER, LNAME and USERNAME first and only then falls back to
pwd.getpwuid(), so operator/k8s/deployment.yaml sets USER. Uses Kopf's private detect_own_id:
re-check this test after any Kopf upgrade.
"""

import pathlib
import pwd

import pytest
import yaml
from kopf._core.engines import peering

USER_VARS = ("LOGNAME", "USER", "LNAME", "USERNAME", "POD_ID")
DEPLOYMENT = pathlib.Path(__file__).resolve().parents[2] / "k8s" / "deployment.yaml"


@pytest.fixture
def no_passwd_entry(monkeypatch):
    """The container's situation: no user variables and no passwd entry for the UID."""
    for var in USER_VARS:
        monkeypatch.delenv(var, raising=False)

    def missing(uid):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    monkeypatch.setattr(pwd, "getpwuid", missing)


def test_startup_identity_fails_without_user(no_passwd_entry):
    with pytest.raises(KeyError, match="uid not found"):
        peering.detect_own_id(manual=False)


def test_startup_identity_succeeds_with_user(no_passwd_entry, monkeypatch):
    monkeypatch.setenv("USER", "nexus-operator")
    assert str(peering.detect_own_id(manual=False)).startswith("nexus-operator@")


def test_deployment_sets_user():
    """The manifest carries the workaround for the operator container."""
    deployment = yaml.safe_load(DEPLOYMENT.read_text(encoding="utf-8"))
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    assert env.get("USER") == "nexus-operator"
