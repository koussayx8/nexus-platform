#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing ArgoCD on k3s ==="

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "Waiting for ArgoCD to be ready..."
kubectl -n argocd rollout status deploy/argocd-server --timeout=120s

echo "ArgoCD installed. Access via:"
echo "  kubectl port-forward svc/argocd-server -n argocd 8080:443"
echo "  Default password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
