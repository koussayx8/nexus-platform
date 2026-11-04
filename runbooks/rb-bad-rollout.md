---
id: rb-bad-rollout
title: Rollout fails after a Git change
version: 1
fault_classes: [bad_rollout]
signals: [NexusRolloutStuck, NexusCrashLooping, ProgressDeadlineExceeded]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment, restart_workload, scale_deployment]
updated: 2026-11-03
---
## Symptoms
A new revision introduced by a Git commit does not become available. NexusRolloutStuck fires: unavailable replicas stay above zero for minutes, the rollout is in progress, the new ReplicaSet has pods failing the readiness probe or in CrashLoopBackOff. The previous revision still exists.

## Diagnosis
Compare the current revision with the previous revisions in the bundle and read the last Git commit. Readiness probe failures that start with the new image digest or configuration point at the change, not at load. The desired state lives in Git, so the fault is fixed in Git.

## Decision
Never roll back in place and never restart or scale a rollout that is failing. The remedy is a Git revert of the offending commit, which a human approves. Escalate with git_revert naming the previous image digest and the commit to revert.

## Action
escalate with recommendation kind git_revert. The recommendation names the previous digest and the commit. No cluster mutation is requested.

## Escalation
Escalate with recommendation kind investigate when no previous revision is known or when the failing pods predate the last commit.
