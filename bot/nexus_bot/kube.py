"""Cluster access, split by identity (ADR-040 decisions 4 and 5).

`Reader` is everything the bot does as its own ServiceAccount: read Incidents, two namespaces'
level labels, the Kill Switch, pods and Deployments. `Approver` is the only write: merge-patch
`spec.approval` while impersonating the allowlisted approver and group. Commands and pushes are
given a Reader only, so they cannot write.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from nexus_client.errors import NexusClientError, NotFound, Unreachable
from nexus_client.incidents import NAMESPACE, KubeIncidentApi, Reply
from nexus_client.models import Incident

LEVEL_LABEL = "nexus.io/autonomy-level"
KILLSWITCH = "nexus-killswitch"


@dataclass(frozen=True)
class Deploy:
    name: str
    ready: int
    desired: int


@dataclass(frozen=True)
class Pod:
    name: str
    phase: str
    restarts: int
    ready: bool


class Reader(Protocol):
    def list_incidents(self) -> list[Incident]: ...

    def get_incident(self, name: str) -> Incident: ...

    def level(self, namespace: str) -> str | None: ...

    def killswitch(self) -> str | None: ...

    def deployments(self, namespace: str) -> list[Deploy]: ...

    def pods(self, namespace: str) -> list[Pod]: ...


class Approver(Protocol):
    def patch_approval(self, name: str, approval: dict[str, Any], resource_version: str) -> Reply: ...


class ApproverFactory(Protocol):
    def __call__(self, user: str, group: str) -> Approver: ...


def _wrap(fn: Any, *args: Any, **kw: Any) -> Any:
    from kubernetes.client import ApiException
    from urllib3.exceptions import HTTPError

    try:
        return fn(*args, **kw)
    except ApiException as e:
        if e.status == 404:
            raise NotFound("not found") from None
        raise NexusClientError(f"{e.status} {e.reason}") from None
    except (HTTPError, OSError) as e:
        raise Unreachable(str(e)) from None


class KubeReader:
    """In-cluster, as system:serviceaccount:nexus-bot:nexus-bot. No impersonation header."""

    def __init__(self) -> None:
        from kubernetes import client

        self._incidents = KubeIncidentApi(NAMESPACE, in_cluster=True)  # loads the in-cluster config
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()

    def list_incidents(self) -> list[Incident]:
        return self._incidents.list()

    def get_incident(self, name: str) -> Incident:
        return self._incidents.get(name).incident

    def level(self, namespace: str) -> str | None:
        ns = _wrap(self._core.read_namespace, namespace)
        return (ns.metadata.labels or {}).get(LEVEL_LABEL)

    def killswitch(self) -> str | None:
        try:
            cm = _wrap(self._core.read_namespaced_config_map, KILLSWITCH, NAMESPACE)
        except NotFound:
            return None
        return (cm.data or {}).get("state")

    def deployments(self, namespace: str) -> list[Deploy]:
        items = _wrap(self._apps.list_namespaced_deployment, namespace).items
        return [Deploy(d.metadata.name, d.status.ready_replicas or 0, d.spec.replicas or 0) for d in items]

    def pods(self, namespace: str) -> list[Pod]:
        out = []
        for p in _wrap(self._core.list_namespaced_pod, namespace).items:
            statuses = p.status.container_statuses or []
            out.append(Pod(p.metadata.name, p.status.phase or "-",
                           sum(c.restart_count or 0 for c in statuses),
                           bool(statuses) and all(c.ready for c in statuses)))
        return out


def kube_approver(user: str, group: str) -> Approver:
    """The three writes' identity: Impersonate-User and Impersonate-Group on every request."""
    return KubeIncidentApi(NAMESPACE, in_cluster=True, as_user=user, as_group=group)
