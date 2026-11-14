"""KubeIncidentApi maps API failures to client errors and surfaces the Audit-Id header."""

import json
from typing import Any

import pytest
from kubernetes.client import ApiException
from urllib3.exceptions import MaxRetryError

from nexus_client.errors import Conflict, Denied, NexusClientError, NotFound, Unreachable
from nexus_client.incidents import KubeIncidentApi


def api() -> KubeIncidentApi:
    return KubeIncidentApi.__new__(KubeIncidentApi)  # no kubeconfig needed to test _call


def raiser(status: int, message: str = "m", audit: str | None = "a1") -> Any:
    def fn(*_a: Any, **_k: Any) -> Any:
        e = ApiException(status=status, reason="r")
        e.body = json.dumps({"message": message})
        e.headers = {"Audit-Id": audit} if audit else {}
        raise e
    return fn


@pytest.mark.parametrize("status,exc", [(404, NotFound), (409, Conflict), (403, Denied), (422, Denied),
                                        (401, Denied), (500, NexusClientError)])
def test_status_mapping(status: int, exc: type[Exception]) -> None:
    with pytest.raises(exc):
        api()._call(raiser(status))


def test_denied_carries_the_policy_message_and_audit_id() -> None:
    with pytest.raises(Denied) as e:
        api()._call(raiser(403, "K5 nexus-incident-approval: only group nexus-approvers", "77aa"))
    assert "K5 nexus-incident-approval" in str(e.value) and e.value.audit_id == "77aa"


def test_success_returns_the_audit_id_header() -> None:
    data, audit = api()._call(lambda *_a, **_k: ({"ok": 1}, 200, {"Audit-Id": "8c2f1e"}))
    assert data == {"ok": 1} and audit == "8c2f1e"


def test_network_failure_is_unreachable() -> None:
    def fn(*_a: Any, **_k: Any) -> Any:
        raise MaxRetryError(None, "https://x", None)  # type: ignore[arg-type]
    with pytest.raises(Unreachable):
        api()._call(fn)


def test_a_kyverno_webhook_denial_is_denied_even_as_400() -> None:
    msg = ('admission webhook "validate.kyverno.svc-fail" denied the request: nexus-incident-approval: '
           "approvers-only: 'K5 nexus-incident-approval: only group nexus-approvers may set spec.approval'")
    with pytest.raises(Denied) as e:
        api()._call(raiser(400, msg, "38544450"))
    assert "approvers-only" in str(e.value) and e.value.audit_id == "38544450"


def test_another_400_stays_an_error_and_keeps_its_message() -> None:
    with pytest.raises(NexusClientError) as e:
        api()._call(raiser(400, "the body of the request was in an unknown format"))
    assert not isinstance(e.value, Denied) and "unknown format" in str(e.value)
