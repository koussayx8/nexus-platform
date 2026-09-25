# ADR-008: k3s over Kind for Local Development

## Status: Accepted

## Context
Need a local Kubernetes cluster for development and testing.
No credit card available for Oracle Cloud. DigitalOcean credit pending.

## Decision
Use k3s natively in WSL2 Ubuntu 24.04 as the primary cluster.
Use DigitalOcean DOKS ($200 student credit) as production/demo cluster when available.

## Rationale
- k3s: 300-400MB RAM vs Kind: 1.5-2GB RAM
- k3s is persistent across WSL restarts — state survives
- k3s runs natively in WSL2 with systemd (Windows 11)
- No Docker Desktop dependency for cluster operation
- DigitalOcean $200 credit covers 2-3 months of DOKS

## Tradeoff
Single-node local cluster — no true multi-node failover testing locally.
Mitigated by DigitalOcean multi-node DOKS for production demos.
