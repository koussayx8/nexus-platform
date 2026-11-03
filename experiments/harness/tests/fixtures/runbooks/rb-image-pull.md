---
id: rb-image-pull
title: Image pull failure
version: 1
fault_classes: [image_pull]
signals: [KubePodNotReady]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment]
updated: 2026-11-02
---
## Symptoms
A container is waiting on ErrImagePull or ImagePullBackOff. Events show Failed pull.

## Diagnosis
Compare the signals with the symptoms above before choosing.

## Decision
A registry or tag problem cannot be fixed at runtime.

## Action
escalate with investigate.

## Escalation
Escalate with investigate when the signals do not match.
