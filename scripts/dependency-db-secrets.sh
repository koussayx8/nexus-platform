#!/usr/bin/env bash
# dependency-db-secrets.sh — create the three Dependency DB Secrets (ADR-020, "Secret contract").
#
# A mutation script, like bootstrap.sh, which calls it after step f: it does not source
# scripts/lib/readonly.sh. Also runnable standalone by the owner. No sudo.
#
#   ~/.nexus/dependency-db-super     -> nexus-data/dependency-db       key postgres-password
#   ~/.nexus/dependency-db-app-dev   -> nexus-data/dependency-db       key app-dev-password
#                                       nexus-dev/dependency-db-app    key password
#   ~/.nexus/dependency-db-app-prod  -> nexus-data/dependency-db       key app-prod-password
#                                       nexus-prod/dependency-db-app   key password
#
# The files follow the ADR-019 generate-or-reuse pattern: umask 077, no trailing newline, reused
# when present. Each Secret is created only if absent (kubectl create --from-file, never apply),
# so no value is ever in argv, in this script's output or in a diff. The output names Secrets
# only, "exists" or "created" per Secret; it is the M1-4 evidence that the three Secrets exist.
#
# Change 5: if any file is missing while any of the three Secrets exists, the script stops.
# Regenerating one side silently would split the DB's credentials from the apps'. To rotate,
# delete all three Secrets and the three files, then rerun.
#
# Usage: scripts/dependency-db-secrets.sh
# Env: NEXUS_WAIT_TIMEOUT_DEFAULT (default 600) bounds the wait for each namespace to exist
#      (nexus-dev appears only once the sample-api-dev Application has synced).
# Exit codes: 0 success; 1 a step failed or refused to proceed; 2 argument error.

set -Eeuo pipefail

(( $# == 0 )) || { echo "dependency-db-secrets: takes no arguments" >&2; exit 2; }

NEXUS_DIR=$HOME/.nexus
TIMEOUT=${NEXUS_WAIT_TIMEOUT_DEFAULT:-600}
FILES=(dependency-db-super dependency-db-app-dev dependency-db-app-prod)
SECRETS=(nexus-data/dependency-db nexus-dev/dependency-db-app nexus-prod/dependency-db-app)

fatal() { echo "dependency-db-secrets: FATAL — $*" >&2; exit 1; }
trap 'echo "dependency-db-secrets: FATAL — unchecked command failed at line $LINENO (exit $?)" >&2; exit 1' ERR

wait_namespace() {   # wait_namespace <namespace>
  local ns=$1 waited=0
  until kubectl get namespace "$ns" >/dev/null 2>&1; do
    (( waited < TIMEOUT )) || fatal "namespace $ns did not appear within ${TIMEOUT}s"
    sleep 5; waited=$((waited + 5))
  done
}

secret_exists() {   # secret_exists <namespace/name>: 0 present, 1 absent, fatal on any API error
  local out
  out=$(kubectl get secret "${1#*/}" -n "${1%%/*}" --ignore-not-found -o name) \
    || fatal "checking whether Secret $1 exists failed"
  [[ -n $out ]]
}

generate_file() {   # generate_file <name under ~/.nexus>
  local f=$NEXUS_DIR/$1
  ( umask 077; python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' > "$f" ) \
    || fatal "generating ~/.nexus/$1 failed"
  [[ -s $f ]] || fatal "the file ~/.nexus/$1 was generated empty"
  echo "generated: ~/.nexus/$1"
}

create_secret() {   # create_secret <namespace/name> <--from-file args...>
  local s=$1
  shift
  kubectl create secret generic "${s#*/}" -n "${s%%/*}" "$@" >/dev/null \
    || fatal "creating Secret $s failed"
}

[[ -d $NEXUS_DIR ]] || install -d -m 0700 "$NEXUS_DIR" || fatal "creating ~/.nexus failed"

for ns in nexus-data nexus-dev nexus-prod; do
  wait_namespace "$ns"
done

declare -A present=()
any_secret=0
for s in "${SECRETS[@]}"; do
  if secret_exists "$s"; then present[$s]=1; any_secret=1; fi
done

missing=()
for f in "${FILES[@]}"; do
  if [[ -f $NEXUS_DIR/$f ]]; then
    [[ -s $NEXUS_DIR/$f ]] || fatal "the file ~/.nexus/$f exists but is empty"
  else
    missing+=("$f")
  fi
done

if (( any_secret && ${#missing[@]} > 0 )); then
  fatal "the directory ~/.nexus is missing ${missing[*]} while at least one of the three Secrets exists; delete all three Secrets and the files to rotate"
fi

for f in "${missing[@]}"; do
  generate_file "$f"
done

for s in "${SECRETS[@]}"; do
  if [[ -n ${present[$s]:-} ]]; then
    echo "exists: $s"
    continue
  fi
  case $s in
    nexus-data/dependency-db)
      create_secret "$s" \
        --from-file=postgres-password="$NEXUS_DIR/dependency-db-super" \
        --from-file=app-dev-password="$NEXUS_DIR/dependency-db-app-dev" \
        --from-file=app-prod-password="$NEXUS_DIR/dependency-db-app-prod" ;;
    nexus-dev/dependency-db-app)
      create_secret "$s" --from-file=password="$NEXUS_DIR/dependency-db-app-dev" ;;
    nexus-prod/dependency-db-app)
      create_secret "$s" --from-file=password="$NEXUS_DIR/dependency-db-app-prod" ;;
  esac
  echo "created: $s"
done
