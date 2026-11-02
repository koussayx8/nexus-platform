---
id: rb-gray-failure
title: Gray failure
version: 1
fault_classes: [gray_failure]
signals: [NexusLatencyAnomaly]
allowed_actions: [restart_workload, escalate]
forbidden_actions: [delete_deployment]
updated: 2026-11-02
---
## Symptoms
Handlers hang while liveness passes. NexusLatencyAnomaly fires, p95 latency high, CPU flat, pods Ready, timeout errors in logs.

## Diagnosis
Compare the signals with the symptoms above before choosing.

## Decision
If latency is high and CPU is flat with all pods Ready and no rollout, restart the workload.

## Action
restart_workload through eviction, one pod at a time.

## Escalation
Escalate with investigate when the signals do not match.
