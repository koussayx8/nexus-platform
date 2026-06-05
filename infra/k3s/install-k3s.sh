#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing k3s on WSL2 ==="

export KUBECONFIG=~/.kube/config

curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644

mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config

echo "k3s installed. Verifying..."
kubectl get nodes
