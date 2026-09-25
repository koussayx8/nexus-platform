# ADR-009: Incident Flight Recorder Architecture

**Status:** Accepted
**Date:** 2026-05-06
**Author:** Koussay Belhouchet

## Context

The NEXUS AI operator (anomaly detection → diagnosis → remediation) is a black box without
an audit trail. The architecture review noted this as a critical gap for both enterprise
trust and thesis defensibility.

The Incident Flight Recorder makes every AI decision traceable, reproducible, and
demo-friendly.

## Decision

Every incident produces an immutable **timeline** of `IncidentTimelineEvent` records:

### Event Schema

```python
@dataclass
class IncidentTimelineEvent:
    incident_id: str          # UUID, groups all events for one incident
    timestamp: datetime       # UTC, when this step occurred
    event_type: str           # enum: see below
    source: str               # which component emitted this event
    autonomy_level: int       # 0-4, current level when event fired
    data: dict                # event-type-specific payload
    duration_ms: int | None   # how long this step took
    confidence: float | None  # AI confidence score (0.0-1.0)
```

### Event Types (ordered by incident lifecycle)

| event_type | Source | Description |
|------------|--------|-------------|
| `anomaly_detected` | LSTM detector | Metrics exceeded threshold |
| `metrics_queried` | PromQL client | Prometheus metrics pulled for context |
| `logs_searched` | Loki/log client | Relevant log lines retrieved |
| `runbook_retrieved` | RAG engine | Top-k runbook sections from ChromaDB |
| `diagnosis_generated` | LLM (Groq) | Root cause analysis text |
| `action_recommended` | LLM (Groq) | Suggested remediation |
| `human_notified` | Notification | Slack/webhook alert sent (Level 2-3) |
| `human_approved` | API/webhook | Human clicked approve (Level 3) |
| `action_executed` | kopf operator | Remediation applied to cluster |
| `action_result` | kopf operator | Success/failure of remediation |
| `mttr_recorded` | Flight Recorder | Time from detection to resolution |
| `postmortem_generated` | LLM (Groq) | Auto-generated incident report |

### Storage

- **Primary:** PostgreSQL table `incident_timeline` (immutable append-only)
- **Dev fallback:** SQLite file in PVC (for k3s single-node)
- **Events are written as they happen** — never reconstructed after the fact

### Visualization

- Grafana dashboard with timeline panel (incident_id filter)
- Backstage plugin (future) showing per-service incident history
- JSON export for Chaos Benchmark Pack CSV reports

## Consequences

- Every AI action is auditable ("prove it worked" has a data-backed answer)
- MTTR is calculated from real timestamps, not estimated
- Chaos Benchmark Pack reads directly from Flight Recorder data
- Postmortem Generator consumes the timeline to produce Markdown reports
- Demo: single incident → visual timeline → "this is what NEXUS did and why"

## Implementation Plan

- Week 9-10: Schema + event emitter (Python dataclass + SQLite)
- Week 14-15: LLM events integrated (diagnosis + recommendation)
- Week 16: Full timeline with action results
- Week 17-20: Grafana dashboard + benchmark export
