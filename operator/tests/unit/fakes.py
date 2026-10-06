"""An in-memory stand-in for KubeApi: the API state outlives any Poller or Loop instance."""

import copy
import datetime

from nexus_operator.kube import AlreadyExists, Conflict
from nexus_operator.model import iso

T0 = datetime.datetime(2026, 10, 2, 9, 0, 0, tzinfo=datetime.timezone.utc)


def merge(base: dict, patch: dict) -> dict:
    """RFC 7386 JSON merge patch."""
    out = dict(base)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class FakeApi:
    def __init__(self, namespaces: dict | None = None, configmaps: dict | None = None):
        self.incidents: dict[str, dict] = {}
        self.namespaces = namespaces or {}  # name -> labels
        self.configmaps = configmaps or {}  # name -> ConfigMap or None
        self.clock = T0
        self.rv = 100
        self.creates: list[str] = []
        self.writes: list[tuple[str, dict]] = []
        self.before_write = None  # callable(name): simulate a concurrent writer

    def _next_rv(self) -> str:
        self.rv += 1
        return str(self.rv)

    # --- KubeApi surface ---
    async def list_incidents(self) -> list[dict]:
        return copy.deepcopy(list(self.incidents.values()))

    async def get_incident(self, name: str) -> dict | None:
        inc = self.incidents.get(name)
        return copy.deepcopy(inc) if inc else None

    async def create_incident(self, body: dict) -> dict:
        name = body["metadata"]["name"]
        self.creates.append(name)
        if name in self.incidents:
            raise AlreadyExists(409, "POST", name)
        obj = copy.deepcopy(body)
        obj["metadata"]["creationTimestamp"] = iso(self.clock)
        obj["metadata"]["resourceVersion"] = self._next_rv()
        self.incidents[name] = obj
        return copy.deepcopy(obj)

    async def patch_status(
        self, name: str, status: dict, resource_version: str
    ) -> dict:
        if self.before_write is not None:
            self.before_write(name)
        obj = self.incidents[name]
        if obj["metadata"]["resourceVersion"] != resource_version:
            raise Conflict(409, "PATCH", name)
        obj["status"] = merge(obj.get("status") or {}, status)
        obj["metadata"]["resourceVersion"] = self._next_rv()
        self.writes.append((name, status))
        return copy.deepcopy(obj)

    async def get_namespace(self, name: str) -> dict | None:
        if name not in self.namespaces:
            return None
        return {"metadata": {"name": name, "labels": self.namespaces[name]}}

    async def get_configmap(self, name: str) -> dict | None:
        return self.configmaps.get(name)

    # --- test helpers ---
    def put(
        self,
        name: str,
        *,
        created: datetime.datetime,
        status: dict | None = None,
        labels: dict | None = None,
        spec: dict | None = None,
    ) -> dict:
        obj = {
            "metadata": {
                "name": name,
                "creationTimestamp": iso(created),
                "resourceVersion": self._next_rv(),
                "labels": labels or {},
            },
            "spec": spec
            or {
                "source": {"type": "detector"},
                "target": {
                    "namespace": "nexus-dev",
                    "kind": "Deployment",
                    "name": "sample-api",
                },
                "detectedAt": iso(created),
            },
        }
        if status is not None:
            obj["status"] = status
        self.incidents[name] = obj
        return obj

    def set_status(self, name: str, status: dict) -> None:
        """Another writer (Kopf, the intake handler) patches status."""
        obj = self.incidents[name]
        obj["status"] = merge(obj.get("status") or {}, status)
        obj["metadata"]["resourceVersion"] = self._next_rv()
