#!/bin/bash
# Start Backstage with ArgoCD integration
# Usage: ./start-with-argocd.sh

echo "Starting ArgoCD port-forward..."
kubectl port-forward svc/argocd-server -n argocd 8080:443 &
ARGOCD_PID=$!
sleep 3

export ARGOCD_BASE_URL="https://localhost:8080"
export ARGOCD_AUTH_TOKEN=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)

echo "ArgoCD token acquired"
echo "Starting Backstage backend..."
cd "$(dirname "$0")"
yarn workspace backend start &
BACKEND_PID=$!
sleep 15

echo "Starting Backstage frontend..."
yarn workspace app start &
FRONTEND_PID=$!

echo ""
echo "NEXUS Backstage running:"
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:7007"
echo "  ArgoCD:   https://localhost:8080"
echo ""
echo "Press Ctrl+C to stop all services"

trap "kill $ARGOCD_PID $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
