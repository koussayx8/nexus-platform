"""The operator's own API client, for the poller, the loop and the intake read (ADR-023).

It uses the operator identity only: the in-cluster ServiceAccount token, read again on every
request because the kubelet rotates it. Every call it makes is in the staged §11 RBAC:
incidents get/list/create, incidents/status patch, namespaces get, and get on the two ConfigMaps;
for the act path (M2), reads of pods and deployments and the two mutating subresources,
deployments/scale and pods/eviction, in nexus-dev only (platform/rbac/nexus-operator-dev.yaml).
"""

import dataclasses
import json
import os
import ssl
from collections.abc import Callable

import aiohttp

from .model import GROUP, NAMESPACE, PLURAL, VERSION

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
USER_AGENT = "nexus-operator"


@dataclasses.dataclass(frozen=True)
class Credentials:
    server: str
    ca_path: str
    token: str = dataclasses.field(repr=False)


def service_account_credentials() -> Credentials:
    """In-cluster credentials; the only login the operator image uses."""
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if ":" in host:
        host = f"[{host}]"
    with open(os.path.join(SA_DIR, "token"), encoding="utf-8") as f:
        token = f.read().strip()
    return Credentials(f"https://{host}:{port}", os.path.join(SA_DIR, "ca.crt"), token)


class ApiError(Exception):
    def __init__(self, status: int, method: str, url: str, message: str = "") -> None:
        super().__init__(f"{method} {url}: HTTP {status} {message}".rstrip())
        self.status = status


# What a call to the API server (or Prometheus) can raise: HTTP errors, transport errors, timeouts
# and undecodable bodies. Callers that must fail closed catch this, not Exception.
TRANSPORT_ERRORS = (aiohttp.ClientError, TimeoutError, OSError, ValueError)


class Conflict(ApiError):
    """409 on a write: the resourceVersion it carried is stale."""


class AlreadyExists(ApiError):
    """409 on a create: an object with that name exists."""


@dataclasses.dataclass(frozen=True)
class Outcome:
    """A mutating (or dry-run) request's result: never raises on an HTTP error status.

    audit_id is the API server's `Audit-Id` response header, present on every response, so a
    denied request has one too (spec §14, reconciliation joins on it).
    """

    status: int
    audit_id: str | None
    message: str = ""
    body: dict | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class KubeApi:
    def __init__(self, credentials: Callable[[], Credentials]) -> None:
        self._credentials = credentials
        first = credentials()
        self._server = first.server
        self._incidents = (
            f"{first.server}/apis/{GROUP}/{VERSION}/namespaces/{NAMESPACE}/{PLURAL}"
        )
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            connector=aiohttp.TCPConnector(
                ssl=ssl.create_default_context(cafile=first.ca_path)
            ),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def _call(self, method: str, url: str, **kwargs) -> dict | None:
        """The JSON body, or None on 404. 409 raises Conflict (AlreadyExists on POST)."""
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._credentials().token}"
        async with self._session.request(method, url, headers=headers, **kwargs) as r:
            if r.status == 404:
                return None
            if r.status == 409:
                cls = AlreadyExists if method == "POST" else Conflict
                raise cls(r.status, method, url)
            if r.status >= 400:
                raise ApiError(r.status, method, url, (await r.text())[:200])
            return await r.json()

    async def list_incidents(self) -> list[dict]:
        body = await self._call("GET", self._incidents)
        if body is None:
            raise ApiError(404, "GET", self._incidents, "the Incident CRD is missing")
        return body["items"]

    async def get_incident(self, name: str) -> dict | None:
        return await self._call("GET", f"{self._incidents}/{name}")

    async def create_incident(self, body: dict) -> dict:
        return await self._call(
            "POST",
            self._incidents,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    async def patch_status(
        self, name: str, status: dict, resource_version: str
    ) -> dict:
        """Merge-patch status through the subresource; the resourceVersion is a precondition."""
        body = {"metadata": {"resourceVersion": resource_version}, "status": status}
        written = await self._call(
            "PATCH",
            f"{self._incidents}/{name}/status",
            data=json.dumps(body),
            headers={"Content-Type": "application/merge-patch+json"},
        )
        if written is None:
            raise ApiError(404, "PATCH", f"{self._incidents}/{name}/status")
        return written

    async def get_namespace(self, name: str) -> dict | None:
        return await self._call("GET", f"{self._server}/api/v1/namespaces/{name}")

    async def get_configmap(self, name: str) -> dict | None:
        return await self._call(
            "GET", f"{self._server}/api/v1/namespaces/{NAMESPACE}/configmaps/{name}"
        )

    async def _outcome(self, method: str, url: str, **kwargs) -> Outcome:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._credentials().token}"
        async with self._session.request(method, url, headers=headers, **kwargs) as r:
            text = await r.text()
            body = None
            try:
                parsed = json.loads(text)
                body = parsed if isinstance(parsed, dict) else None
            except ValueError:
                pass
            message = (body or {}).get("message") or text[:300]
            return Outcome(
                r.status, r.headers.get("Audit-Id"), str(message)[:500], body
            )

    @staticmethod
    def _dry(dry_run: bool) -> dict:
        return {"dryRun": "All"} if dry_run else {}

    async def get_deployment(self, namespace: str, name: str) -> dict | None:
        return await self._call(
            "GET",
            f"{self._server}/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
        )

    async def list_pods(self, namespace: str, selector: str) -> list[dict]:
        body = await self._call(
            "GET",
            f"{self._server}/api/v1/namespaces/{namespace}/pods",
            params={"labelSelector": selector},
        )
        return (body or {}).get("items", [])

    async def get_scale(self, namespace: str, name: str) -> dict | None:
        return await self._call(
            "GET",
            f"{self._server}/apis/apps/v1/namespaces/{namespace}/deployments/{name}/scale",
        )

    async def patch_scale(
        self, namespace: str, name: str, replicas: int, dry_run: bool = False
    ) -> Outcome:
        """Merge-patch the Scale subresource; dry_run sends the exact request with dryRun=All."""
        return await self._outcome(
            "PATCH",
            f"{self._server}/apis/apps/v1/namespaces/{namespace}/deployments/{name}/scale",
            params=self._dry(dry_run),
            data=json.dumps({"spec": {"replicas": replicas}}),
            headers={"Content-Type": "application/merge-patch+json"},
        )

    async def evict(
        self, namespace: str, pod: str, uid: str, dry_run: bool = False
    ) -> Outcome:
        """Create an Eviction for one pod, preconditioned on its UID."""
        body = {
            "apiVersion": "policy/v1",
            "kind": "Eviction",
            "metadata": {"name": pod, "namespace": namespace},
            "deleteOptions": {"preconditions": {"uid": uid}},
        }
        return await self._outcome(
            "POST",
            f"{self._server}/api/v1/namespaces/{namespace}/pods/{pod}/eviction",
            params=self._dry(dry_run),
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._session.close()
