"""Reads go out as the bot; only the approver client impersonates (ADR-040 decisions 4 and 5).
No cluster: the in-cluster loader is replaced, and requests stop before the network."""

from typing import Any

import pytest

from nexus_bot.kube import KubeReader, kube_approver


@pytest.fixture(autouse=True)
def no_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    from kubernetes import client, config

    monkeypatch.setattr(config, "load_incluster_config", lambda: client.Configuration.set_default(
        client.Configuration(host="https://127.0.0.1:1")))

    def no_kubeconfig(**_k: Any) -> None:
        raise AssertionError("a kubeconfig was read")

    monkeypatch.setattr(config, "load_kube_config", no_kubeconfig)


def _headers_sent(monkeypatch: pytest.MonkeyPatch, api_client: Any, call: Any) -> dict[str, str]:
    sent: dict[str, str] = {}

    def request(method: str, url: str, **kw: Any) -> Any:
        sent.update(kw.get("headers") or {})
        raise RuntimeError("stop before the network")

    monkeypatch.setattr(api_client.rest_client, "request", request)
    with pytest.raises(RuntimeError):
        call()
    return sent


def test_reader_never_impersonates(monkeypatch: pytest.MonkeyPatch) -> None:
    r = KubeReader()
    for api_client, call in [
        (r._incidents._api.api_client, r.list_incidents),
        (r._core.api_client, lambda: r.pods("nexus-dev")),
        (r._apps.api_client, lambda: r.deployments("nexus-dev")),
    ]:
        sent = _headers_sent(monkeypatch, api_client, call)
        assert not any(k.startswith("Impersonate-") for k in sent)


def test_approver_impersonates_user_and_group(monkeypatch: pytest.MonkeyPatch) -> None:
    a: Any = kube_approver("koussay-approver", "nexus-approvers")
    sent = _headers_sent(monkeypatch, a._api.api_client,
                         lambda: a.patch_approval("inc-x", {"decision": "rejected"}, "1"))
    assert sent["Impersonate-User"] == "koussay-approver"
    assert sent["Impersonate-Group"] == "nexus-approvers"
