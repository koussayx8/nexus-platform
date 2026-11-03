---
id: rb-image-pull
title: Image pull failure
version: 1
fault_classes: [image_pull]
signals: [KubePodNotReady, ErrImagePull, ImagePullBackOff]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment, restart_workload]
updated: 2026-11-03
---
## Symptoms
A container cannot start because its image cannot be pulled. The container waits with reason ErrImagePull or ImagePullBackOff. Events show Failed pulling image, manifest unknown, unauthorized or a registry timeout. The pod is not Ready.

## Diagnosis
Read the waiting reason and the event message. A wrong tag, a missing digest, a registry outage or a missing pull credential cause it. None of these is fixed by restarting the pod, which retries the same pull.

## Decision
A registry, tag or credential problem cannot be repaired by the operator. Escalate with investigate and name the image reference from the events.

## Action
escalate with recommendation kind investigate. No cluster mutation is requested.

## Escalation
Escalate to the platform owner. If the failing image came from a recent commit, mention that commit in the recommendation.
