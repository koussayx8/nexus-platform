-- Flight Recorder schema (spec §14). Idempotent: every statement can be rerun.
-- One table per row of the §14 table. Row keys are the spec's keys, so a retried write is a no-op
-- (INSERT ... ON CONFLICT DO NOTHING in the Writer). Times are timestamptz, UTC.
--
-- Append-only by grants (10-roles.sql), not by convention: the operator role (nexus_writer) may
-- INSERT everywhere it records, and UPDATE only the columns that complete a row written earlier:
--   actions        request_at, audit_id, http_status, result   (the write-ahead completion)
--   incidents      terminal_phase, reason
-- Nothing is ever deleted by an application role.

CREATE TABLE IF NOT EXISTS runs (
    run_id            text PRIMARY KEY,
    scenario          text NOT NULL,
    reasoner          text NOT NULL,
    level             smallint NOT NULL CHECK (level BETWEEN 0 AND 3),
    repetition        integer NOT NULL CHECK (repetition >= 1),
    git_commit        text NOT NULL,
    ground_truth_hash text NOT NULL,
    config_snapshot   jsonb NOT NULL,
    advisory_checks   boolean NOT NULL,
    started_at        timestamptz NOT NULL,
    ended_at          timestamptz,
    discarded         boolean NOT NULL DEFAULT false,
    discard_reason    text,
    CHECK (NOT discarded OR discard_reason IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_uid   text PRIMARY KEY,
    run_id         text REFERENCES runs (run_id),   -- NULL outside a measured run
    target         text NOT NULL,                    -- namespace/name, as spec.target
    source         text NOT NULL,
    detected_at    timestamptz NOT NULL,
    terminal_phase text,
    reason         text,
    CHECK ((terminal_phase IS NULL) = (reason IS NULL))
);
CREATE INDEX IF NOT EXISTS incidents_target_detected ON incidents (target, detected_at);
CREATE INDEX IF NOT EXISTS incidents_run ON incidents (run_id);

CREATE TABLE IF NOT EXISTS transitions (
    incident_uid text NOT NULL REFERENCES incidents (incident_uid),
    seq          integer NOT NULL CHECK (seq >= 0),
    from_phase   text,                               -- NULL for the first transition
    to_phase     text NOT NULL,
    at           timestamptz NOT NULL,
    stage        text NOT NULL,                      -- the owning stage
    reason       text,
    PRIMARY KEY (incident_uid, seq)
);

CREATE TABLE IF NOT EXISTS evidence_bundles (
    bundle_hash      text PRIMARY KEY,
    incident_uid     text NOT NULL REFERENCES incidents (incident_uid),
    schema_version   text NOT NULL,
    content          jsonb NOT NULL,                 -- redacted
    redaction_counts jsonb NOT NULL,
    collected_at     timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_bundles_incident ON evidence_bundles (incident_uid);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_hash      text PRIMARY KEY,
    incident_uid       text NOT NULL REFERENCES incidents (incident_uid),
    reasoner_mode      text NOT NULL,
    model_id           text,
    prompt_version     text,
    index_hash         text,
    retrieved_chunk_ids text[] NOT NULL DEFAULT '{}',
    raw_output         text,
    parsed_proposal    jsonb,
    schema_valid       boolean NOT NULL,
    latency_ms         integer,
    fallback_used      boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS proposals_incident ON proposals (incident_uid);

CREATE TABLE IF NOT EXISTS gate_decisions (
    incident_uid text NOT NULL REFERENCES incidents (incident_uid),
    stage        smallint NOT NULL CHECK (stage BETWEEN 1 AND 11),   -- §9 stage reference
    verdict      text NOT NULL CHECK (verdict IN ('pass', 'fail')),
    detail       jsonb,
    at           timestamptz NOT NULL,
    PRIMARY KEY (incident_uid, stage)
);

CREATE TABLE IF NOT EXISTS actions (
    idempotency_key text PRIMARY KEY,
    incident_uid    text NOT NULL REFERENCES incidents (incident_uid),
    action_id       text NOT NULL,
    parameters      jsonb NOT NULL,
    previous_state  jsonb,
    intent_at       timestamptz NOT NULL,            -- committed BEFORE the Kubernetes request
    request_at      timestamptz,                     -- NULL until the request was sent
    audit_id        text,                            -- the API server's Audit-Id response header
    http_status     integer,
    result          text,
    action_class    text NOT NULL,
    CHECK (audit_id IS NULL OR request_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS actions_incident ON actions (incident_uid);
CREATE UNIQUE INDEX IF NOT EXISTS actions_audit_id ON actions (audit_id) WHERE audit_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS verifications (
    incident_uid text NOT NULL REFERENCES incidents (incident_uid),
    criteria_id  text NOT NULL,
    observations jsonb NOT NULL,
    result       text NOT NULL,
    at           timestamptz NOT NULL,
    PRIMARY KEY (incident_uid, criteria_id)
);

-- Written by the reconciliation step (the runner), never by the operator.
CREATE TABLE IF NOT EXISTS audit_links (
    audit_id       text PRIMARY KEY,
    matched_action text REFERENCES actions (idempotency_key),   -- NULL: an unexplained request
    verdict        text NOT NULL,
    at             timestamptz NOT NULL
);
