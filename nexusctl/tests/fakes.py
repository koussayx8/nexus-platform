"""Test doubles and fixtures shared by the nexusctl tests."""

import copy
from collections.abc import Callable
from typing import Any

from nexus_client.errors import Conflict, NotFound
from nexus_client.incidents import Reply
from nexus_client.models import Incident

HASH = "sha256:ab12"


def incident_dict(name: str = "inc-s2-run0042", *, phase: str = "AwaitingApproval",
                  action: str = "scale_deployment", replicas: int = 4, proposal_hash: str = HASH,
                  hypothesis: str = "CPU saturation on sample-api", approval: dict[str, Any] | None = None,
                  rv: str = "100") -> dict[str, Any]:
    d: dict[str, Any] = {
        "apiVersion": "nexus.io/v1alpha1", "kind": "Incident",
        "metadata": {"name": name, "namespace": "nexus-system", "resourceVersion": rv,
                     "creationTimestamp": "2026-10-14T09:12:05Z"},
        "spec": {"source": {"type": "detector", "alertname": "NexusCpuAnomaly",
                            "requester": "system:serviceaccount:nexus-system:nexus-operator"},
                 "target": {"namespace": "nexus-dev", "kind": "Deployment", "name": "sample-api"},
                 "detectedAt": "2026-10-14T09:12:03Z"},
        "status": {"phase": phase, "reason": "proposed_escalation" if phase == "AwaitingApproval" else "x",
                   "autonomyLevel": 2,
                   "evidence": {"bundleHash": "sha256:9e41", "recordId": 118, "redactions": 3},
                   "reasoner": {"modelId": "openai/gpt-oss-120b", "promptVersion": "p-1.2",
                                "indexHash": "sha256:77c0", "chunks": ["rb-s2#2", "rb-s2#1"]},
                   "proposal": {"hash": proposal_hash, "actionId": action,
                                "parameters": {"replicas": replicas} if action == "scale_deployment" else {},
                                "faultClass": "cpu_saturation", "confidence": 0.82, "abstain": False,
                                "hypothesis": hypothesis},
                   "gate": {"verdict": "requires_approval", "firstFailingStage": "none", "dryRun": "allowed"}},
    }
    if approval is not None:
        d["spec"]["approval"] = approval
    return d


class FakeApi:
    """An in-memory IncidentApi. `after_first_get` runs once after the first get (to simulate the
    proposal changing while the Approver reads the screen)."""

    def __init__(self, *items: dict[str, Any]) -> None:
        self.items = {i["metadata"]["name"]: copy.deepcopy(i) for i in items}
        self.patches: list[tuple[str, dict[str, Any], str]] = []
        self.gets = 0
        self.after_first_get: Callable[[dict[str, Any]], None] | None = None
        self.patch_error: Exception | None = None
        self.audit_id = "8c2f1e"

    def get(self, name: str) -> Reply:
        if name not in self.items:
            raise NotFound("not found")
        inc = Incident.model_validate(copy.deepcopy(self.items[name]))
        self.gets += 1
        if self.gets == 1 and self.after_first_get:
            self.after_first_get(self.items[name])
        return Reply(inc, self.audit_id)

    def list(self) -> list[Incident]:
        return [Incident.model_validate(copy.deepcopy(i)) for i in self.items.values()]

    def patch_approval(self, name: str, approval: dict[str, Any], resource_version: str) -> Reply:
        self.patches.append((name, copy.deepcopy(approval), resource_version))
        if self.patch_error:
            raise self.patch_error
        item = self.items[name]
        if item["metadata"]["resourceVersion"] != resource_version:
            raise Conflict("the object has been modified")
        stamped = {**approval, "approvedBy": "approver-1", "approvedAt": "2026-10-14T09:13:40Z"}
        item["spec"]["approval"] = stamped  # what Kyverno K5 does on admission
        return Reply(Incident.model_validate(copy.deepcopy(item)), self.audit_id)
