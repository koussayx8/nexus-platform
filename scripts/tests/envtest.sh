#!/usr/bin/env bash
# envtest.sh — a throwaway local Kubernetes API server for the M1b-6 spikes (TASKS.md M1b-6):
# CEL transition rules on the Incident CRD (6a) and the Kopf status-persistence spike (6b).
#
# kube-apiserver and etcd come from the envtest 1.34.1 bundle (kubernetes-sigs/controller-tools,
# SHA-512 pinned below). The cluster runs k3s 1.34.6; the newest envtest build is 1.34.1. The
# server binds to 127.0.0.1, uses RBAC authorization and a static token file for two users, and
# writes an audit log of every request (Metadata level):
#   admin      group system:masters, to install CRDs and RBAC
#   operator   user system:serviceaccount:nexus-system:nexus-operator, so RoleBindings to the
#              nexus-operator ServiceAccount apply to it exactly as in the cluster
# There is no controller-manager and no scheduler: objects are admitted and stored, and nothing
# reconciles them.
#
# It never touches the k3s cluster:
#   - up, status and down refuse to run unless KUBECONFIG=/nonexistent (the offline rule);
#   - it listens on free ports other than 6443, serves a certificate from its own CA, and writes
#     kubeconfigs that name only https://127.0.0.1:<its port> and that CA, so a request that
#     reached k3s would fail TLS;
#   - it calls its own kubectl by absolute path with --kubeconfig, so ~/.kube/config is never read;
#   - `check <kubeconfig>` is the guard its callers (6a, 6b) run before they use a kubeconfig.
# Tokens and keys are generated once per directory, stay 0600 (umask 077), and are never printed.
# Nothing is deleted recursively: each `up` gets a new run directory.
#
# Usage (NEXUS_ENVTEST_DIR must be outside the repository and outside ~/.kube):
#   NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent envtest.sh up
#   NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent envtest.sh status
#   NEXUS_ENVTEST_DIR=<dir> KUBECONFIG=/nonexistent envtest.sh down
#   NEXUS_ENVTEST_DIR=<dir> envtest.sh check <kubeconfig>
# After `up`: <dir>/admin.kubeconfig, <dir>/operator.kubeconfig, <dir>/bin/kubectl, and the audit
# log at <dir>/run/<UTC timestamp>/audit.log.
#
# UNVERIFIED until the first run, which the owner reviews first: the bundle layout (the script
# finds etcd, kube-apiserver and kubectl by name) and that kube-apiserver 1.34.1 still accepts
# --token-auth-file.
#
# Exit codes: 0 ok; 1 a check failed (status: not ready; check: kubeconfig rejected); 2 usage or
# guard error.
set -euo pipefail
umask 077

ENVTEST_VERSION=1.34.1
ENVTEST_SHA512=c5e7c237ae18a8c65d0df1214b864ecd19aa9ff4f1383dbd477c202546d778c0f74efe15750ec38f57d8de26ba17cd62f404446cdd3c975399a5d4589de35cdd
ENVTEST_URL=https://github.com/kubernetes-sigs/controller-tools/releases/download/envtest-v${ENVTEST_VERSION}/envtest-v${ENVTEST_VERSION}-linux-amd64.tar.gz
ADMIN_USER=nexus-envtest-admin
OPERATOR_USER=system:serviceaccount:nexus-system:nexus-operator
K3S_PORT=6443

die() { echo "envtest: $*" >&2; exit 2; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd "$here/../.." && pwd -P)
[[ -n ${NEXUS_ENVTEST_DIR:-} ]] || die "set NEXUS_ENVTEST_DIR to a directory outside the repository"
dir=$(realpath -m -- "$NEXUS_ENVTEST_DIR")
[[ $dir != / ]] || die "NEXUS_ENVTEST_DIR is /"
case $dir/ in
  "$repo"/*) die "NEXUS_ENVTEST_DIR is inside the repository: $dir" ;;
  "$(realpath -m -- ~/.kube)"/*) die "NEXUS_ENVTEST_DIR is inside ~/.kube: $dir" ;;
esac

kubeconfig_env_guard() {
  [[ ${KUBECONFIG-} == /nonexistent ]] || die "run with KUBECONFIG=/nonexistent (offline rule)"
}

# check_kubeconfig <file>: accept only a kubeconfig this script wrote for this directory.
check_kubeconfig() {
  local f=$1 servers ca
  [[ -f $f ]] || { echo "envtest: no kubeconfig at $f" >&2; return 1; }
  [[ $(realpath -- "$f") != "$(realpath -m -- ~/.kube/config)" ]] \
    || { echo "envtest: refusing ~/.kube/config" >&2; return 1; }
  ! grep -q 'insecure-skip-tls-verify' "$f" \
    || { echo "envtest: $f skips TLS verification" >&2; return 1; }
  servers=$(awk '$1 == "server:" {print $2}' "$f")
  [[ $servers =~ ^https://127\.0\.0\.1:([0-9]+)$ && ${BASH_REMATCH[1]} != "$K3S_PORT" ]] \
    || { echo "envtest: $f names server(s) '$servers', not one envtest port on 127.0.0.1" >&2; return 1; }
  ca=$(awk '$1 == "certificate-authority:" {print $2}' "$f")
  [[ $ca == "$dir/pki/ca.crt" ]] || { echo "envtest: $f does not use the envtest CA" >&2; return 1; }
}

# kctl <kubeconfig> <args...>: envtest's own kubectl, guarded.
kctl() {
  local kc=$1; shift
  check_kubeconfig "$kc" || die "guard rejected $kc"
  "$dir/bin/kubectl" --kubeconfig "$kc" "$@"
}

free_port() {
  local p
  p=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  [[ $p != "$K3S_PORT" ]] || die "picked port $K3S_PORT; run up again"
  echo "$p"
}

fetch() {
  if [[ -x $dir/bin/etcd && -x $dir/bin/kube-apiserver && -x $dir/bin/kubectl ]]; then return 0; fi
  mkdir -p "$dir/bin"
  local tgz depth m members
  tgz=$(mktemp "$dir/envtest-XXXXXX.tar.gz")
  curl -fsSL --retry 3 -o "$tgz" "$ENVTEST_URL" || { rm -f "$tgz"; die "download failed: $ENVTEST_URL"; }
  echo "$ENVTEST_SHA512  $tgz" | sha512sum -c --quiet - \
    || { rm -f "$tgz"; die "checksum mismatch; nothing extracted"; }
  mapfile -t members < <(tar -tzf "$tgz" | grep -E '(^|/)(etcd|kube-apiserver|kubectl)$')
  [[ ${#members[@]} == 3 ]] \
    || { rm -f "$tgz"; die "expected etcd, kube-apiserver and kubectl in the bundle, found: ${members[*]:-none}"; }
  depth=$(tr -cd / <<<"${members[0]}" | wc -c)
  for m in "${members[@]}"; do
    [[ $(tr -cd / <<<"$m" | wc -c) == "$depth" ]] || { rm -f "$tgz"; die "bundle members at different depths"; }
  done
  tar -xzf "$tgz" -C "$dir/bin" --strip-components="$depth" "${members[@]}"
  rm -f "$tgz"
}

pki() {
  [[ -s $dir/pki/ca.crt ]] && return 0
  mkdir -p "$dir/pki"
  local p=$dir/pki
  openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj /CN=nexus-envtest-ca \
    -keyout "$p/ca.key" -out "$p/ca.crt" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj /CN=nexus-envtest-apiserver \
    -keyout "$p/server.key" -out "$p/server.csr" 2>/dev/null
  printf 'subjectAltName=IP:127.0.0.1,DNS:localhost\n' > "$p/server.ext"
  openssl x509 -req -days 7 -in "$p/server.csr" -CA "$p/ca.crt" -CAkey "$p/ca.key" -CAcreateserial \
    -extfile "$p/server.ext" -out "$p/server.crt" 2>/dev/null
  openssl genrsa -out "$p/sa.key" 2048 2>/dev/null
  openssl rsa -in "$p/sa.key" -pubout -out "$p/sa.pub" 2>/dev/null
}

# Static token file: token,user,uid,"groups". Written once; tokens never leave this directory.
tokens() {
  [[ -s $dir/tokens.csv ]] && return 0
  printf '%s,%s,uid-admin,"system:masters"\n%s,%s,uid-operator,"system:serviceaccounts,system:serviceaccounts:nexus-system"\n' \
    "$(openssl rand -hex 32)" "$ADMIN_USER" "$(openssl rand -hex 32)" "$OPERATOR_USER" > "$dir/tokens.csv"
}

token_of() { awk -F, -v u="$1" '$2 == u {print $1}' "$dir/tokens.csv"; }

# write_kubeconfig <name> <user> <port>
write_kubeconfig() {
  cat > "$dir/$1.kubeconfig" <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: nexus-envtest
    cluster:
      server: https://127.0.0.1:$3
      certificate-authority: $dir/pki/ca.crt
users:
  - name: $1
    user:
      token: $(token_of "$2")
contexts:
  - name: nexus-envtest-$1
    context:
      cluster: nexus-envtest
      user: $1
current-context: nexus-envtest-$1
EOF
}

up() {
  kubeconfig_env_guard
  [[ ! -e $dir/run/current ]] || die "already up ($(cat "$dir/run/current")); run down first"
  mkdir -p "$dir/run"
  fetch
  pki
  tokens
  local run etcd_port peer_port api_port i
  run=$dir/run/$(date -u +%Y%m%dT%H%M%SZ)
  mkdir -p "$run"
  etcd_port=$(free_port); peer_port=$(free_port); api_port=$(free_port)
  printf 'etcd=%s\npeer=%s\napi=%s\n' "$etcd_port" "$peer_port" "$api_port" > "$run/ports"
  cat > "$run/audit-policy.yaml" <<'EOF'
apiVersion: audit.k8s.io/v1
kind: Policy
omitStages: [RequestReceived]
rules:
  - level: Metadata
EOF
  write_kubeconfig admin "$ADMIN_USER" "$api_port"
  write_kubeconfig operator "$OPERATOR_USER" "$api_port"
  # One process group for both servers: the session leader records its PID (the PGID), starts
  # etcd in the background and execs kube-apiserver, which retries etcd until it answers.
  # shellcheck disable=SC2016  # the inner script expands its own positional arguments
  setsid bash -c '
    run=$1 dir=$2 etcd_port=$3 peer_port=$4 api_port=$5
    echo $$ > "$run/pgid"
    "$dir/bin/etcd" --name envtest --data-dir "$run/etcd" --unsafe-no-fsync \
      --listen-client-urls "http://127.0.0.1:$etcd_port" --advertise-client-urls "http://127.0.0.1:$etcd_port" \
      --listen-peer-urls "http://127.0.0.1:$peer_port" --initial-advertise-peer-urls "http://127.0.0.1:$peer_port" \
      --initial-cluster "envtest=http://127.0.0.1:$peer_port" > "$run/etcd.log" 2>&1 &
    exec "$dir/bin/kube-apiserver" \
      --bind-address 127.0.0.1 --advertise-address 127.0.0.1 --secure-port "$api_port" \
      --etcd-servers "http://127.0.0.1:$etcd_port" \
      --tls-cert-file "$dir/pki/server.crt" --tls-private-key-file "$dir/pki/server.key" \
      --token-auth-file "$dir/tokens.csv" --authorization-mode RBAC \
      --service-account-issuer "https://127.0.0.1:$api_port" \
      --service-account-key-file "$dir/pki/sa.pub" --service-account-signing-key-file "$dir/pki/sa.key" \
      --service-cluster-ip-range 10.96.0.0/24 \
      --audit-policy-file "$run/audit-policy.yaml" --audit-log-path "$run/audit.log" \
      --cert-dir "$run/certs" > "$run/apiserver.log" 2>&1
  ' envtest "$run" "$dir" "$etcd_port" "$peer_port" "$api_port" < /dev/null > /dev/null 2>&1 &
  echo "$run" > "$dir/run/current"
  for i in $(seq 1 60); do
    if kctl "$dir/admin.kubeconfig" get --raw /readyz > /dev/null 2>&1; then
      echo "envtest: ready after ${i}s: $(kctl "$dir/admin.kubeconfig" version -o json | jq -r .serverVersion.gitVersion) at https://127.0.0.1:$api_port"
      echo "envtest: kubeconfigs $dir/admin.kubeconfig and $dir/operator.kubeconfig; audit log $run/audit.log"
      return 0
    fi
    sleep 1
  done
  echo "envtest: not ready after 60 s; logs in $run" >&2
  down || true
  exit 1
}

status() {
  kubeconfig_env_guard
  [[ -e $dir/run/current ]] || { echo "envtest: not up"; return 1; }
  if kctl "$dir/admin.kubeconfig" get --raw /readyz > /dev/null 2>&1; then
    echo "envtest: ready ($(cat "$dir/run/current"))"
  else
    echo "envtest: not ready ($(cat "$dir/run/current"))"; return 1
  fi
}

down() {
  kubeconfig_env_guard
  [[ -e $dir/run/current ]] || { echo "envtest: not up"; return 0; }
  local run pgid i p
  run=$(cat "$dir/run/current")
  pgid=$(cat "$run/pgid" 2>/dev/null || true)
  if [[ $pgid =~ ^[0-9]+$ ]] && kill -0 -- "-$pgid" 2>/dev/null; then
    kill -TERM -- "-$pgid" 2>/dev/null || true
    for i in $(seq 1 10); do kill -0 -- "-$pgid" 2>/dev/null || break; sleep 1; done
    if kill -0 -- "-$pgid" 2>/dev/null; then
      kill -KILL -- "-$pgid" 2>/dev/null || true; sleep 1
      echo "envtest: process group $pgid ignored SIGTERM for ${i}s; sent SIGKILL"
    fi
  fi
  while IFS="=" read -r _ p; do
    if ss -ltn "( sport = :$p )" | tail -n +2 | grep -q .; then
      echo "envtest: port $p is still bound" >&2; return 1
    fi
  done < "$run/ports"
  rm -f "$dir/run/current"
  echo "envtest: down; run directory kept: $run"
}

case ${1:-} in
  up) up ;;
  status) status ;;
  down) down ;;
  check)
    [[ -n ${2:-} ]] || die "usage: envtest.sh check <kubeconfig>"
    check_kubeconfig "$2"
    echo "envtest: $2 is a kubeconfig for this envtest directory" ;;
  *) die "usage: envtest.sh up|status|down|check <kubeconfig>" ;;
esac
