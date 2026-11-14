"""KubeIncidentApi against the real kubernetes client, with only the HTTP layer faked.

Regression for kubernetes 37: the plain CustomObjectsApi methods ignore
_return_http_data_only and return the body alone, so get, list and patch failed live with
"too many values to unpack". These tests run the client library's own (de)serialisation.
"""

import json

import pytest
from fakes_http import FakeHttp

from nexus_client.incidents import KubeIncidentApi

INCIDENT = {
    "apiVersion": "nexus.io/v1alpha1", "kind": "Incident",
    "metadata": {"name": "inc-x", "namespace": "nexus-system", "resourceVersion": "7"},
    "spec": {"source": {"type": "detector", "alertname": "NexusCrashLooping"},
             "target": {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"},
             "detectedAt": "2026-11-14T07:00:00Z"},
}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> tuple[KubeIncidentApi, FakeHttp]:
    from kubernetes import config

    monkeypatch.setattr(config, "load_kube_config", lambda **_k: None)  # no kubeconfig is read
    api = KubeIncidentApi()
    http = FakeHttp()
    monkeypatch.setattr(api._api.api_client.rest_client, "request", http.request)
    return api, http


def test_get_returns_the_incident_and_audit_id(api: tuple[KubeIncidentApi, FakeHttp]) -> None:
    kube, http = api
    http.reply(200, INCIDENT, audit="a-get")
    r = kube.get("inc-x")
    assert r.incident.metadata.name == "inc-x" and r.audit_id == "a-get"


def test_list(api: tuple[KubeIncidentApi, FakeHttp]) -> None:
    kube, http = api
    http.reply(200, {"apiVersion": "v1", "kind": "List", "items": [INCIDENT]})
    assert [i.metadata.name for i in kube.list()] == ["inc-x"]


def test_patch_is_a_merge_patch_pinned_to_the_resource_version(
        api: tuple[KubeIncidentApi, FakeHttp]) -> None:
    kube, http = api
    http.reply(200, INCIDENT, audit="a-patch")
    r = kube.patch_approval("inc-x", {"decision": "approved"}, "7")
    method, headers, body = http.sent[-1]
    assert method == "PATCH" and headers["Content-Type"] == "application/merge-patch+json"
    sent = json.loads(body) if isinstance(body, (str, bytes)) else body
    assert sent == {"metadata": {"resourceVersion": "7"}, "spec": {"approval": {"decision": "approved"}}}
    assert r.audit_id == "a-patch"


def test_a_policy_denial_keeps_its_message(api: tuple[KubeIncidentApi, FakeHttp]) -> None:
    from nexus_client.errors import Denied

    kube, http = api
    http.reply(403, {"kind": "Status", "message": "K5 nexus-incident-approval: no"}, audit="a-deny")
    with pytest.raises(Denied) as e:
        kube.patch_approval("inc-x", {"decision": "approved"}, "7")
    assert "K5" in str(e.value) and e.value.audit_id == "a-deny"
