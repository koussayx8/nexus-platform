#!/usr/bin/env bash
# dependency-db init (ADR-020, change 13). docker-entrypoint.sh executes this once, on the
# socket-only temporary server, only when PGDATA is empty.
# Passwords come from the environment through psql \getenv: never in argv, never echoed.
# No `set -x`. Any failure aborts init before the marker, so the pod never goes Ready (change 20).
set -Eeuo pipefail

: "${APP_DEV_PASSWORD:?APP_DEV_PASSWORD is not set}"
: "${APP_PROD_PASSWORD:?APP_PROD_PASSWORD is not set}"

psql -v ON_ERROR_STOP=1 --no-psqlrc --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
\getenv pw_dev APP_DEV_PASSWORD
\getenv pw_prod APP_PROD_PASSWORD

CREATE TABLE items (
    id   integer PRIMARY KEY,
    name text    NOT NULL
);
INSERT INTO items (id, name)
SELECT g, 'item-' || g FROM generate_series(1, 20) AS g;

-- 35 = 7 pods x 5 slots per environment (change 11).
CREATE ROLE app_dev  LOGIN CONNECTION LIMIT 35 PASSWORD :'pw_dev';
CREATE ROLE app_prod LOGIN CONNECTION LIMIT 35 PASSWORD :'pw_prod';
GRANT SELECT ON items TO app_dev, app_prod;
SQL

touch /var/lib/postgresql/data/.nexus-init-done
