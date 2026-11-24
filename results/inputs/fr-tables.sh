#!/usr/bin/env bash
# fr-tables.sh — the Flight Recorder rows `export-results.py --tables` reads, for one window. The owner runs it.
#   bash fr-tables.sh <since> <until> > tables.json
# One JSON object {incidents, transitions, evidence_bundles, proposals, gate_decisions, actions, verifications}:
# the Incidents detected in [since, until) and every row of those Incidents. Read-only (nexus_reader has SELECT
# only); the one cluster call is a `kubectl exec` into flight-recorder-db-0, as scripts/fr-export.sh does.
# scripts/fr-export.sh covers only actions, proposals and gate_decisions; export-results.py needs all seven.
set -euo pipefail
[[ $# -eq 2 ]] || { echo "usage: $0 <since> <until>" >&2; exit 2; }
since=$1 until=$2
rfc3339='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
for t in "$since" "$until"; do [[ $t =~ $rfc3339 ]] || { echo "not an RFC 3339 timestamp: $t" >&2; exit 2; }; done
uids="SELECT incident_uid FROM incidents WHERE detected_at >= '$since'::timestamptz AND detected_at < '$until'::timestamptz"
agg() { # table, order
  echo "(SELECT coalesce(json_agg(t ORDER BY $2), '[]'::json) FROM $1 t WHERE t.incident_uid IN ($uids))"
}
sql="SELECT json_build_object(
 'incidents', (SELECT coalesce(json_agg(t ORDER BY detected_at), '[]'::json) FROM incidents t WHERE t.incident_uid IN ($uids)),
 'transitions', $(agg transitions 't.incident_uid, t.seq'),
 'evidence_bundles', $(agg evidence_bundles 't.incident_uid'),
 'proposals', $(agg proposals 't.incident_uid, t.proposal_hash'),
 'gate_decisions', $(agg gate_decisions 't.incident_uid, t.stage'),
 'actions', $(agg actions 't.intent_at'),
 'verifications', $(agg verifications 't.incident_uid'))"
exec kubectl --context default -n nexus-system exec flight-recorder-db-0 -c flight-recorder-db -- \
  psql -X -q -A -t -v ON_ERROR_STOP=1 -U nexus_reader -d nexus_recorder -c "$sql"
