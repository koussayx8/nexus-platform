#!/usr/bin/env bash
# fixtures.sh up|down - the objects the containment matrix needs (spec section 20). LIVE ONLY.
#
# MUTATES THE CLUSTER (creates and deletes Incidents and one scratch namespace): the owner runs
# it, as Cluster Admin, after approving it in chat. Nothing here is applied by ArgoCD.
#
#   up    the scratch namespace `nexus-containment-unlabelled` (no autonomy label) with a Deployment
#         `probe`, and the Incident fixtures of matrix.yaml:
#           cont-dev-l3, cont-dev-l2-none, cont-prod-l1, cont-data-l0     unapproved
#           cont-dev-l2-approved-scale, cont-dev-l2-approved-restart      approved (K3 pins)
#   down  deletes them.
#
# Incidents are created the way the system creates them, so K6 and K5 are exercised: human Incidents
# by an Approver (impersonated), status by the operator (status subresource), approvals by the
# Approver with the proposal's hash. Approvals expire in 10 minutes (K5 allows at most 15): run `up`
# again before each Kill Switch pass.
#
# The nexus-dev and nexus-prod levels (3, 2, 1) are NOT set here: they are Git commits on
# experiment/dev-state (and main for nexus-prod), per TASKS.md, and ArgoCD would revert a label.
set -euo pipefail

NS=nexus-system
UNLAB=nexus-containment-unlabelled
APPROVER=(--as=approver-1 --as-group=nexus-approvers)
OPERATOR=(--as=system:serviceaccount:nexus-system:nexus-operator)
HASH=sha256:ab12

incident() {  # incident <name> <target-namespace> <action> <replicas|none>
  local name=$1 tns=$2 action=$3 replicas=$4 params='{}'
  [ "$replicas" = none ] || params="{\"replicas\":$replicas}"
  kubectl create -n "$NS" "${APPROVER[@]}" -f - <<EOF
apiVersion: nexus.io/v1alpha1
kind: Incident
metadata:
  name: $name
spec:
  source: {type: human}
  target: {namespace: $tns, kind: Deployment, name: sample-api}
  detectedAt: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
  kubectl patch incidents.nexus.io "$name" -n "$NS" "${OPERATOR[@]}" --subresource=status --type=merge -p \
    "{\"status\":{\"phase\":\"AwaitingApproval\",\"autonomyLevel\":2,\"proposal\":{\"hash\":\"$HASH\",\"actionId\":\"$action\",\"parameters\":$params}}}"
}

approve() {  # approve <name> <action> <replicas|none>
  local name=$1 action=$2 replicas=$3 params='{}'
  [ "$replicas" = none ] || params="{\"replicas\":$replicas}"
  local expires
  expires=$(date -u -d '+10 minutes' +%Y-%m-%dT%H:%M:%SZ)
  kubectl patch incidents.nexus.io "$name" -n "$NS" "${APPROVER[@]}" --type=merge -p \
    "{\"spec\":{\"approval\":{\"decision\":\"approved\",\"actionId\":\"$action\",\"parameters\":$params,\"proposalHash\":\"$HASH\",\"expiresAt\":\"$expires\"}}}"
}

up() {
  kubectl create namespace "$UNLAB"
  kubectl create deployment probe -n "$UNLAB" --image=registry.k8s.io/pause:3.10 --replicas=1
  incident cont-dev-l3 nexus-dev scale_deployment 3
  incident cont-dev-l2-none nexus-dev scale_deployment 3
  incident cont-prod-l1 nexus-prod scale_deployment 3
  incident cont-data-l0 nexus-data scale_deployment 3
  incident cont-dev-l2-approved-scale nexus-dev scale_deployment 3
  incident cont-dev-l2-approved-restart nexus-dev restart_workload none
  approve cont-dev-l2-approved-scale scale_deployment 3
  approve cont-dev-l2-approved-restart restart_workload none
}

down() {
  for i in cont-dev-l3 cont-dev-l2-none cont-prod-l1 cont-data-l0 cont-dev-l2-approved-scale cont-dev-l2-approved-restart; do
    kubectl delete incidents.nexus.io "$i" -n "$NS" --ignore-not-found
  done
  kubectl delete namespace "$UNLAB" --ignore-not-found
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *) echo "usage: fixtures.sh up|down" >&2; exit 64 ;;
esac
