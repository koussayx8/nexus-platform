"""--as / --as-group become Impersonate-* headers on every request (kubectl parity)."""

from typing import Any

import pytest

from nexus_client import incidents
from nexus_client.incidents import KubeIncidentApi, impersonation_headers


def test_no_flags_no_headers() -> None:
    assert impersonation_headers(None, None) == {}


def test_user_and_group() -> None:
    assert impersonation_headers("koussay-approver", "nexus-approvers") == {
        "Impersonate-User": "koussay-approver", "Impersonate-Group": "nexus-approvers"}


def test_group_without_user_is_refused() -> None:
    with pytest.raises(ValueError, match="needs --as"):
        impersonation_headers(None, "nexus-approvers")


def test_headers_reach_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from kubernetes import config

    monkeypatch.setattr(config, "load_kube_config", lambda **_k: None)  # no kubeconfig is read
    api = KubeIncidentApi(as_user="koussay-approver", as_group="nexus-approvers")
    sent: dict[str, Any] = {}

    def request(method: str, url: str, **kw: Any) -> Any:
        sent.update(kw.get("headers") or {})
        raise RuntimeError("stop before the network")

    monkeypatch.setattr(api._api.api_client.rest_client, "request", request)
    with pytest.raises(RuntimeError):
        api.get("inc-x")
    assert sent["Impersonate-User"] == "koussay-approver"
    assert sent["Impersonate-Group"] == "nexus-approvers"


def test_without_flags_nothing_is_impersonated(monkeypatch: pytest.MonkeyPatch) -> None:
    from kubernetes import config

    monkeypatch.setattr(config, "load_kube_config", lambda **_k: None)
    api = KubeIncidentApi()
    assert not any(h.startswith("Impersonate-") for h in api._api.api_client.default_headers)
    assert incidents.NAMESPACE == "nexus-system"


def test_in_cluster_reads_no_kubeconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    from kubernetes import config

    called: list[str] = []
    monkeypatch.setattr(config, "load_incluster_config", lambda: called.append("in-cluster"))

    def no_kubeconfig(**_k: Any) -> None:
        raise AssertionError("a kubeconfig was read")

    monkeypatch.setattr(config, "load_kube_config", no_kubeconfig)
    api = KubeIncidentApi(in_cluster=True, as_user="koussay-approver", as_group="nexus-approvers")
    assert called == ["in-cluster"]
    assert api._api.api_client.default_headers["Impersonate-User"] == "koussay-approver"
