# ADR-035: The Operator's Reasoner Client

**Status:** Proposed (task 2c, G19, PR to `dev`)
**Date:** 2026-11-03
**Spec:** §4, §6 transitions 4-6, §14, §16, §17; ADR-031, ADR-034; Q1

## Decisions

| # | Decision |
| --- | --- |
| 1 | **Off by default.** With `reasonerEndpoint` empty, Diagnosing is exactly what ADR-034 describes: R1 on the redacted bundle, `status.reasoner` = `{mode: rules, fallbackUsed: false}`. |
| 2 | **One call, only the redacted bundle.** With the endpoint set, Diagnosing sends `POST {endpoint}/v1/propose` with `{bundle, context}` and an `X-Request-Id`: 30 s timeout, one attempt, no retry. `context` carries the incident uid, level (`L0`-`L3`), catalogue version, and the mode, model id and reasoning effort from the config keys `reasonerMode`, `reasonerModel`, `reasonerEffort`. Before sending, the client checks the bundle is the operator's redacted `evidence/v1`: `redaction.counts` present, `hash` equal to the canonical hash, **and a second redaction pass over every string finds nothing**. Otherwise nothing is sent and the Incident ends `Escalated` / `evidence_error` (§6 transition 3: a redaction error escalates). The second pass works because redaction is now idempotent: the `password` pattern skips a value that is already a `[REDACTED:…]` marker. |
| 3 | **The Reasoner is never trusted.** The answer is read with a 1 MiB cap and parsed, and `proposal.validate` runs in the client **and again in the operator** before the proposal is stored or reaches the Gate. Only the schema is checked there. Whether the action is in the catalogue, within bounds, and allowed at the level is the Safety Gate's decision, so that an out-of-envelope proposal (arm R0) is blocked and counted at the Gate (`not_in_catalogue`, `bounds_exceeded`). The Reasoner's metadata is reduced to short scalars. |
| 4 | **Fallback once.** A timeout, a transport failure, a 5xx, a 422, an unparseable answer or a schema-invalid proposal is answered by R1, once, with `fallbackUsed = true`. An invalid R1 proposal ends the Incident `Blocked` / `schema_invalid` (§7: invalid twice). |
| 5 | **The call does not stall the loop.** The Reasoner call runs as a task and a later tick collects it, so a 30 s call cannot trip the 30 s liveness limit of the reconcile loop. Because the bundle and the answer are kept in memory between ticks, a recorder outage after the answer never causes a second request; a restart of the operator mid-diagnosis starts again. A call still not finished at the 60 s Diagnosing timeout ends `Escalated` / `reasoner_unavailable`. |
| 6 | **What is recorded** (the Flight Recorder `proposals` table, one row per proposal): reasoner mode, model id, prompt version, corpus hash (`index_hash`), retrieved chunk ids, the model's **raw output**, the parsed proposal and its hash, schema validity, latency, `fallback_used`, and the **request id**. A failed attempt gets its own row (hash of the request id and reason; `schema_valid` false; the raw output, if any, and the reason, for a 422 its `detail`), and the R1 row that replaces it carries `fallback_used = true` and the same request id. `status.reasoner` has the same fields for the proposal in use. Counters: `nexus_operator_reasoner_calls_total{result}` and `nexus_operator_reasoner_fallbacks_total`. |
| 7 | **Schema: two additive columns on `proposals`,** `request_id text` and `reject_reason text`, added by `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in `05-schema.sql` (idempotent: it also applies to a volume initialised before). The Writer's `record_proposal` takes them as keyword arguments and **leaves them out of the INSERT while the database lacks them** (it looks at `information_schema` and keeps only a positive answer), so a new image against an old volume records everything else instead of failing closed. Nothing else in §14 changed; grants are table-level and cover the new columns. |

## Consequences

- The operator opens a connection to the Reasoner only when `reasonerEndpoint` is set; the NetworkPolicy `operator-egress` already allows `nexus-reasoner:8080`.
- **Live check pending:** the Reasoner service on a cluster, the egress rule, the migration of an existing Flight Recorder volume (`psql -f 05-schema.sql` as the database owner), and an Incident that falls back after a real timeout.
- Spec v1.1 note: §16 says the proposal's `evidenceRefs` and `runbookRefs` must point at fields in the bundle and chunks retrieved; the Reasoner service checks them (422 `invalid_refs`); the operator does not re-resolve them, because they are recorded and never used for a decision.
