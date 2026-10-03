# ADR-023: The Incident CRD, Its CEL Rules and Kopf's Status-Only Persistence

## Status: Proposed (M1b-6, branch `feat/m1b-6b-kopf-spike`; number tentative, assigned in merge order)

## Context
Spec §8 defines the Incident. §11 freezes the operator's rights on it: `incidents` get, list, watch
and create, and `incidents/status` get, patch and update, in `nexus-system`. Patch on the
Incident's `spec` or metadata is "explicitly denied by omission". §4 follows from that: Kopf in
standalone mode, progress and diff-base in the Incident `status`, no annotations, no finalizers,
and a 5 s cadence for every non-terminal Incident. §27 lists two M1 checks: CEL transition rules
(a rejected spec patch), and Kopf's status-based persistence and standalone mode ("the RBAC design
depends on it").

M1b stages §11 (owner, 2026-09-28). The operator skeleton gets Incidents create and status, the
reads it uses (Incidents, the ConfigMaps `nexus-killswitch` and `nexus-operator-config`, namespaces)
and nothing else. `deployments/scale` and `pods/eviction` wait for M2.

Both checks ran offline on the envtest harness (`scripts/tests/envtest.sh`: kube-apiserver 1.34.1,
RBAC, a static token for the operator's ServiceAccount identity, an audit log of every request).
The live checks follow the M1 exit.

## Decision

### The Incident CRD (M1b-6a)
- `platform/crds/incidents.nexus.io.yaml`: `nexus.io/v1alpha1`, namespaced, short name `inc`, the
  status subresource and the §8 printer columns.
- C1–C4 are `x-kubernetes-validations`. C1 (source, target and detectedAt frozen) and C2
  (`spec.approval` write-once) are transition rules.
- In `status`, only seven named sub-objects keep unknown fields (`evidence`, `reasoner`,
  `proposal`, `gate`, `execution`, `verification`, `kopf`), never the whole status.
- Result (`~/nexus-evidence/m1b-6/incident-crd-run-2.txt`): 26 of 26 cases pass. With each C-rule
  removed in turn, exactly that rule's deny cases fail. A first `spec.approval` is admitted
  (case `C2-allow-first`); every later change to it is denied.

### Kopf's 5 s cadence is a startup loop, not `@kopf.timer` (M1b-6b)
**Finding, run 1** (2026-09-28T20:30Z, `~/nexus-evidence/m1b-6/kopf-spike-run-1*`):
- Kopf 1.44.6 registers every `@kopf.timer` and `@kopf.daemon` with `requires_finalizer=True`
  (`kopf/on.py`, lines 778 and 715), with no setting to turn it off.
- Before any change handler runs, Kopf JSON-patches `/metadata/finalizers` with
  `kopf.zalando.org/KopfFinalizerMarker`. Under the staged RBAC that patch gets 403: 31 requests
  (two attempts per run, each retried up to 9 times).
- Kopf retries, escalates, throttles and starts again, so the create handlers never ran (60 s and
  72 s of waiting). The timer itself ran and wrote status (31 ticks, all 200).
- Kopf's resource observer also asked for `list customresourcedefinitions` (18 × 403). It does not
  need it: it logs a warning and keeps the resources from its startup scan.

**Decision** (owner, 6b gate, 2026-09-28): option (b).
- An operator-level loop, started in `@kopf.on.startup` and cancelled in `@kopf.on.cleanup`, lists
  the Incidents in `nexus-system` every 5 s and reconciles each one. It writes only through the
  status subresource.
- No timers, no daemons and no delete handlers, so Kopf never needs a finalizer.
- `settings.scanning.disabled = True` declares Kopf's restricted mode: no CRD or namespace list or
  watch.
- Kopf also runs with `--standalone`, `--namespace nexus-system` and `posting.enabled = False` (§11
  grants no events). Progress goes to `status.kopf.progress` (`StatusProgressStorage`) and the
  diff-base to `status.kopf.last-handled-configuration` (`StatusDiffBaseStorage`).

**Rejected**
- **(a) Grant `patch` (or `update`) on `incidents`.** This would enable self-approval:
  - RBAC cannot limit a verb to one field. `patch incidents` covers the whole object, `spec`
    included, not only `metadata.finalizers`.
  - C1 freezes `source`, `target` and `detectedAt`. But C2 admits the first `spec.approval` on an
    Incident that has none (6a, `C2-allow-first`).
  - Kyverno K5, which reserves `spec.approval` for group `nexus-approvers`, arrives in M3. Until
    then, an operator with `patch` could write an approval for its own proposal. §11 rules that out
    by omission ("It cannot approve its own proposals").
  - From M3, K3 admits an operator scale when an approved Incident exists. A self-written approval
    would then authorise the operator's own mutation unless K5 stops it, so approval integrity
    would rest on one control instead of two.
  - It also contradicts §4: "The operator has no patch rights on Incident metadata or spec".
- **(c) Register the timer with `requires_finalizer=False` through Kopf's internal registry**
  (`registry._spawning`, `TimerHandler`). This is a private API and may break on any upgrade.

### The single writer of each status field (owner, 6b gate)
| Field | The only writer |
| --- | --- |
| `status.phase`, `status.reason`, the phase timestamps (`status.timestamps.detected`, `.terminal`, …) | the reconcile loop |
| Intake fields: `status.autonomyLevel` (and, in the spike only, `status.timestamps.intake`) | Kopf's intake handlers |
| `status.kopf` (progress and diff-base) | Kopf |
| `spec`, `metadata` | never the operator (RBAC) |

- Writers JSON-merge-patch their own keys through `/status`, never a whole map.
- If a field ever has two writers, every patch carries the `resourceVersion` it read. A 409 means
  re-read, recompute and retry; there is never a blind overwrite.
- The loop computes each transition from fields the intake handlers own. Its writes therefore also
  carry the `resourceVersion` they were computed from (the spike tries 3 times).
- Handlers write only their intake fields, or just trigger the loop.
- **The loop never moves an Incident to a terminal phase while Kopf handler progress is pending in
  `status.kopf`** (owner, 6b gate). It waits a tick, or finishes after Kopf does. Pending means an
  entry is left in `status.kopf.progress`. Kopf's last patch holds the last handler's fields, the
  progress purge and the diff-base in one write, so the terminal write always comes after it, and
  nothing writes a terminal Incident (§7). In the spike, only the L0 path follows this rule so far
  (it waits for intake); the timeout path does not (see Consequences).
- **The rule is bounded (owner, 6b gate): every Kopf handler gets a timeout, and the loop escalates
  an Incident whose handler exceeded it.** Kopf checks `timeout=` only before each attempt and
  after a failed one (`kopf/_core/actions/execution.py`, lines 245, 276 and 312). It does not stop
  a handler that is still running. The loop's own check of the handler's `started` time in
  `status.kopf.progress` against that timeout is therefore the bound.

Reconcile rules in the spike (§7; plan M1b-8), with a missing or invalid `nexus.io/autonomy-level`
label read as L0 (§12):

| State | Next status |
| --- | --- |
| No phase | `Detected`; entry is creation, so `timestamps.detected` = `metadata.creationTimestamp` |
| `Detected`, intake done, L0 | `Recorded`, reason `level_observe` |
| `Detected` past the timeout: 20 s, checked every 5 s (fires within ≤ 25 s) | `Escalated`, reason `evidence_error` (no Evidence Collector yet) |

## Measured behaviour (run 2, 2026-09-28T21:13Z; `~/nexus-evidence/m1b-6/kopf-spike-run-2*`)
Incident A (L0) was killed mid-handler and resumed, Incident C (L0) was the race, and Incident B
(L1) waited for the timeout.

| Criterion | Result |
| --- | --- |
| P1 handlers run, progress lands in status | PASS. At the kill, `status.kopf.progress` had `intake_level` succeeded and `intake_stamp` pending. A ended `Recorded` / `level_observe`, with the diff-base in `status.kopf` |
| P2 no writes outside status | PASS. 0 patch or update calls on incidents outside `/status`. A, B and C ended with no finalizers and no annotations |
| P3 resume after SIGKILL mid-handler | PASS. `intake_level` ran once; `intake_stamp` started in both runs and finished only in run 2 |
| P4 every 403 listed | 0, in the audit log and in Kopf's logs |
| P5 no lost updates | PASS (see below) |
| P6 Detected timeout: 20 s, checked every 5 s (fires within ≤ 25 s) | PASS. B `Escalated` / `evidence_error` 23 s after creation. A and C `Recorded` 9 s after creation |

P5 forced the interleaving on C:
- the loop read C, Kopf's intake patch landed, and the loop's write carrying the old
  `resourceVersion` got 409;
- the loop re-read C and wrote again;
- C ended with both effects: `autonomyLevel` and `timestamps.intake` from intake, and `Recorded`,
  `timestamps.detected` and `timestamps.terminal` from the loop;
- Kopf's 6 status patches never carried `phase` or `reason`, and the loop's 6 writes never carried
  intake or `kopf` fields.

Other observations:
- Nothing wrote to A, B or C after the loop's terminal write: each final `resourceVersion` equals
  that write's.
- The operator identity made only these calls: discovery GETs, list and watch of incidents (Kopf),
  list and get of incidents (the loop), get of namespaces (intake), and patch of `incidents/status`.
  There were no CRD, namespace-list, event or peering calls.
- Staged grants left unused: incidents create (the Alert Poller's, M1b-8), `incidents/status` get
  and update, the two ConfigMaps, and namespaces list and watch.
- One 429 ("storage is (re)initializing") hit the loop's first list, 4 s after the CRD was
  applied. The loop logged it, and its next tick succeeded.

## Consequences
- M1b-8's operator uses the startup loop and this single-writer table: no `@kopf.timer`,
  `@kopf.daemon` or delete handlers. Kopf stays pinned (1.44.6). Rerun this spike
  (`operator/spikes/kopf-status/run-spike.sh`) after any Kopf upgrade. M1b-8 deleted the spike;
  its successor is `operator/tests/envtest/run-envtest.sh` (ADR-025).
- The loop needs its own API client (aiohttp in the spike) with the operator identity only. M1b-8
  chooses the client.
- A transition waits up to 5 s for the next tick. The Detected timeout is 20 s, checked every 5 s
  (fires within ≤ 25 s). A handler could wake the loop at once instead; M1b-8 decides.
- Direction for M1b-8 (owner, 6b gate): the loop never moves an Incident to a terminal phase while
  Kopf handler progress is pending in `status.kopf`; it waits a tick, or finishes after Kopf does.
  - The spike's timeout path does not follow this yet: it can escalate while an intake handler is
    pending, and Kopf's late patch would then write a terminal Incident.
  - M1b-8 tests that interleaving with a forced hook, as P5 did.
  - Every handler gets a timeout, and the loop escalates an Incident whose handler exceeded it,
    even with progress still pending (owner, 6b gate). A handler that finishes after that
    escalation would still write a terminal Incident, so M1b-8 must decide how to prevent it.
    One option: bound each handler body to the same timeout (`asyncio.wait_for`), and let the
    loop escalate one tick later.
- The spike's `timestamps.intake`, the race hook and its handler sleeps are test scaffolding, not
  operator design.
- Live confirmation comes with M1b-8's deployment on k3s: the API audit log should show the same
  set of calls.
