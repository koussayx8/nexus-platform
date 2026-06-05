#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing Kyverno on k3s ==="

kubectl create namespace kyverno --dry-run=client -o yaml | kubectl apply -f -

helm repo add kyverno https://kyverno.github.io/kyverno/ 2>/dev/null || true
helm repo update

helm upgrade --install kyverno kyverno/kyverno \
  --namespace kyverno \
  --set replicaCount=1 \
  --wait --timeout 120s

echo "Kyverno installed. Verifying..."
kubectl -n kyverno get pods
