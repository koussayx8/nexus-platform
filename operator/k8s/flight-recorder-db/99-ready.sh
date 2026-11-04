#!/usr/bin/env bash
# The readiness marker, last in init order: a half-initialised database never goes Ready.
set -Eeuo pipefail
touch /var/lib/postgresql/data/.nexus-init-done
