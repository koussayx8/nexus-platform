# ADR-003: Autonomy Ladder — Graded AI Trust Model

**Status:** Accepted
**Date:** 2026-05-06
**Author:** Koussay Belhouchet

## Context

The NEXUS self-healing operator needs a trust model that answers the biggest enterprise
question: "Can I trust the AI to touch production?" Jumping directly from manual operations
to full auto-remediation is unsafe and undefensible in a thesis context.

The architecture review (nexus_architecture_review.md §W4) specifically flagged the
self-healing safety model as underspecified. This ADR addresses that gap.

## Decision

Implement a five-level **Autonomy Ladder** inspired by autonomous vehicle levels:

| Level | Name | AI Behavior | Human Role | Risk |
|-------|------|-------------|------------|------|
| **0** | Observe | Collect metrics, detect anomalies | Full manual response | None |
| **1** | Diagnose | Anomaly detection + root cause analysis | Interprets diagnosis | None |
| **2** | Recommend | Suggest remediation action + runbook | Approves/rejects | Low |
| **3** | Approve | Execute action after human confirmation | Clicks "approve" | Medium |
| **4** | Auto-Heal | Execute low-risk actions automatically | Monitors + overrides | High |

### Implementation

1. **Per-service annotation:** `nexus.io/autonomy-level: "0"` on each Deployment
   - Services opt-in to higher levels individually
   - New services start at Level 0 (safe default)
   - Level can be changed without redeployment (annotation patch)

2. **Action risk classification:**
   - **LOW:** restart pod, scale up replicas, toggle feature flag
   - **MEDIUM:** rollback deployment, drain node, modify resource limits
   - **HIGH:** delete PVC, modify network policy, scale to zero

3. **Guard rails (all levels):**
   - Incident Flight Recorder logs every step (auditable)
   - Blast radius estimation before action execution
   - Automatic circuit breaker: 3 failed remediations → drop to Level 0
   - Dry-run mode for Level 4 actions during initial deployment

4. **Failure documentation:**
   - Every misdiagnosis is logged as a `LadderFailureEvent`
   - ADR-003 appendix will contain real examples of "AI was wrong, Ladder prevented harm"
   - This addresses the architecture review's recommendation about failure mode documentation

## Consequences

- Safety becomes a **thesis feature**, not a weakness
- Committee question "what if AI is wrong?" has a designed answer
- Scope is manageable: Week 1-12 can operate at Levels 0-1, Levels 2-4 added in Week 15-16
- The Ladder provides natural structure for the Chaos Benchmark Pack experiments
  (run same scenario at Level 0 vs Level 2 vs Level 4, compare MTTR)

## References

- nexus_architecture_review.md §W4 (Self-Healing Safety Model)
- SAE J3016 (Autonomous Vehicle Levels — conceptual inspiration)
- Netflix automated remediation patterns (deterministic runbook mapping)
