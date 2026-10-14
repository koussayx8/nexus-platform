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
- **What it checks.** `repo-checks.sh` step 9 fails when any of the 4 rule-defining files differs
  from `d351d964` (#79's head):
  - `kustomization.yaml` and `nexus-detection.yaml` under `platform/observability/alerts/`;
  - the promtool test;
  - `promtool-rules.sh`.
- **Every listed path exists at the pin** (2026-10-13, owner, GATE M1b-9 review). The guard
  (`scripts/lib/s5-pin-guard.sh`, tested by `scripts/tests/s5-pin-guard.sh`) fails on a listed path
  absent at the pin. `sample-api-error-rate.yaml`, deleted before the pin, is dropped from the list:
  a re-added file is not deployed unless the pinned `kustomization.yaml` names it.
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

## Addendum (2026-10-06): the #100 live gate stopped on a crash loop
- **What happened.** `nexus` deployed at `main` `5d7b1c7`, and the pod crash-looped (5 restarts in
  5 min) before any API call: `KeyError: 'getpwuid(): uid not found: 10001'` in Kopf's
  `peering.detect_own_id` → `getpass.getuser()`.
- **Why.** The image runs as UID 10001 with no `/etc/passwd` entry. envtest ran Kopf as a real user,
  so it never saw this.
- **Fix (owner, option 1).** The Deployment sets `USER=nexus-operator`, which `getpass` reads before
  it falls back to `pwd`. The verified digest `ef3c6955` stays.
- **Regression test.** `operator/tests/unit/test_runtime_user.py`, with `pwd.getpwuid` patched to
  raise and the user variables unset:
  - without `USER`, startup identity fails;
  - with it, it succeeds;
  - the manifest carries the variable.
- **Later.** A passwd entry at the next image rebuild, then the workaround goes.

## Addendum (2026-10-14): S5, the M1b exit demo — PASS, valid
**Verdict (owner, S5 gate review, 2026-10-14): PASS, valid. The M1b exit criterion is met.**
`NexusErrorRateAnomaly` fired for `nexus-dev`, and exactly one Incident created after t0 ended `Recorded`
(`level_observe`, L0) in `nexus-dev`. No discard rule and no stop condition fired. Evidence (0600, not in Git):
`~/nexus-evidence/m1b-s5/s5-20261014T0621Z/` (`s5-record.txt`).

**Run.**
- Graded `main` `01838a4e53c8161017cad09af277a09e921eea0a`; `experiment/dev-state`
  `f480a6e76b62c0dc57390cdc5339ebf738a739dd`. S5 pin (4 paths): empty diff.
- Values read live: ServiceMonitor 15s, Prometheus evaluation 30s, group interval 30s, Alertmanager `group_wait` 30s.
- Load: B = 80 req/s per namespace (160 users, 0.5 / 0.5). The VM rebooted about 05:24Z and Locust's
  `--autostart` resumed the swarm about 05:25Z. The owner applied the plan's registered §4 wording
  ("baseline still running, no new warm-up"); the pre-fault check decided readiness.
- Run-start baselines: `boot_id` `711847ec-2efa-4ad0-b850-eda01e6541eb`, k3s NRestarts 0; DB uid `b7616273…`
  restartCount 1 (the reboot); missed-evaluation counter 0 (Prometheus restarted 05:24:59Z).
- Pre-fault check (06:24:15Z): PASS. Every `nexus-dev` Z under 1.5 (max 0.55); no Nexus alert; 0 Incidents;
  kill switch `active`; node CPU 10.6 %, memory 25.6 %; 80.0 / 80.0 req/s; error-ratio baseline present;
  `nexus-dev` L0 in the file, live, and `sample-api-dev` Synced at the dev-state SHA.

**Timeline** (wall clock, UTC; injection and restore by the owner, spec §20 S5):

| Point | Time | From t0 |
|---|---|---|
| t0 `ALTER ROLE app_dev NOLOGIN`, sessions terminated (0 open) | 06:39:40.284 | 0 |
| t1 first dev `/items` 503 in the log | 06:39:40.802 | +0.5 s |
| t2 error ratio > 5 % and Z > 3; t3 alert pending (`activeAt`) | 06:40:39.823 | +59.5 s |
| t4 alert firing (d = 0.002 s) | 06:41:39.823 | **+119.5 s** (registered prediction +105 s) |
| t5 Alertmanager `startsAt` | 06:41:39.823 | +119.5 s |
| t6 Incident created (audit 06:41:45.161) | 06:41:45 | **+124.7 s**; t6 − t5 = 5.2 s |
| t7 `Recorded` / `level_observe` (audit 06:41:50.500) | 06:41:50 | **+129.7 s**; t7 − t6 = 5.0 s |
| Restore `ALTER ROLE app_dev LOGIN` (`app_dev\|t\|35`, `app_prod\|t\|35`) | 06:43:08.656 | |
| t8 first dev `/items` 200 / alert inactive / error ratio < 5 % | by 06:43:32 / 06:44:46 / 06:45:18 | |

- **The Incident:** `inc-8419006b8da738fc-1791960099`, created by `NexusErrorRateAnomaly`, fingerprint
  `8419006b8da738fc`, `startsAt` 06:41:39.823Z. It was the only new Incident.
- **Segment validity, t0..t7:** D −0.00003 s, D_c 0.006 s (reliable; r_start −0.00006, r_end −0.00003);
  0 missed `nexus-detection` evaluations. `boot_id`, k3s, the measured-path pods, the DB uid and restartCount, and
  the Application revisions were unchanged. The clock sampler (06:22–06:50Z) saw no step over 200 ms.
- **Operator:** 211 audit rows; no 403; no write outside `incidents` and `incidents/status`.
- **First fire, +119.5 s against +105 s:** recorded. t0 fell 0.46 s after an evaluation; the first evaluation
  that saw the ratio above 5 % was 06:40:39.8Z. Not analysed further.
- **Prod control (observation only, not gated):** across 06:24–06:46Z, `nexus-prod` had no Nexus alert, no 5xx,
  and its error-ratio Z stayed 0.

**Deviations and their effect.**
1. **First pre-fault check void (agent script defect).** The adapted run-start script compared Application
   revisions from two different readers (`None` vs `""` for the multi-source `kyverno` and `observability`), so
   the check failed with all 8 Applications unchanged. Nothing was injected. The owner classified it VOID, not a
   fail, and authorised one rerun of the run start and pre-fault check in a new run directory, after the fix was
   shown to pass on the recorded data and still fail on a changed revision. Effect: none on the result.
2. **The analysis script crashed after t8.** `kubectl logs --timestamps` returned `+02:00` offsets, which its
   timestamp parser did not accept. t8, the Locust `/stop` and the forward close had completed. The rest (timings,
   validity, audit, verdict) was rerun read-only with a fixed parser; its end-clock read was 2.5 min later than
   planned. Effect: none (r_end −0.00003).
3. **Guard re-probe waived** (owner, S5 plan gate review, 2026-10-13; ADR-021). It tests the development agent,
   not NEXUS; NEXUS's own guards (kill switch, L0) were in the pre-fault check.

**Still open.** The S5 pin guard is removed or re-pinned through an ADR (`TASKS.md`).
