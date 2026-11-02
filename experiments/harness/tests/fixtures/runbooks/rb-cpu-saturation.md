---
id: rb-cpu-saturation
title: CPU saturation
version: 1
fault_classes: [cpu_saturation]
signals: [NexusCpuAnomaly]
allowed_actions: [scale_deployment, escalate]
forbidden_actions: [delete_deployment]
updated: 2026-11-02
---
## Symptoms
CPU saturation under sustained load. NexusCpuAnomaly fires, CPU and latency both high, pods Ready, throttling.

## Diagnosis
Compare the signals with the symptoms above before choosing.

## Decision
If CPU and latency are both high and all replicas are Ready, scale the deployment up by one.

## Action
scale_deployment by one replica.

## Escalation
Escalate with investigate when the signals do not match.
