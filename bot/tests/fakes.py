"""Test doubles: Telegram and Kubernetes behind fakes. Nothing here opens a socket."""

import copy
from datetime import UTC, datetime
from typing import Any

from nexus_client.errors import Conflict, Denied, NotFound
from nexus_client.incidents import Reply
from nexus_client.models import Incident

from nexus_bot.kube import Deploy, Pod

UID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
HASH = "sha256:" + "ab" * 32
NOW = datetime(2026, 11, 28, 10, 0, 0, tzinfo=UTC)
APPROVER = "koussay-approver"
OWNER_ID = 424242


def incident_dict(name: str = "inc-0123abcd-1764324000", *, uid: str = UID, phase: str = "AwaitingApproval",
                  proposal_hash: str = HASH, approval: dict[str, Any] | None = None, rv: str = "100",
                  stamp: str | None = "2026-11-28T09:58:00Z", hypothesis: str = "CPU saturation") -> dict[str, Any]:
    key = "awaitingapproval" if phase == "AwaitingApproval" else "terminal"
    d: dict[str, Any] = {
        "metadata": {"name": name, "namespace": "nexus-system", "uid": uid, "resourceVersion": rv,
                     "creationTimestamp": "2026-11-28T09:57:00Z"},
        "spec": {"source": {"type": "detector", "alertname": "NexusCpuAnomaly"},
                 "target": {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"},
                 "detectedAt": "2026-11-28T09:56:58Z"},
        "status": {"phase": phase, "reason": "proposed_escalation", "autonomyLevel": 2,
                   "timestamps": {key: stamp} if stamp else {},
                   "proposal": {"hash": proposal_hash, "actionId": "scale_deployment",
                                "parameters": {"replicas": 4}, "faultClass": "cpu_saturation",
                                "hypothesis": hypothesis},
                   "gate": {"verdict": "requires_approval"}},
    }
    if approval is not None:
        d["spec"]["approval"] = approval
    return d


class FakeCluster:
    """Reader and approver over one in-memory store. The approver side does what the API server,
    K5 and C2 do: stamps approvedBy from the impersonated user, refuses a non-approver, checks the
    resourceVersion and keeps the approval write-once."""

    def __init__(self, *items: dict[str, Any]) -> None:
        self.items = {i["metadata"]["name"]: copy.deepcopy(i) for i in items}
        self.patches: list[dict[str, Any]] = []  # {user, group, name, approval, rv}
        self.levels = {"nexus-dev": "2", "nexus-prod": "1"}
        self.state: str | None = "active"
        self.fail_list: Exception | None = None
        self.patch_error: Exception | None = None
        self.approver_users = {APPROVER}

    # Reader
    def list_incidents(self) -> list[Incident]:
        if self.fail_list:
            raise self.fail_list
        return [Incident.model_validate(copy.deepcopy(i)) for i in self.items.values()]

    def get_incident(self, name: str) -> Incident:
        if name not in self.items:
            raise NotFound("not found")
        return Incident.model_validate(copy.deepcopy(self.items[name]))

    def level(self, namespace: str) -> str | None:
        return self.levels.get(namespace)

    def killswitch(self) -> str | None:
        return self.state

    def deployments(self, namespace: str) -> list[Deploy]:
        return [Deploy("sample-api", 2, 3, 3, 2, "2026-11-23T10:00:00Z")]

    def pods(self, namespace: str) -> list[Pod]:
        return [Pod("sample-api-7d9-abc", "Running", 4, True, 1, 1, "Running", "2026-11-28T08:00:00Z"),
                Pod("sample-api-7d9-def", "Pending", 0, False, 1, 0, "Pending", "2026-11-28T09:57:00Z")]

    # ApproverFactory
    def approver_for(self, user: str, group: str) -> "FakeApprover":
        return FakeApprover(self, user, group)


class FakeApprover:
    def __init__(self, cluster: FakeCluster, user: str, group: str) -> None:
        self.c, self.user, self.group = cluster, user, group

    def patch_approval(self, name: str, approval: dict[str, Any], resource_version: str) -> Reply:
        self.c.patches.append({"user": self.user, "group": self.group, "name": name,
                               "approval": copy.deepcopy(approval), "rv": resource_version})
        if self.c.patch_error:
            raise self.c.patch_error
        item = self.c.items[name]
        if self.group != "nexus-approvers" or self.user not in self.c.approver_users:
            raise Denied("K5 approvers-only: only group nexus-approvers may set spec.approval", "aud-1")
        if item["metadata"]["resourceVersion"] != resource_version:
            raise Conflict("the object has been modified")
        if "approval" in item["spec"]:
            raise Denied("C2: spec.approval is write-once", "aud-2")
        item["spec"]["approval"] = {**approval, "approvedBy": self.user, "approvedAt": "2026-11-28T10:00:01Z"}
        item["metadata"]["resourceVersion"] = str(int(resource_version) + 1)
        return Reply(Incident.model_validate(copy.deepcopy(item)), "aud-ok")


class FakeTransport:
    """Records every Bot API call; returns scripted results per method."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: dict[str, list[Any]] = {}
        self.fail: dict[str, Exception] = {}
        self.next_message_id = 1

    def call(self, method: str, payload: dict[str, Any], timeout: float) -> Any:
        self.calls.append((method, copy.deepcopy(payload)))
        if method in self.fail:
            raise self.fail[method]
        queue = self.results.get(method)
        if queue:
            return queue.pop(0)
        if method == "sendMessage":
            self.next_message_id += 1
            return {"message_id": self.next_message_id, "chat": {"id": payload["chat_id"]}}
        if method == "getUpdates":
            return []
        return True

    def sent(self) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == "sendMessage"]


def message(text: str, *, user: int = OWNER_ID, chat: int | None = None, chat_type: str = "private",
            update_id: int = 1, is_bot: bool = False) -> dict[str, Any]:
    return {"update_id": update_id,
            "message": {"message_id": 10, "text": text, "from": {"id": user, "is_bot": is_bot},
                        "chat": {"id": user if chat is None else chat, "type": chat_type}}}


def press(data: str, *, user: int = OWNER_ID, chat: int | None = None, chat_type: str = "private",
          update_id: int = 2) -> dict[str, Any]:
    return {"update_id": update_id,
            "callback_query": {"id": "cb-1", "data": data, "from": {"id": user, "is_bot": False},
                               "message": {"message_id": 77,
                                           "chat": {"id": user if chat is None else chat, "type": chat_type}}}}
