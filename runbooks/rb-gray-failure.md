---
id: rb-gray-failure
title: Gray failure
version: 2
fault_classes: [gray_failure]
signals: [NexusLatencyAnomaly, Unhealthy, timeout]
allowed_actions: [restart_workload, escalate]
forbidden_actions: [delete_deployment, scale_deployment]
updated: 2026-11-09
---
## Symptoms
Request handlers hang while the liveness probe still passes. CPU is flat, every pod stays Ready with zero restarts, no rollout is in progress, and NexusLatencyAnomaly fires. It takes one of two forms. When requests still complete, but slowly, p95 latency is far above baseline and logs show request timeout errors and slow handlers, not crashes. When handlers hang completely, requests pile up in flight and never return: the in-flight request count is far above baseline, the rate of completed requests drops, and p95 latency can stay flat because it only measures the requests that complete.

## Diagnosis
Check that latency is anomalous and CPU is not. Check that all replicas are Ready and that the current revision is the one that was healthy before. A hang that liveness does not catch is a gray failure: the process is alive but its handlers are deadlocked. Scaling does not help because CPU is not the limit.

## Decision
If the p95 latency Z-score is above 3, the CPU Z-score is at most 1, all replicas are Ready and no rollout is in progress, restart the workload. If the in-flight Z-score is above 3 and the request-rate Z-score is below -3, with the same CPU, Ready and rollout conditions, restart the workload too: that is a full hang. Otherwise do not restart.

## Action
restart_workload on the Deployment. Pods are replaced by the Executor through eviction, one at a time. Verification is the restart criterion in the Action Catalogue, not this text.

## Escalation
Escalate with recommendation kind investigate when latency is anomalous but CPU is also high, when a rollout is in progress, or when a restart did not bring latency back to baseline.
