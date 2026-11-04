---
id: rb-disk-pressure
title: Disk pressure
version: 1
fault_classes: [disk_pressure]
signals: [NodeFilesystemSpaceFillingUp, DiskPressure, Evicted]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment, restart_workload, scale_deployment]
updated: 2026-11-03
---
## Symptoms
A node or volume runs out of disk space. The node reports DiskPressure, pods are Evicted for low ephemeral-storage, writes fail with no space left on device, and pods are rescheduled or stay Pending.

## Diagnosis
Check which node reports DiskPressure and which volume is full. Restarting or scaling moves pods to a node with the same problem or adds more writers to a full disk.

## Decision
Freeing disk space is a node operation outside the Action Catalogue. Escalate with investigate and name the node and the full volume.

## Action
escalate with recommendation kind investigate. No cluster mutation is requested.

## Escalation
Escalate to the platform owner as urgent when the node is already evicting pods.
