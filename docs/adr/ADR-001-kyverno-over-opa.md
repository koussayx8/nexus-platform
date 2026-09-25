# ADR-001: Kyverno over OPA Gatekeeper

## Status: Accepted

## Context
Need a Kubernetes admission controller for policy enforcement
and image signature verification.

## Decision
Use Kyverno exclusively. Remove OPA Gatekeeper.

## Rationale
- Kyverno handles admission control AND Cosign image verification natively (v1.10+)
- OPA requires separate tooling for image signature verification
- Kyverno uses YAML-native policies — no Rego language to learn
- Running both creates dual webhook overhead in the admission path
- Single policy engine = single source of truth

## Tradeoff
OPA/Rego is more expressive for complex policy logic.
If policies grow beyond Kyverno's capability, migrate to OPA — document in future work.
