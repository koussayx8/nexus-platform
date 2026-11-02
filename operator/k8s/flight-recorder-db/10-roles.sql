-- Flight Recorder roles (spec §14; ADR-031). docker-entrypoint.sh runs *.sql and *.sh in name order,
-- once, on the socket-only temporary server, only when PGDATA is empty: 05-schema.sql, this file,
-- then 99-ready.sh. Same pattern as dependency-db (ADR-020).
-- Passwords come from the environment through psql \getenv: never in argv, never echoed.
--
-- Roles (least privilege; none owns a table):
--   nexus_writer  the operator: INSERT, SELECT; UPDATE only the completion columns.
--   nexus_runner  the experiment runner and reconciliation: INSERT/UPDATE runs, INSERT audit_links.
--   nexus_reader  Grafana: SELECT only.
\set ON_ERROR_STOP on
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
\getenv pw_writer WRITER_PASSWORD
\getenv pw_runner RUNNER_PASSWORD
\getenv pw_reader READER_PASSWORD

CREATE ROLE nexus_writer LOGIN CONNECTION LIMIT 10 PASSWORD :'pw_writer';
CREATE ROLE nexus_runner LOGIN CONNECTION LIMIT 5  PASSWORD :'pw_runner';
CREATE ROLE nexus_reader LOGIN CONNECTION LIMIT 10 PASSWORD :'pw_reader';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO nexus_writer, nexus_runner, nexus_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO nexus_writer, nexus_runner, nexus_reader;

-- The operator: append, and complete the two rows it opened.
GRANT INSERT ON incidents, transitions, evidence_bundles, proposals, gate_decisions,
                actions, verifications TO nexus_writer;
GRANT UPDATE (terminal_phase, reason) ON incidents TO nexus_writer;
GRANT UPDATE (request_at, audit_id, http_status, result) ON actions TO nexus_writer;

-- The runner: run metadata and the reconciliation output.
GRANT INSERT ON runs, audit_links TO nexus_runner;
GRANT UPDATE (ended_at, discarded, discard_reason) ON runs TO nexus_runner;
