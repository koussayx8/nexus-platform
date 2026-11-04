#!/usr/bin/env bash
# flight-recorder-secrets.sh — create the Flight Recorder DB Secrets (spec §14; ADR-031).
#
# A mutation script, like dependency-db-secrets.sh and bootstrap.sh, which calls it after step f2:
# it does not source scripts/lib/readonly.sh. Also runnable standalone by the owner. No sudo.
#
#   ~/.nexus/flight-recorder-super   -> nexus-system/flight-recorder-db      key postgres-password
#   ~/.nexus/flight-recorder-writer  -> nexus-system/flight-recorder-db      key writer-password
#                                       nexus-system/flight-recorder-writer  key password  (operator mount)
#   ~/.nexus/flight-recorder-runner  -> nexus-system/flight-recorder-db      key runner-password
#   ~/.nexus/flight-recorder-reader  -> nexus-system/flight-recorder-db      key reader-password
#                                       monitoring/flight-recorder-reader    key password  (Grafana)
#
# The operator pod mounts only flight-recorder-writer and Grafana only flight-recorder-reader, so
# neither identity can read the other credentials, and the operator needs no Secrets RBAC (§11).
# The runner reads ~/.nexus/flight-recorder-runner on the host.
#
# The files follow the ADR-019/ADR-020 generate-or-reuse pattern: umask 077, no trailing newline,
# reused when present. Each Secret is created only if absent (kubectl create --from-file, never
# apply), so no value is ever in argv, in this script's output or in a diff. The output names
# Secrets only, "exists" or "created" per Secret.
#
# If any file is missing while any of the four Secrets exists, the script stops: regenerating one
# side silently would split the DB's credentials from its clients'. To rotate, delete all four
# Secrets and the four files, and the database's volume, then rerun (roles are set at first init).
#
# Usage: scripts/flight-recorder-secrets.sh
# Env: NEXUS_WAIT_TIMEOUT_DEFAULT (default 600) bounds the wait for each namespace to exist.
# Exit codes: 0 success; 1 a step failed or refused to proceed; 2 argument error.

set -Eeuo pipefail

(( $# == 0 )) || { echo "flight-recorder-secrets: takes no arguments" >&2; exit 2; }

NEXUS_DIR=$HOME/.nexus
TIMEOUT=${NEXUS_WAIT_TIMEOUT_DEFAULT:-600}
FILES=(flight-recorder-super flight-recorder-writer flight-recorder-runner flight-recorder-reader)
SECRETS=(nexus-system/flight-recorder-db nexus-system/flight-recorder-writer monitoring/flight-recorder-reader)

fatal() { echo "flight-recorder-secrets: FATAL — $*" >&2; exit 1; }
trap 'echo "flight-recorder-secrets: FATAL — unchecked command failed at line $LINENO (exit $?)" >&2; exit 1' ERR

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

for ns in nexus-system monitoring; do
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
  fatal "the directory ~/.nexus is missing ${missing[*]} while at least one of the Secrets exists; delete all Secrets and the files to rotate"
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
    nexus-system/flight-recorder-db)
      create_secret "$s" \
        --from-file=postgres-password="$NEXUS_DIR/flight-recorder-super" \
        --from-file=writer-password="$NEXUS_DIR/flight-recorder-writer" \
        --from-file=runner-password="$NEXUS_DIR/flight-recorder-runner" \
        --from-file=reader-password="$NEXUS_DIR/flight-recorder-reader" ;;
    nexus-system/flight-recorder-writer)
      create_secret "$s" --from-file=password="$NEXUS_DIR/flight-recorder-writer" ;;
    monitoring/flight-recorder-reader)
      create_secret "$s" --from-file=password="$NEXUS_DIR/flight-recorder-reader" ;;
  esac
  echo "created: $s"
done
