---
id: rb-bad-rollout
title: Rollout fails after a Git change
version: 2
fault_classes: [bad_rollout]
signals: [NexusRolloutStuck, NexusCrashLooping, ProgressDeadlineExceeded]
allowed_actions: [escalate]
forbidden_actions: [delete_deployment, restart_workload, scale_deployment]
source: https://runbooks.prometheus-operator.dev/runbooks/kubernetes/kubepodcrashlooping/ (KubePodCrashLooping; NEXUS wording, adapted to the GitOps boundary)
updated: 2026-11-15
---
## Symptoms
A Git commit changes the image or the configuration, and the new revision never becomes available. NexusRolloutStuck fires: the rollout is in progress and unavailable replicas stay above zero for minutes. Pods of the new ReplicaSet fail the readiness probe, or exit and are started again and again until they sit in CrashLoopBackOff. The rolling update cannot finish, and pods of the previous revision still serve.

## Diagnosis
Read the current and previous revisions in the bundle, their image digests, the last Git commit, and the restart counts and events of the failing pods. When the failures start with the new revision, the change is the cause, not load: a broken image, a wrong command or argument, a missing ConfigMap, Secret or volume, a probe on the wrong port or with a timeout that is too short, or limits the new version does not fit in. The desired state lives in Git, so the fault is fixed in Git.

## Decision
Never roll back in place and never restart or scale a rollout that is failing: a restart starts the same broken revision again. The remedy is a Git revert of the offending commit, which a human approves. Escalate with git_revert naming the previous image digest and the commit to revert.

## Action
escalate with recommendation kind git_revert. The recommendation names the previous digest and the commit. No cluster mutation is requested.

## Escalation
Escalate with recommendation kind investigate when no previous revision is known or when the failing pods predate the last commit.
