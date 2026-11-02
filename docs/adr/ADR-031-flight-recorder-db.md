# ADR-031: flight-recorder-db and the Writer

**Status:** Proposed (PR for task 1)
**Date:** 2026-11-02
**Spec:** §14 (schema, write-ahead rule), §17 (fail closed), §25; invariant I14

## Decisions

| # | Decision |
| --- | --- |
| 1 | PostgreSQL 17 (the dependency-db image digest) as a StatefulSet in `nexus-system` with a 2 Gi PVC (k3s local-path), deployed by the `nexus` Application through `operator/k8s/flight-recorder-db/`. The AppProject needs no change: StatefulSet, Service, ConfigMap and Secret are already whitelisted. |
| 2 | Schema = the §14 table, one SQL table per row (`05-schema.sql`, idempotent). Init runs once on an empty volume: `05-schema.sql`, `10-roles.sql`, `99-ready.sh` (readiness marker). Schema changes after first init are a later migration (UNKNOWN until needed). |
| 3 | Three roles, none owning a table. `nexus_writer` (operator): INSERT, SELECT, and UPDATE only `actions(request_at, audit_id, http_status, result)` and `incidents(terminal_phase, reason)`. `nexus_runner`: INSERT `runs`, `audit_links`; UPDATE `runs(ended_at, discarded, discard_reason)`. `nexus_reader` (Grafana): SELECT. No role may DELETE or TRUNCATE. Append-only is enforced by grants and tested. |
| 4 | Credentials: `scripts/flight-recorder-secrets.sh` (generate-or-reuse, `kubectl create` only, as ADR-019/020), called by `bootstrap.sh` as step f3. The DB pod gets `nexus-system/flight-recorder-db`; the operator will mount only `flight-recorder-writer` (never through Secrets RBAC); Grafana gets `monitoring/flight-recorder-reader`. |
| 5 | The Writer (`nexus_operator.recorder`): `Recorder` is the interface for stages; `PostgresRecorder` implements it over asyncpg. Appends are `ON CONFLICT DO NOTHING` on the spec's keys (idempotent). Any database error or timeout (3 s) raises `RecorderUnavailable`: the caller ends the incident `Blocked` / `recorder_unavailable`. |
| 6 | Write-ahead: `execute_recorded` commits the intent row, then calls `send`, then completes the row with the status and `Audit-Id`. If the intent fails, `send` is never called. If the completion fails after the request, the outcome reports `completion_pending` (§17). A replayed idempotency key never sends again. |
| 7 | `AuditedClient` (aiohttp, operator identity, token read per request) returns `ApiResult(status, audit_id, body)`, supports `dryRun=All`, and never raises on a 4xx: a denial is a recorded outcome whose message attributes the blocking layer. |
| 8 | The breaker's counts come from the recorder: `count_actions(target, since)` and `last_action_at(target)`, by intent time, joined to `incidents.target`. Which window, epoch and thresholds apply is the Safety Gate's (task 2). |
| 9 | Grafana: a labelled datasource ConfigMap (`grafana_datasource`) using `nexus_reader`, the password from env `FR_READER_PASSWORD` (Secret `flight-recorder-reader`, `optional`), and the dashboard `flight-recorder.json`. |

## Not decided here / limits

- `asyncpg` is added to `operator/requirements-dev.txt` for the tests only. The hashed runtime pin (two architectures, ADR-017) and the operator's configuration keys join `requirements.txt` when task 2 wires the Writer in, after the multi-arch pin work (task 5) has merged. Until then the image cannot connect: **live check pending** (the whole DB, the Secrets script, Grafana datasource and `Audit-Id` header on a live API server).
- No NetworkPolicy until M3: any pod can reach 5432. Traffic inside the cluster is plaintext (`sslmode=disable`); N1–N6 must allow operator, runner port-forward and Grafana.
- Whether the API server returns `Audit-Id` on every response, and whether a client-supplied `Audit-ID` header is honoured (which would let the id be written ahead), is the M2-5 spike: UNVERIFIED.
- ADR number 031 is the next free number on `dev` at branching; another task may take it first.
