#!/usr/bin/env bash
# network-n1-n6.sh - the empirical NetworkPolicy tests of spec §18 (N1-N6). LIVE ONLY: needs a
# cluster with platform/network applied and a running Reasoner. Run after every rebuild.
# Read-only except for `kubectl exec` into two pods (the owner runs it; exec is an ask rule).
#
#   N1 Reasoner -> kubernetes.default.svc:443        must fail
#   N2 Reasoner -> node IP:6443                       must fail
#   N3 Reasoner -> Flight Recorder DB:5432            must fail
#   N4 Reasoner -> hosted model endpoint:443          must succeed   ($MODEL_HOST, default api.groq.com)
#   N5 sample-api pod -> Flight Recorder DB:5432      must fail
#   N6 Reasoner -> ServiceAccount token file          must be absent
#
# The probes need python3 inside both images (the Reasoner and sample-api are Python images).
# Exit 0 only if all six hold. Status: live check pending (no cluster in the authoring session).
set -euo pipefail

MODEL_HOST=${MODEL_HOST:-api.groq.com}
TOKEN=/var/run/secrets/kubernetes.io/serviceaccount/token
fail=0

pod() {  # pod <namespace> <label selector>
  kubectl get pod -n "$1" -l "$2" -o jsonpath='{.items[0].metadata.name}'
}

probe() {  # probe <namespace> <pod> <host> <port>; exit 0 = connected
  kubectl exec -n "$1" "$2" -- python3 -c '
import socket, sys
s = socket.socket(); s.settimeout(5)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(1)
' "$3" "$4"
}

expect() {  # expect <fail|succeed> <label> <command...>
  local want=$1 label=$2; shift 2
  local rc=0
  "$@" >/dev/null 2>&1 || rc=$?
  if { [ "$want" = fail ] && [ "$rc" -ne 0 ]; } || { [ "$want" = succeed ] && [ "$rc" -eq 0 ]; }; then
    echo "ok   $label"
  else
    echo "FAIL $label (wanted $want, exit $rc)"
    fail=1
  fi
}

reasoner=$(pod nexus-reasoner app.kubernetes.io/name=nexus-reasoner)
api=$(pod nexus-dev app.kubernetes.io/name=sample-api)
node_ip=$(kubectl get node -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
frdb_ip=$(kubectl get pod -n nexus-system -l app.kubernetes.io/name=flight-recorder-db -o jsonpath='{.items[0].status.podIP}')

expect fail    "N1 Reasoner -> kubernetes.default.svc:443"   probe nexus-reasoner "$reasoner" kubernetes.default.svc 443
expect fail    "N2 Reasoner -> node IP:6443"                 probe nexus-reasoner "$reasoner" "$node_ip" 6443
expect fail    "N3 Reasoner -> Flight Recorder DB:5432"      probe nexus-reasoner "$reasoner" "$frdb_ip" 5432
expect succeed "N4 Reasoner -> $MODEL_HOST:443"              probe nexus-reasoner "$reasoner" "$MODEL_HOST" 443
expect fail    "N5 sample-api -> Flight Recorder DB:5432"    probe nexus-dev "$api" "$frdb_ip" 5432
expect fail    "N6 ServiceAccount token file present"        kubectl exec -n nexus-reasoner "$reasoner" -- test -e "$TOKEN"

exit "$fail"
