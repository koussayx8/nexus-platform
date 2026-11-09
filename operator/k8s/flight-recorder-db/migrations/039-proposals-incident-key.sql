-- ADR-039: key `proposals` by (incident_uid, proposal_hash) instead of proposal_hash alone.
--
-- proposal_hash is the hash of the proposal content only, so two Incidents with an identical
-- proposal shared one key and the second row was dropped (ON CONFLICT DO NOTHING). On 2026-11-07
-- six nexus-prod Incidents lost their row this way; they are recorded as lost, not backfilled
-- (each Incident's status still carries its proposal).
--
-- Not part of the pod's init (the ConfigMap ships 05-schema.sql, 10-roles.sql, 99-ready.sh only):
-- a fresh volume already gets the composite key from 05-schema.sql. The owner runs this once on a
-- volume initialised before ADR-039, as the database owner:
--
--   kubectl --context default -n nexus-system exec -i flight-recorder-db-0 -c flight-recorder-db -- \
--     psql -X -U postgres -d nexus_recorder -v ON_ERROR_STOP=1 -f - \
--     < operator/k8s/flight-recorder-db/migrations/039-proposals-incident-key.sql
--
-- Rollout order (ADR-039, mandatory): run this BEFORE the operator image with the scoped reader
-- and writer is deployed, with no Incident in Diagnosing. Between this migration and that image,
-- the running operator's `ON CONFLICT (proposal_hash)` has no matching unique index and every
-- proposal write fails: those Incidents end Blocked / recorder_unavailable (fail closed, no
-- mutation). Deploy the image right after.
--
-- Idempotent: a no-op when the key is already (incident_uid, proposal_hash), safe to re-run; any
-- other key shape stops with an error and changes nothing. One transaction.

BEGIN;

DO $$
DECLARE
    pk_name text;
    pk_cols text[];
BEGIN
    SELECT c.conname, array_agg(a.attname::text ORDER BY k.ord)
      INTO pk_name, pk_cols
      FROM pg_constraint c
     CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k (attnum, ord)
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
     WHERE c.conrelid = 'proposals'::regclass AND c.contype = 'p'
     GROUP BY c.conname;

    IF pk_cols = ARRAY['incident_uid', 'proposal_hash'] THEN
        RAISE NOTICE 'ADR-039: proposals is already keyed by (incident_uid, proposal_hash); nothing to do';
        RETURN;
    END IF;
    IF pk_cols IS DISTINCT FROM ARRAY['proposal_hash'] THEN
        RAISE EXCEPTION 'ADR-039: unexpected proposals primary key %, not migrating', pk_cols;
    END IF;

    EXECUTE format('ALTER TABLE proposals DROP CONSTRAINT %I', pk_name);
    ALTER TABLE proposals ADD PRIMARY KEY (incident_uid, proposal_hash);
    RAISE NOTICE 'ADR-039: proposals is now keyed by (incident_uid, proposal_hash)';
END
$$;

COMMIT;
