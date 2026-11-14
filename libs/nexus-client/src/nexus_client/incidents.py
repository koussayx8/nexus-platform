"""Access to Incident custom resources: a small protocol, and the Kubernetes implementation.

`IncidentApi` is what nexusctl's commands depend on, so tests use a fake. `KubeIncidentApi` uses
the official Kubernetes client with the human's kubeconfig; it has no ServiceAccount and never
talks to the operator or the Reasoner. Every response carries the API server's `Audit-Id`.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from .errors import Conflict, Denied, NexusClientError, NotFound, Unreachable
from .models import Incident

GROUP, VERSION, PLURAL = "nexus.io", "v1alpha1", "incidents"
NAMESPACE = "nexus-system"


@dataclass(frozen=True)
class Reply:
    incident: Incident
    audit_id: str | None


class IncidentApi(Protocol):
    def get(self, name: str) -> Reply: ...

    def list(self) -> list[Incident]: ...

    def patch_approval(self, name: str, approval: dict[str, Any], resource_version: str) -> Reply:
        """Merge-patch spec.approval, failing with Conflict if resourceVersion moved."""
        ...


def _message(body: str | None, fallback: str) -> str:
    """The policy or CEL message from a Status body, if there is one."""
    import json

    try:
        return str(json.loads(body or "{}").get("message") or fallback)
    except ValueError:
        return fallback


def impersonation_headers(as_user: str | None, as_group: str | None) -> dict[str, str]:
    """The Impersonate-* headers for `--as` / `--as-group` (what kubectl sends for the same flags).

    The official client ignores a kubeconfig user's `as:` / `as-groups:` fields, so nexusctl takes
    the identity as flags. One group only: the client merges headers into a dict, so a repeated
    Impersonate-Group cannot be sent. The API server checks that the caller may impersonate.
    """
    if as_group and not as_user:
        raise ValueError("--as-group needs --as (the API server requires a user to impersonate)")
    headers: dict[str, str] = {}
    if as_user:
        headers["Impersonate-User"] = as_user
    if as_group:
        headers["Impersonate-Group"] = as_group
    return headers


class KubeIncidentApi:
    def __init__(self, namespace: str = NAMESPACE, context: str | None = None,
                 kubeconfig: str | None = None, as_user: str | None = None,
                 as_group: str | None = None) -> None:
        from kubernetes import client, config

        headers = impersonation_headers(as_user, as_group)
        config.load_kube_config(config_file=kubeconfig, context=context)  # type: ignore[no-untyped-call]
        api_client = client.ApiClient()
        for name, value in headers.items():
            api_client.set_default_header(name, value)  # type: ignore[no-untyped-call]
        self._api = client.CustomObjectsApi(api_client)
        self._ns = namespace

    def _call(self, fn: Any, *args: Any, **kw: Any) -> tuple[dict[str, Any], str | None]:
        from kubernetes.client import ApiException
        from urllib3.exceptions import HTTPError

        try:
            data, _status, headers = fn(*args, _return_http_data_only=False, **kw)
        except ApiException as e:
            audit = (e.headers or {}).get("Audit-Id") if e.headers else None
            if e.status == 404:
                raise NotFound(_message(e.body, "not found")) from e
            if e.status == 409:
                raise Conflict(_message(e.body, "conflict")) from e
            if e.status in (401, 403, 422):
                raise Denied(_message(e.body, e.reason or "denied"), audit) from e
            raise NexusClientError(f"{e.status} {e.reason}") from e
        except (HTTPError, OSError) as e:
            raise Unreachable(str(e)) from e
        return data, headers.get("Audit-Id")

    def get(self, name: str) -> Reply:
        data, audit = self._call(self._api.get_namespaced_custom_object,
                                 GROUP, VERSION, self._ns, PLURAL, name)
        return Reply(Incident.model_validate(data), audit)

    def list(self) -> list[Incident]:
        data, _ = self._call(self._api.list_namespaced_custom_object,
                             GROUP, VERSION, self._ns, PLURAL)
        return [Incident.model_validate(i) for i in data.get("items", [])]

    def patch_approval(self, name: str, approval: dict[str, Any], resource_version: str) -> Reply:
        body = {"metadata": {"resourceVersion": resource_version}, "spec": {"approval": approval}}
        data, audit = self._call(self._api.patch_namespaced_custom_object,
                                 GROUP, VERSION, self._ns, PLURAL, name, body)
        return Reply(Incident.model_validate(data), audit)
