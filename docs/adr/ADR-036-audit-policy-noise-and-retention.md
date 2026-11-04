# ADR-036: Audit policy — Incident reads and routine noise at None; 30 backups

**Status:** Proposed (PR to `dev`; applied at a planned k3s restart, owner)
**Date:** 2026-11-04
**Spec:** §14 (audit policy rules 1-4), §14 reconciliation; ADR-019 (policy and rotation), ADR-034
**Supersedes in part:** ADR-019's policy text and `audit-log-maxbackup=10`

## Context

Measured on the live cluster on 2026-11-04, read-only, over the two full hours 13:00-15:00Z after
promotion 2, from `/var/log/nexus-audit/` (bytes of the JSON lines; 4 Incidents, all terminal, no run):

| Source (any verb) | MB/h | Share |
|---|---|---|
| `nexus-operator` `list incidents` (about 18 lists per minute, logged at RequestResponse by rule 1) | 10.15 | 31% |
| Kyverno `create selfsubjectaccessreviews` | 9.77 | 30% |
| `update leases` (Kyverno, kubelet, apiserver) | 9.87 | 30% |
| Everything else (ArgoCD `patch applications`, webhook configurations, metrics-server) | 2.59 | 8% |
| **Total** | **32.37** | |

At 100 MiB × (10 backups + the active file) = 1,153,433,600 B that is **35.6 h of uptime**. The
operator's list is the part that grows: it writes every Incident into the log on every list, about
2.4 KB per Incident per list, so each Incident adds about 2.4 MB/h and ten Incidents bring the
retention near 22 h. The runs of M2 and M4 create many Incidents.

None of the three sources is evidence in the sense of §14. The reconciliation (`audit-join.py`)
reads the operator's `patch`, `update` and `create` events on `deployments/scale` and `pods/eviction`
and joins them to the Flight Recorder by `Audit-Id`; reads of Incidents, lease renewals and
self-permission checks play no part in it.

## Decision

The policy, with the rule order explicit (the first matching rule wins):

| # | Who and what | Level | Against §14 |
|---|---|---|---|
| 1 | `nexus-operator` `get`, `list`, `watch` on `incidents`, `incidents/status` | **None** (new, first) | A read: rule 4 already puts reads at None. It must come before rule 2, or rule 2 would match it. |
| 2 | `nexus-operator` on `deployments/scale`, `pods/eviction`, `incidents`, `incidents/status` | RequestResponse | Rule 1, unchanged for every write, and for `get` on the two subresources |
| 3 | Group `nexus-approvers` on `incidents` | RequestResponse | Rule 2, unchanged |
| 4a | `update`, `patch` on `coordination.k8s.io` `leases`, any identity | **None** (new) | Rule 3 says "any write". See below. |
| 4b | `create` on `authorization.k8s.io` `selfsubjectaccessreviews`, `selfsubjectrulesreviews`, any identity | **None** (new) | Same |
| 5 | Any other write: `create`, `update`, `patch`, `delete`, `deletecollection` | Metadata | Rule 3, unchanged |
| 6 | Everything else | None | Rule 4, unchanged |

`audit-log-maxbackup` goes from 10 to **30**; `maxage=30` and `maxsize=100` stay.

### Why each change is consistent with §14

- **Rule 1 (operator reads, None).** §14 rule 1 is about what the operator *requested of the
  enforcement plane*: Scale and Eviction, and the Incident writes. A read of an Incident changes
  nothing and is not an action, so rule 4 ("reads, watches, health checks: None") is the right
  level for it. Every operator write, denied or admitted, stays at RequestResponse, so the request
  body and Kyverno's denial message are still captured, which is what attribution needs.
- **Rule 4a (leases).** Rule 3 exists so that every *change to the cluster* by the Cluster Admin,
  the runner or ArgoCD is on record. A lease renewal is a heartbeat of a controller, not a change
  anyone requested; it carries no decision of enforcement. `create` and `delete` of a lease stay at
  Metadata (rule 5), so a lease that appears or is removed is still recorded.
- **Rule 4b (self-permission checks).** `create` is the verb of the request, but a
  `SelfSubjectAccessReview` is not stored; it asks a question about the caller's own rights.
  `subjectaccessreviews` (a question about someone else) stay at Metadata.
- **Reconciliation is unaffected.** Its inputs are the operator's mutating events on the two
  subresources, unchanged at RequestResponse (`scripts/tests/audit-policy.sh` asserts it).

### The numbers

Same two hours, same method, with the policy applied to the recorded events:

| | MB/h | Retention, 10 backups (1,153,433,600 B) | Retention, 30 backups (3,250,585,600 B) |
|---|---|---|---|
| Before | 32.37 | 35.6 h | 100.4 h |
| After (idle, 4 Incidents) | **2.59** | **445 h (18.5 d)** | **1,255 h (52 d), capped by `maxage=30` to 30 d** |
| After, at 10 times the idle rate (a run) | 25.9 | 44.5 h | 125 h (5.2 d) |

The remaining 2.59 MB/h is ArgoCD's `patch applications` (0.83), the Kyverno webhook-configuration
updates (1.23) and a few small writes. After the change the rate no longer grows with the number of
Incidents; it grows with the operator's and the experimenter's *writes*, which are the evidence.
G5 and M4's reconciliation still copy the files soon after each run (the plan's rule).

Disk: 31 files × 100 MiB ≈ 3.1 GiB, on a volume with 933 GiB free.

## Consequences

- The operator's reads of Incidents, lease renewals and `can-i` checks are no longer in the audit
  log. An investigation of "who listed what" for those is not possible after the change. A read of
  anything else is unchanged (it was already None).
- Rule order is now load-bearing. `scripts/tests/audit-policy.sh` (in `repo-checks`) extracts the
  policy from `scripts/bootstrap.sh` and asserts the order, the levels of the operator's and the
  approvers' writes, and that every other write stays at Metadata. `bootstrap.sh` carries the same
  policy, so a rebuild gets it.
- The live policy and `config.yaml` change only at a restart of k3s (the API server reads the policy
  at start). Until then the cluster runs ADR-019's policy and `maxbackup=10`.
- The change touches no RBAC, no Kyverno policy and no manifest that ArgoCD syncs.

### Spec v1.1 note

§14 "Audit policy" becomes the seven-rule table above (rules 1-4 of v1.0 map to 2, 3, 5 and 6; the
new rules are the operator-read exemption first and the two noise rules before the write rule), and
the retention row says 30 backups. The text "RequestResponse on rule 1 captures Kyverno's denial
messages" stays true.

## Applying it (owner, at a planned k3s restart; sudo)

1. Back up `/etc/rancher/k3s/audit-policy.yaml` and `/etc/rancher/k3s/config.yaml`.
2. Extract the policy from `scripts/bootstrap.sh` on `main` (the sed command in
   `scripts/tests/audit-policy.sh`), `diff` it against the live file, install it, and change
   `audit-log-maxbackup` to 30 in `config.yaml`.
3. `sudo systemctl restart k3s`; wait for the node and every Application.
4. Verify: a dry-run Scale as the operator whose `Audit-Id` is found in the log with its request
   body; the M7 audit probe; `verify-state.sh` 15/15; and, after a few minutes, no operator
   `list incidents`, no lease `update` and no `selfsubjectaccessreviews` event since the restart.

Rollback: put the two backups back and restart.
