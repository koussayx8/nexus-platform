#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== NEXUS Backstage Dev Startup ==="
echo ""

# Auto-source nvm if node is not in PATH
if ! command -v node &> /dev/null; then
  export NVM_DIR="$HOME/.nvm"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    source "$NVM_DIR/nvm.sh"
    nvm use 20 &>/dev/null || nvm use default &>/dev/null
  fi
fi

# Check Node version
NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 20 ]; then
  echo "ERROR: Node 20+ required. Current: $(node --version)"
  echo "Run: nvm use 20"
  exit 1
fi
echo "Node version: $(node --version) ✓"

# Install dependencies (postinstall will stub isolated-vm)
echo "Installing dependencies..."
yarn install

# Verify stub
if grep -q "module.exports = {}" node_modules/isolated-vm/isolated-vm.js; then
  echo "isolated-vm stub: active ✓"
else
  echo "Applying isolated-vm stub..."
  node scripts/stub-isolated-vm.js
fi

# Kill any existing processes on these ports
fuser -k 3000/tcp 2>/dev/null || true
fuser -k 7007/tcp 2>/dev/null || true
sleep 2

echo ""
echo "Starting backend on :7007..."
yarn workspace backend start &
BACKEND_PID=$!

echo "Waiting for backend to initialize..."
sleep 20

echo "Starting frontend on :3000..."
yarn workspace app start &
FRONTEND_PID=$!

echo ""
echo "=== NEXUS Backstage Ready ==="
echo "  Open: http://localhost:3000"
echo "  Enter as Guest to access catalog"
echo ""
echo "  To enable ArgoCD plugin:"
echo "  kubectl port-forward svc/argocd-server -n argocd 8080:443"
echo ""
echo "Press Ctrl+C to stop"

trap "echo 'Stopping...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
