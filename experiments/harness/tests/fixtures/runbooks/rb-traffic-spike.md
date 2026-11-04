---
id: rb-traffic-spike
title: Traffic spike
version: 1
fault_classes: [traffic_spike]
signals: [NexusTrafficAnomaly]
allowed_actions: [no_action]
forbidden_actions: [delete_deployment]
updated: 2026-11-02
---
## Symptoms
Healthy increase in traffic. NexusTrafficAnomaly fires, request rate high, error ratio low, latency normal.

## Diagnosis
Compare the signals with the symptoms above before choosing.

## Decision
If request rate is high with low errors and normal latency, do nothing.

## Action
no_action.

## Escalation
Escalate with investigate when the signals do not match.
