"""Pydantic models of the Incident CRD (spec §8). `extra="ignore"`: the client reads what it
needs and never rewrites the rest. Field names are the CRD's own (camelCase)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Source(_Model):
    type: Literal["detector", "human"]
    alertname: str | None = None
    fingerprint: str | None = None
    requester: str | None = None
    note: str | None = None


class Target(_Model):
    namespace: str
    kind: str
    name: str


class Approval(_Model):
    """spec.approval. approvedBy and approvedAt are stamped by Kyverno K5; the client never sets them."""

    decision: Literal["approved", "rejected"]
    actionId: str | None = None  # noqa: N815 - CRD field name
    parameters: dict[str, Any] | None = None
    proposalHash: str | None = None  # noqa: N815
    expiresAt: str | None = None  # noqa: N815
    reason: str | None = None
    approvedBy: str | None = None  # noqa: N815
    approvedAt: str | None = None  # noqa: N815


class Proposal(_Model):
    hash: str
    actionId: str  # noqa: N815
    parameters: dict[str, Any] = Field(default_factory=dict)
    faultClass: str | None = None  # noqa: N815
    confidence: float | None = None
    abstain: bool = False
    hypothesis: str | None = None


class Spec(_Model):
    source: Source
    target: Target
    detectedAt: str  # noqa: N815
    approval: Approval | None = None


class Status(_Model):
    phase: str | None = None
    reason: str | None = None
    autonomyLevel: int | None = None  # noqa: N815
    timestamps: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    reasoner: dict[str, Any] = Field(default_factory=dict)
    proposal: Proposal | None = None
    gate: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)


class Metadata(_Model):
    name: str
    namespace: str | None = None
    resourceVersion: str | None = None  # noqa: N815
    creationTimestamp: str | None = None  # noqa: N815
    labels: dict[str, str] = Field(default_factory=dict)


class Incident(_Model):
    apiVersion: str = "nexus.io/v1alpha1"  # noqa: N815
    kind: str = "Incident"
    metadata: Metadata
    spec: Spec
    status: Status = Field(default_factory=Status)
