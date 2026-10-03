# ADR-025: The Operator Skeleton — Alert Poller, Status-Only Reconciler, Staged §11 RBAC

## Status: Proposed (M1b-8, branch `feat/m1b-8-operator`; plan M1b-8 revision 3, approved by Koussay, typed, 2026-10-02)

## Context
Spec §4 defines the NEXUS Operator's stages. M1b needs only the first two: the Alert Poller and
the Incident Reconciler. The S5 exit demo ends with an Incident `Recorded` in `nexus-dev` at L0.
ADR-023 (the 6b spike) fixed the operator's Kopf mode:
- standalone;
- progress and diff-base in the Incident status;
- a startup loop instead of `@kopf.timer`;
- one writer per status field.

It left three things to M1b-8: the API client, the bound on a pending handler, and how a late
handler is kept from writing a terminal Incident.

## Decision

### Staged §11
- The operator runs with only the §11 rows M1b uses (`platform/rbac/nexus-operator.yaml`):
  - `incidents` get, list, watch and create;
  - `incidents/status` get, patch and update;
  - get on `nexus-killswitch` and `nexus-operator-config`;
  - namespaces get, list and watch.
- The other §11 reads, `deployments/scale` and `pods/eviction` come in M2.
- This is a temporary, documented difference from the frozen spec, not a redesign: no stage after
  `Detected` exists yet.

### Alert Poller
- **Polling.** Every `alertPollSeconds` (10 s) it sends
  `GET /api/v2/alerts?active=true&silenced=false&inhibited=false`.
- **Target.**
  - The poller reads the `nexus_target` label, which the four pinned Nexus alerts carry as
    `<namespace>/sample-api`.
  - The value must read `<namespace>/<name>`, the namespace must be one C3 admits, and it must
    equal the alert's `namespace` label.
  - Any other alert with `nexus_target` is ignored and logged once. Alerts without it, such as
    Watchdog, are ignored silently.
- **One Incident per firing episode.**
  - The key is (fingerprint, `startsAt`).
  - It is looked up in the API on every poll, across all phases (terminal ones included), never in
    process memory.
  - Each Incident carries the label `nexus.io/fingerprint` and `spec.detectedAt` = `startsAt`, and
    the two are compared as instants. The spec gives `detectedAt` only as a time (§8), so this
    contradicts nothing.
  - The name is `inc-<fingerprint>-<startsAt epoch seconds>`. If the lookup ever misses, the
    second create fails with 409 AlreadyExists; that is logged and never retried or overwritten.
- **Absorb.**
  - An open Incident on the same target absorbs other episodes. Each absorption is logged once
    per process, with alertname, fingerprint, `startsAt` and the Incident.
  - An absorbed episode has no Incident of its own. Once the absorbing Incident is terminal, the
    next poll gives the episode one if it still fires (owner, plan approval).
  - Keeping absorption durable would need the poller to write status, which breaks the single
    writer, so it is left to M2.
- **Smoke.**
  - An alert labelled `nexus_smoke="true"` creates an Incident labelled `nexus.io/smoke: "true"`.
    Detection counts select `!nexus.io/smoke`.
  - Smoke and real Incidents never absorb each other, so a smoke Incident cannot hide a real
    detection.
- **Alertmanager down.** The poll fails quiet (§16): errors are logged and counted, and no
  Incident is created.

### Incident Reconciler
ADR-023's single-writer table holds unchanged:

| Field | Writer |
|---|---|
| `phase`, `reason`, `timestamps` | the loop |
| `autonomyLevel` | the intake handler |
| `status.kopf` | Kopf |

**Rules:**

| State | Next status |
|---|---|
| No phase | `Detected`, with `timestamps.detected` = `creationTimestamp` |
| `Detected`, intake done, L0 | `Recorded` / `level_observe` |
| `Detected`, `detectedTimeoutSeconds` (20 s) after creation | `Escalated` / `evidence_error` |

- **The timeout runs from creation, not from `detectedAt`.** Otherwise the poll delay and
  Alertmanager's `group_wait` would use up the 20 s.
- **Pending handlers.** The loop never moves an Incident to a terminal phase while Kopf progress is
  pending in `status.kopf.progress`.
- **The bound** (ADR-023's open item). The intake handler has `timeout=10` s and its namespace read
  is bounded to the same 10 s (`HANDLER_TIMEOUT_S`). Once the oldest pending record started more
  than 10 s plus one loop period (5 s) ago, the timeout path escalates anyway.
  - In the normal case intake finishes in under a second, so the bound never delays the 20 s
    escalation.
  - A handler that still writes after that escalation is a residual risk. It needs a Kopf stall of
    more than 5 s past its own timeout. The envtest test's T criterion shows no such write.
- **API client.** The loop's own client is aiohttp with the ServiceAccount token, read again on
  every request. Its user agent is `nexus-operator`, so it can be told apart from Kopf's in the
  audit log.

### The kill switch in M1b-8
- **It changes no transition.** Its authority is over operator mutations (K4 in M3; Safety Gate
  stage 5, advisory, in M2), and M1b-8 has neither.
- Detection and recording go on while it is halted.
- The operator reads it once per poll and logs `active`, `halted` or `missing` (treated as halted)
  at start and on every change.

### Configuration
- `nexus-operator-config` is read at startup, so a change takes effect at the next start.
- Defaults exist only for the values the spec freezes (10 s, 5 s, 20 s).
- `alertmanagerURL` is required: without it, or with a malformed value, the operator stops at
  startup (`PermanentError`).
- The M2 keys (`advisoryChecks: "on"`, `approvalTTL: "15m"`, `breakerEpoch`, `reasonerEndpoint`)
  are carried by the template (PR B) and are not read by M1b.

### Liveness, metrics
- **Liveness now.** Kopf's endpoint is `--liveness=http://0.0.0.0:8080/healthz`.
  - The probe handler fails (HTTP 500) when a loop has stopped, or has not finished a tick for
    three periods (at least 30 s).
  - A tick counts as finished even when the API failed: liveness asks whether the loop turns, not
    whether the API answered.
- **Metrics and the ServiceMonitor in M2.**

### P6 and the margin (owner, M1b-8 point 3)
- **The window.** It is [T, T + P + m] = [20, 28] s: the timeout T = 20 s, the loop period P = 5 s
  and the margin m = 3 s (one API round trip plus scheduling jitter).
- **Where it applies.** It is checked by the envtest integration test, which is run by hand.
- **In CI.** The unit tests check the order with a fake clock: `Detected`, no terminal phase before
  20 s, then `Escalated`. CI never checks the window.
- **If envtest is ever wired into CI.** `run-envtest.sh` already switches to order-only on
  `CI=true`, and the margin is not widened.

### The S5 pin guard
- **What it checks.** `repo-checks.sh` step 9 fails when any of the 5 rule-defining files differs
  from `d351d964` (#79's head):
  - the 3 paths under `platform/observability/alerts/`;
  - the promtool test;
  - `promtool-rules.sh`.
- **Expiry.** At the M1b exit, after S5 is graded, the guard is removed or re-pinned through an ADR.

### `nexus-dev`'s level for S5
- **Now.** `nexus-dev` is L0, declared on `experiment/dev-state` (`overlays/dev/namespace.yaml`,
  ADR-018), and it stays L0 for S5.
- **Pre-check.** Right before the injection, all of these must hold, and all three go into the S5
  record:
  - the file shows "0" at the dev-state SHA;
  - the live label is "0";
  - `sample-api-dev` is Synced at that SHA.
- **Drift.** A value other than "0" stops the run. It is fixed by a PR into
  `experiment/dev-state`, never with kubectl.

## Consequences
- **The spike is replaced.** `operator/spikes/kopf-status/` is deleted, and the envtest test
  `operator/tests/envtest/run-envtest.sh` takes its place: P1–P6 plus episode, smoke, absorb,
  ignore, terminal-write, liveness and kill-switch checks. Rerun it after any Kopf upgrade.
- **Test-only code.** The race hook (`_race_hold`) and the envtest login exist only under
  `operator/tests/`. Production reaches them only through the two `None` seams in
  `nexus_operator/hooks.py`.
- **Deploy (PR B).**
  - It adds the ServiceAccount and Deployment (`operator/k8s/`), `rbac` to
    `platform/kustomization.yaml`, the `nexus` Application, the verify and bootstrap checks, and
    the ConfigMap template.
  - The owner applies the live ConfigMap by hand (ADR-019).
- **UNVERIFIED.**
  - Prometheus's `startsAt` is assumed to be the time the alert starts firing (after `for: 1m`),
    not when it became pending.
  - It is settled read-only against Prometheus `/api/v1/alerts` (`activeAt`) when a real alert
    fires: at the live acceptance, or in M1b-9.

## Addendum (2026-10-03, M1b-8 PR B): the deploy

### What deploys
- **The `nexus` Application** tracks `main` at `operator/k8s` (automated, prune, selfHeal), with
  `/spec/replicas` excluded from diffing and `RespectIgnoreDifferences=true` (§3). `root` creates it.
- **The image** is pinned by the digest CI built and Cosign-signed on `main` `3ea41f8`:
  `sha256:ef3c695542d308977d0909656dc40ddb5dfaf1c2168853c95b57c80959083b53`. `cosign verify`
  against `operator.yml@refs/heads/main` returned exit 0, with workflow SHA `3ea41f8`.
- **The image workflow** now ignores `operator/k8s/**`, so a manifest change builds no new image.
- **The Deployment.** 1 replica, `Recreate`, restricted security context, read-only root
  filesystem, the ServiceAccount token mounted, no readiness probe and no metrics port.
- **RBAC.** `platform/rbac` joins the `platform` kustomization.
- **The template.** `nexus-operator-config` gets its real schema. The live ConfigMap is admin-owned
  (ADR-019), so the owner updates it by hand before the merge.

### RBAC can arrive after the operator starts; the operator waits without restarting
`platform` (RBAC) and `nexus` (the operator) sync independently, so the pod can start first.

**What happens, measured** on envtest by `operator/tests/envtest/run-rbac-late.sh`, run 1, with the
code of image `ef3c6955`:
- The startup handler's ConfigMap read gets 403. That is an ordinary error, so Kopf retries the
  handler every 60 s (`default_backoff`) and the process never exits.
- Kopf starts no watcher, no loop and no `/healthz` before startup succeeds.
- Startup succeeded 47 s after the RBAC was applied. The only 403s were 2 `get configmaps/
  nexus-operator-config`, and there were none after startup.

**The probes follow from that.**
- `startupProbe`: `/healthz` every 10 s, `failureThreshold` 36, so 360 s. It covers the worst skew:
  `root` and `platform` share one Git reference cache, so their pickups differ by at most the
  controller's 180 s refresh window (ADR-020). Adding the 60 s retry gives 240 s, under 360 s.
- `livenessProbe`: 15 s × 3. It runs only after startup.

**The 403 rule at the live gate.**
- 403s on `get configmaps/nexus-operator-config` are allowed until the operator logs
  `config alertmanagerURL=`, its startup success.
- Any other 403, or any 403 after that line, is a stop condition.

### ADR-020 coupling
- **Rollout term.** The operator's is the 360 s startupProbe plus a 240 s pull allowance (49 MiB
  compressed; about 45 s at the 1.13 MB/s measured in M1-4, and up to 4× under contention) =
  600 s. That equals the existing rollout term, so none of the four ADR-020 values change.
- **Bootstrap wait.** `bootstrap.sh` waits 900 s for `nexus`, derived as for `dependency-db`:
  160 retry + 600 rollout + 60 stable = 820, rounded up.
- **Verify.** `verify-state.sh` keeps its 1140 s default and adds M11 (the CRD with C1–C4 and
  `Prune=false,Delete=false`) and M12 (the operator Ready on its Git-pinned digest).

### Known gap (Later)
- **A fresh bootstrap.** Step g creates `nexus-operator-config` after `root` exists. Image
  `ef3c6955` treats a missing ConfigMap as permanent (`ConfigError` → `PermanentError`), so the
  pod can restart until step g runs, then recover.
- **The live gate is not affected**, because the ConfigMap exists before the merge.
- **The fix** is a Later item, in either of two ways:
  - treat a 404 like a 403 (retry) in the next image;
  - create the ConfigMaps before `root`.
