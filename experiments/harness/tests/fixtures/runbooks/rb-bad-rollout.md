---
id: rb-bad-rollout
title: Bad rollout
version: 1
fault_classes: [bad_rollout]
signals: [NexusRolloutStuck]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment]
updated: 2026-11-02
---
## Symptoms
Rollout fails after a Git change. NexusRolloutStuck fires, new revision unavailable, readiness probe failure, rollout in progress.

## Diagnosis
Compare the signals with the symptoms above before choosing.

## Decision
Never roll back in place. Escalate with git_revert naming the previous digest.

## Action
escalate with git_revert.

## Escalation
Escalate with investigate when the signals do not match.
