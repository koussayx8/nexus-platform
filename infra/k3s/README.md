# K3s Local Cluster Setup

Scripts to bootstrap a local Kubernetes cluster on WSL2 for NEXUS development.

## Order
Run these scripts **in order** for a fresh setup:

1. `install-k3s.sh` — Install k3s lightweight Kubernetes
2. `install-argocd.sh` — Install ArgoCD for GitOps
3. `install-kyverno.sh` — Install Kyverno for policy enforcement

## Prerequisites
- WSL2 with Ubuntu 24.04
- `curl`, `helm`, `kubectl` available
- sudo access within WSL
