#!/usr/bin/env bash
# fr-export.sh — export one Flight Recorder table for a time window. The owner runs it.
#
#   bash scripts/fr-export.sh <actions|proposals|gate_decisions> <since> <until> [--jsonl] > out.csv
#
# <since> and <until> are RFC 3339 UTC or with an offset (2026-11-20T06:25:39Z). The window is
# [since, until). CSV with a header line on stdout; --jsonl prints one JSON object per row instead
# (the `actions` input scripts/audit-join.py reads).
#
# UNTESTED UNTIL THE OWNER'S FIRST RUN: the queries are written from
# operator/k8s/flight-recorder-db/05-schema.sql and checked offline against a stub kubectl only
# (scripts/tests/test_fr_export.py). The connection assumes psql inside the pod reaches the server
# through the local socket as nexus_reader without a password (the postgres image's local default).
#
# Read-only: nexus_reader has SELECT only (10-roles.sql). The one cluster call is a `kubectl exec`
# into flight-recorder-db-0 (an ask rule). No password is passed, printed or stored.
#
# Which rows (the column that places a row in the window):
#   actions         intent_at (the operator's intent, committed before the Kubernetes request)
#   gate_decisions  at
#   proposals       the Incident's detected_at (a proposal row has no time of its own), with the
#                   Incident's target and detected_at alongside
set -euo pipefail

usage() { echo "usage: $0 <actions|proposals|gate_decisions> <since> <until> [--jsonl]" >&2; exit 2; }
[[ $# -eq 3 || $# -eq 4 ]] || usage
table=$1 since=$2 until=$3 format=csv
if [[ $# -eq 4 ]]; then
  [[ $4 == --jsonl ]] || usage
  format=jsonl
fi

# The window is interpolated into SQL, so only a strict RFC 3339 timestamp is accepted.
rfc3339='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
for t in "$since" "$until"; do
  [[ $t =~ $rfc3339 ]] || { echo "not an RFC 3339 timestamp: $t" >&2; exit 2; }
done

case $table in
  actions)
    select="SELECT a.* FROM actions a
            WHERE a.intent_at >= '$since'::timestamptz AND a.intent_at < '$until'::timestamptz
            ORDER BY a.intent_at" ;;
  gate_decisions)
    select="SELECT g.* FROM gate_decisions g
            WHERE g.at >= '$since'::timestamptz AND g.at < '$until'::timestamptz
            ORDER BY g.at, g.incident_uid, g.stage" ;;
  proposals)
    select="SELECT p.*, i.target, i.detected_at FROM proposals p JOIN incidents i USING (incident_uid)
            WHERE i.detected_at >= '$since'::timestamptz AND i.detected_at < '$until'::timestamptz
            ORDER BY i.detected_at, p.incident_uid" ;;
  *) echo "unknown table: $table (actions, proposals or gate_decisions)" >&2; exit 2 ;;
esac

# jsonl: unaligned, tuples-only rows of row_to_json (COPY's text format would escape the JSON's
# backslashes a second time), the form scripts/audit-join.py documents. csv: COPY's CSV with a header.
if [[ $format == jsonl ]]; then
  sql="SELECT row_to_json(r) FROM ($select) r"
  out=(-A -t)
else
  sql="COPY ($select) TO STDOUT WITH (FORMAT csv, HEADER)"
  out=()
fi

exec kubectl --context default -n nexus-system exec flight-recorder-db-0 -c flight-recorder-db -- \
  psql -X -q -v ON_ERROR_STOP=1 "${out[@]}" -U nexus_reader -d nexus_recorder -c "$sql"
