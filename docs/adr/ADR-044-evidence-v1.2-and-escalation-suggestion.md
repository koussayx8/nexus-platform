# ADR-044: evidence/v1.2 (rollout diff, pod spec, other alerts) and the escalation suggestion

## Status: Proposed, 2026-11-20 (owner, after the Block 5 trial runs: items (m), (n), (o)). Before the `v0.6.0` freeze.

## Context
In S3 (2026-11-19) R3 named the fault class right (`bad_rollout`) but could not say what had changed: the bundle
held the new ReplicaSet's name and image digest, not its probe change, so the model proposed a restart. In S6
(2026-11-20) two namespaces alerted in the same second and each Incident saw only its own alert. An escalation
carried a reason and a recommendation kind, but nothing a human could review as a concrete change, and nothing
that said the runbooks had a hole.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | **evidence/v1.2** = v1.1 plus three read-only fields. The operator writes v1.2; the Reasoner, the operator's client and the harness client accept v1, v1.1 and v1.2. | Additive: recorded v1.1 bundles stay valid input. |
| 2 | **`workload.podSpec`**: per container of the current ReplicaSet's template: name, image, container ports, the readiness, liveness and startup probes in short (`httpGet /ready :8000, period 10s, failures 2`; an `exec` probe is shown without its command), resources, env var NAMES and `envFrom` references. Never env values, commands or arguments. | What the workload is meant to be, in the terms a bad rollout changes. A command line or a value may carry a secret; a name does not. |
| 3 | **`workload.rolloutDiff`**: the same fields compared with the previous ReplicaSet's template (by revision), one line each (`sample-api.readinessProbe: httpGet /ready … -> httpGet /readyz …`); env names added or removed; null without a previous revision; `[]` when nothing in these terms changed (a `rollout restart` changes only an annotation). | The model sees the change itself, from the cluster's own objects. **No Git, no commit messages** (blinding, ADR-022): the diff is between ReplicaSets, not commits. |
| 4 | **`cluster.activeAlerts`**: the other firing Nexus alerts at collection time (`ALERTS{alertstate="firing", alertname=~"Nexus.*"}`), alert name and target (`nexus_target`, else namespace) only, sorted, deduplicated, at most 10; the Incident's own alert on its own target is left out; null without Prometheus. | A spike that hits both namespaces, or a dependency failing under several services, is visible as such. |
| 5 | **Caps**: 300 characters a value or diff line, 20 diff lines, 40 env names a container, 10 alerts. Everything is untrusted text: the operator's redaction runs over every string of the bundle, as before. | Bounded prompt growth; no field can carry a payload of any size. |
| 6 | **`escalation.suggestedChange`** (text or YAML, at most 4,000 characters) and **`escalation.runbookGap`** (text, at most 1,000): optional strings in `proposal/v1`'s escalation object. A non-string is an invalid proposal. Untrusted, display only, **never applied**: no code path reads them for a decision or a request. The Incident shows them cut to their caps (`status.proposal.escalation`); the Flight Recorder keeps the proposal as received (`proposals.parsed_proposal`). | A human gets a concrete change to review and the runbook author a gap to close, without giving model text any authority. |
| 7 | **Prompt rule 8**: when escalating, fill `suggestedChange` with the concrete change (from `rolloutDiff` and `podSpec`) and `runbookGap` when the chunks did not cover the case; both shown to a human, never applied; leave them out when there is nothing concrete. `promptVersion` becomes **`prompt/v2.1`** (a rule changed); `prompt/v3` stays reserved for the budget, after the bake-off. | The rule is part of the experimental condition, so it is versioned. |
| 8 | **Shown**: `nexusctl explain` adds an `UNTRUSTED: ESCALATION SUGGESTION (never applied)` box; the Flight Recorder dashboard has an "Escalations" table (first 1,000 characters of the suggestion, 500 of the gap, plain text). The bot's Explain button (#252) must show them the same way. | One label everywhere. |

## Consequences
- The prompt grows: on the 2026-11-19 S1 bundle with the real `nexus-dev` templates and two other alerts, the
  estimate goes from 4,469 to 4,869 tokens (still trim level 2 under 5,000). A larger bundle trims its log excerpt
  one level earlier (ADR-042 decision 3) until prompt/v3 sets the budget.
- `prompt/v2.1` and `evidence/v1.2` are frozen values: runs before and after this change are not comparable (#234).
- The RBAC is unchanged: the collector already lists ReplicaSets; Prometheus is already read.
- R1 does not read the new fields and is unaffected.
