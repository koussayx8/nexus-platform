---
id: rb-cpu-saturation
title: CPU saturation under sustained load
version: 1
fault_classes: [cpu_saturation]
signals: [NexusCpuAnomaly, NexusLatencyAnomaly, throttling]
allowed_actions: [scale_deployment, escalate]
forbidden_actions: [delete_deployment, restart_workload]
updated: 2026-11-03
---
## Symptoms
Sustained load saturates the CPU of every replica. NexusCpuAnomaly fires, often with NexusLatencyAnomaly: CPU utilisation and p95 latency are both far above baseline, request rate is high, CPU throttling is visible, all pods are Ready and none restarts.

## Diagnosis
Check that CPU and latency are both anomalous and that the request rate is high. Check that all replicas are Ready and that no rollout is in progress. The load exceeds the capacity of the current replica count, so more replicas share it. A restart would drop capacity while the load stays.

## Decision
If the CPU and p95 latency Z-scores are both above 3 and all replicas are Ready, scale the Deployment up by one replica. Never exceed the replica ceiling of the Action Catalogue.

## Action
scale_deployment with the replica count raised by one. Compensation is scaling back, itself a gated request.

## Escalation
Escalate with recommendation kind resource_limit_change when the replica ceiling is reached or when the load stays saturated after scaling. Escalate with investigate when CPU is not anomalous.
