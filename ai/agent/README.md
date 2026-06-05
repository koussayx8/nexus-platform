# NEXUS AI Agent — Kubernetes Operator

Autonomous incident response operator built with kopf (Kubernetes Operator Python Framework).

## Autonomy Ladder
1. **Level 0**: Manual — human performs all actions
2. **Level 1**: Assist — agent suggests, human approves
3. **Level 2**: Auto — agent acts, human monitors
4. **Level 3**: Full — agent operates independently with guardrails

## Architecture
- kopf watches Incident CRDs
- Redis for state/event bus
- PostgreSQL for audit trail

## Timeline
Week 11-12 implementation.
