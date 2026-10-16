> **M2-1 plan, rev 1, with the owner's plan-gate decisions recorded (2026-10-16). Frozen at approval; current state
> lives in TASKS.md.** Line references are at `dev` `a9dbfb38403004e3cd1f1f600d580bbd16b4212d`.

# M2-1 plan: ADR-030 step 0, the A1 feasibility screen on WSL2 (rev 1)

## Context

ADR-030 (a), step 0: before any arm64 work, record with Locust stopped the node's CPU and memory use and the summed
pod requests and limits (CPU and memory). If that leaves no room on 2 OCPUs / 12 GB, a decision gate chooses a reduced
stack before anything else. It is a screen only: no number carries over to the A1 calibration (M2-9).

The M2 plan (rev 3, GATE M2-0) places step 0 in M2-1, with the Locust stop and resume as an approved live step, and
adds the expected M2 components to the sums. The owner also checks A1 capacity in the console.

This plan fixes the go/no-go rule **before any measurement**. The rule cannot change after the window opens.

## Decisions requested at this gate

| # | Decision | Proposal |
|---|---|---|
| D1 | The go/no-go rule | Rules G1–G5 below, fixed now |
| D2 | How Locust is stopped and resumed | `GET /stop` and `POST /swarm` on the Locust web API through one approved port-forward; no Kubernetes object changes |
| D3 | The window | 10 min settle after the stop, then a 60-min window W, then the resume |
| D4 | The M2 additions | Placeholders P1 and P2 below, because no planned manifest exists yet (report c) |
| D5 | The void rules | V1–V5 below; a void window is repeated once, then reported |

**Owner, plan gate (2026-10-16):**
- **D1 accepted**, with pre-registered options for a failed G1 (Design, the go/no-go rule).
- **D2 accepted.** `POST /swarm` restores exactly the users, spawn rate and class filter in
  `platform/load/master.yaml`. Locust's state after the resume is checked against them and recorded (Design, Locust).
- **D3, D4 (P1, P2) and D5 (V1–V5) accepted**, with a note under V1 (Design, void rules).

## Gate reports a–d (commands run this session, read-only)

**a) Precondition: VERIFIED.** `git rev-parse HEAD dev origin/dev` →
`a9dbfb38403004e3cd1f1f600d580bbd16b4212d` (all three); the tree is clean.

**b) Host facts: VERIFIED.**
- `/proc/cpuinfo`, `lscpu`: Intel Core i7-9750H, 2.60 GHz, 6 cores, 2 threads per core, 12 logical CPUs, under
  Microsoft's hypervisor.
- `/proc/meminfo`: `MemTotal` 16298636 kB (15.54 GiB).
- ADR-030's last reading (2026-10-16, about 06:53Z, load state not checked): 2.16 cores and 3.8 GiB in use; pod
  requests 1.86 CPU and 2.70 GiB; memory limits 6.04 GiB. Those figures do not count for the screen, because
  Locust's state was not checked.

**c) The M2 additions have no planned manifest: CONTRADICTED as an input.**
- `grep -rn flight-recorder --include=*.yaml .` returns nothing. The `flight-recorder-db` manifests are an M2-6
  deliverable, and the operator's M2 resources are an M2-7 deliverable.
- What exists:
  - `apps/dependency-db/statefulset.yaml:66-72`: requests 100m / 256Mi, limits 2000m / 512Mi.
  - `operator/k8s/deployment.yaml:85-91`: requests 50m / 128Mi, limits 500m / 256Mi.
- So the screen uses labelled placeholders (Design, M2 additions). M2-6 and M2-7 re-check the rule against their
  real values by arithmetic, with no new measurement.

**d) Locust today: VERIFIED in Git, not read live.**
- `platform/load/master.yaml:56-65`: `--autostart --users=160 --spawn-rate=4 --expect-workers=1 --class-picker`;
  B = 80 req/s per namespace.
- Lines 18-23: there is no self-heal, so a stopped swarm stays stopped until a `POST /swarm`.
- The M1b scripts used the same calls: `cbrun.py:149-171` in
  `~/nexus-evidence/m1b-9/cb-run3-adr028-20261013T1323Z/scripts/` opens `port-forward svc/locust-master 18090:8089`,
  then `GET /stop` and `POST /swarm` with `user_count`, `spawn_rate=4` and `user_classes=DevUser,ProdUser`.

## Design

### The go/no-go rule (D1), fixed before measurement

**Capacity used by the rule.**
- CPU: 2 OCPUs. On Ampere A1, one OCPU is one core, so 2.00 cores.
- Memory: 12 GB, read conservatively as 12 × 10⁹ bytes, which is **M = 11.18 GiB**.
- Allocatable is taken as equal to capacity: k3s reserves nothing by default (UNVERIFIED on A1, read at M2-9).

**Inputs.**
- Node use with Locust stopped, from W.
- The requests and limits of every non-terminated pod in every namespace, kube-system included.
- The placeholders P1 and P2.
- WSL2 hyperthreads are counted 1 : 1 as A1 cores. How an i7-9750H thread compares with an Ampere Altra core is
  UNKNOWN, and the 50 % CPU-use bound (G3) is the margin for it.

**GO if all five hold. Otherwise a decision gate** (below).

| # | Check | Bound | Why this bound |
|---|---|---|---|
| G1 | Σ CPU requests + P1 + P2 | ≤ **1.50 cores** (75 % of 2.00) | Pods schedule only while requests fit. The 25 % left covers a rollout surge (`maxSurge: 1`), the sync hook Jobs, and k3s itself, which runs outside any pod |
| G2 | Σ memory requests + P1 + P2 | ≤ **8.39 GiB** (75 % of M) | The same reason, for memory |
| G3 | Node CPU in use (the maximum 5-min average over W) + the CPU requests of P1 and P2 | ≤ **1.00 core** (50 % of 2.00) | The other half carries load B, the faults and the operator. S2 alone drives one sample-api pod to its 1000m limit, which is half the node. The hyperthread-to-core ratio is unknown |
| G4 | Node memory in use (the maximum of `MemTotal − MemAvailable` over W) + the memory requests of P1 and P2 | ≤ **6.71 GiB** (60 % of M) | Memory cannot be throttled, and there is no swap. Under load, Locust, sample-api and both PostgreSQL instances grow, and the OS and page cache need room |
| G5 | Σ memory limits + P1 + P2 | ≤ **11.18 GiB** (M) | Then no pod within its limits can push the node out of memory |

- **Recorded, not gated:**
  - Σ CPU limits. Overcommitted CPU limits only throttle, and ADR-030 already makes a too-large DB limit a
    calibration decision.
  - The per-namespace use and the containers that have no limit.
- **NO-GO → decision gate.**
  - The agent reports the failing checks and the largest requesters and users per namespace. It proposes no
    reduced stack and changes nothing.
  - The owner chooses a reduced stack, or accepts the risk with a reason. Until then nothing else in M2 starts,
    as ADR-030 requires.
- **GO** means only that the stack is not ruled out. It is a screen: A1 is measured afresh at M2-9.

**Pre-registered options for a failed G1** (owner, plan gate, 2026-10-16). The decision gate chooses among them:
1. **Right-size requests** to measured usage plus a stated margin.
2. **Drop or shrink** named components.
3. **WSL2 hosts the M2 exit.**

- **W runs either way.** G3 and G4 are measured and inform option 1.
- **G1 is expected to fail.** The current requests are 1.86 cores (ADR-030's reading, load state not checked), and
  P1 adds 0.10, so the total is 1.96 cores against a 1.50 bound.

### The M2 additions (D4): placeholders until M2-6 and M2-7

| # | Component | Requests | Limits | Basis |
|---|---|---|---|---|
| P1 | `flight-recorder-db` | 100m / 256Mi | 500m / 512Mi | `dependency-db`'s M1 values (ADR-020, before ADR-028). It writes one row per transition, not one per request |
| P2 | Operator growth (Evidence Collector, Executor, Breaker, metrics) | +0m / +128Mi | +0m / +256Mi | Today 50m / 128Mi and 500m / 256Mi. The M2 stages add memory for evidence bundles; CPU stays event-driven |
| | **Total added** | **+100m / +384Mi** | **+500m / +768Mi** | |

- If M2-6 or M2-7 sets a value above its placeholder, that gate recomputes G1–G5 with W's numbers.
- A failure there is a decision gate, as above.

### How Locust is stopped and resumed (D2): the least invasive way

| Option | Effect | Verdict |
|---|---|---|
| `GET /stop`, later `POST /swarm`, on the Locust web API through a port-forward | Changes no Kubernetes object, so ArgoCD sees no drift. The master and worker pods keep running, and their requests stay in the sums, as they would on A1 | **Proposed** |
| Scale the Locust Deployments to 0 | A `kubectl` write on an ArgoCD-managed object; selfHeal reverts it (CLAUDE.md rule 2) | Rejected |
| A Git commit that drops `--autostart` | A desired-state change and a rollout, outside this phase | Rejected |

- **Agent commands** (`kubectl` reads only; the port-forward is an `ask` rule and is part of the approved step):
  1. `kubectl --context default -n nexus-load port-forward svc/locust-master 18090:8089`, started in its own
     process group.
  2. `GET http://127.0.0.1:18090/stop`, then `GET /stats/requests` until `state` is `stopped` and `user_count`
     is 0. Record both.
  3. Close the forward by its process group and check that port 18090 is free.
  4. At the resume, open a fresh forward and `POST /swarm` with `user_count=160`, `spawn_rate=4` and
     `user_classes=DevUser`, `user_classes=ProdUser`. Read `/stats/requests` until `state` is `running` with 160
     users, and record the class split. Close the forward.
  - **What the resume restores** (owner, plan gate): exactly the users, spawn rate and class filter in
    `platform/load/master.yaml`: `--users=160`, `--spawn-rate=4`, and no class filter (`--class-picker` with both
    classes, `DevUser` and `ProdUser`).
  - **Checked and recorded:** Locust's state after the resume (`state`, `user_count`, the classes running and their
    split) is checked against those three values. Any difference is recorded and reported at the gate.
- **Back at B.** The resume is confirmed when the service-proxy query gives 80 ± 8 req/s per namespace for
  5 consecutive minutes.
- **Dashboards.** No dashboard or other port-forward is open during W (standing rule).

### The window (D3)

| Point | Time | What is recorded |
|---|---|---|
| T_stop | Locust stopped | Start reads (below), the `/stop` answer |
| Settle | T_stop → T_stop + 10 min | Nothing gated. The 5-min rates decay, in-flight requests drain and DB sessions close |
| **W** | **T_stop + 10 min → T_stop + 70 min (60 min)** | Node and per-namespace use (Prometheus); `kubectl top node` at the start, the middle and the end; the requests and limits snapshot at the start and the end |
| T_resume | After the end reads | End reads, the `/swarm` answer, back at B |
| Follow-up | T_resume → T_resume + 30 min | Incidents created from T_stop onwards (read only) |

- **Why 60 min:** it covers about 20 ArgoCD reconcile cycles, and Kyverno's background scan if it runs hourly
  (1 h is the usual default, UNVERIFIED for this chart's values). Prometheus's 2-hourly head compaction may or may
  not fall inside W; the record says which.
- **Expected traffic gap:** about 72 min, from T_stop to T_resume.

**Start and end reads**, each with its UTC time:
- `boot_id` (`/proc/sys/kernel/random/boot_id`);
- `systemctl show k3s -p MainPID -p NRestarts -p ActiveEnterTimestamp`;
- `CLOCK_REALTIME`, `CLOCK_MONOTONIC` and `CLOCK_BOOTTIME`, as one Python `time.clock_gettime` sample;
- every Application's sync revision, sync status and health (`kubectl get applications -n argocd -o json`, read).

From these, two figures go into the record:
- **The pause gap:** (Δrealtime − Δmonotonic) between the start and end reads. A WSL VM pause shows here.
- **The traffic gap:** T_resume − T_stop.

### What is measured

All Prometheus reads go through the API server's service proxy (`kubectl get --raw …/proxy/api/v1/query`, GET).

| Figure | Query or source |
|---|---|
| Node CPU in use | `max_over_time((sum(rate(node_cpu_seconds_total{mode!~"idle\|iowait\|steal"}[5m])))[60m:30s])` at the end of W; the mean with `avg_over_time` |
| Node memory in use | `max_over_time((node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes)[60m:30s])`; the mean likewise |
| Per-namespace CPU | `sum by (namespace) (rate(container_cpu_usage_seconds_total{container!="",image!=""}[5m]))`, maximum and mean over W |
| Per-namespace memory | `sum by (namespace) (container_memory_working_set_bytes{container!="",image!=""})`, maximum and mean over W |
| Requests and limits | `kubectl get pods -A -o json`. Each non-terminated pod counts its effective request: the larger of its summed containers and its largest init container, plus overhead. The sums are taken per namespace and in total. Containers with no limit are listed. Cross-checked against `kubectl describe node koussay` (Allocated resources) |
| Node capacity | `kubectl get node koussay -o json` (`capacity`, `allocatable`) |
| Non-k3s load on the VM | `ps -eo pid,comm,%cpu,rss --sort=-%cpu \| head` at the start and the end. The agent's own session is in it, and is named |

The arithmetic is a short Python script in the scratchpad. It reads only the saved JSON, and its output and inputs
go to `~/nexus-evidence/m2-1/` (0600, not in Git).

### Void rules (D5)

A void window is repeated once from a fresh Locust stop. A second void is reported, and the gate decides.

| # | Void if |
|---|---|
| V1 | `boot_id`, or k3s `MainPID` or `NRestarts`, differs between the start and the end |
| V2 | The pause gap exceeds 1 s |
| V3 | Locust is not `stopped` at the start or the end of W |
| V4 | An Application revision changes during W: no merge to `main` or `experiment/dev-state` while W runs |
| V5 | A dashboard, another port-forward, or heavy local work (builds, test suites, image pulls) runs during W |

**Under V1 (owner, plan gate, 2026-10-16):**
- There were four clean VM power-offs on 2026-10-16: 06:29:49Z, 06:34:15Z, 06:37:23Z and 10:57:15Z, each logged by
  systemd-logind as "The system will power off now!".
- The 10:57:15Z one is unexplained, and the owner checks the Windows side.
- **During W, a WSL terminal stays open and the laptop stays awake.**

### Owner step: check A1 capacity in the console (create, then terminate)

Any day before the M2-1 gate report; the result goes into the report.

1. OCI Console, **home region**: Compute → Instances → Create instance.
2. Image: Canonical Ubuntu 24.04 (aarch64). Shape: **`VM.Standard.A1.Flex`, 2 OCPUs, 12 GB**. Leave the boot volume
   at its default (at least 47 GB, inside the 200 GB Always Free total).
3. Networking: no public IPv4 address. No SSH key is needed, because nobody logs in.
4. Create. If the answer is "Out of host capacity", try each availability domain once and record each answer.
5. On success, as soon as the state is Provisioning or Running: **Terminate**, with "Permanently delete the attached
   boot volume" ticked.
6. Confirm that the instance shows Terminated and that no boot volume is left (Storage → Boot volumes).
7. Report the region, the availability domain, the UTC time, the result for each AD tried, and that nothing remains.

- **Cost:** none. A1 within 2 OCPUs and 12 GB is Always Free (ADR-030 fact-check).
- **Effect on the rule:** capacity varies over time, so a success now does not reserve it for M2-9. It does not
  change GO or NO-GO. It feeds the A1 date decision (2026-11-20, GATE M2-0).

## Phases and gates

| Step | Content | Cluster effect | Gate |
|---|---|---|---|
| **M2-1.0** | This plan (PR to `dev`) | none | **M2-1 plan gate**: D1–D5 approved or changed. The rule is frozen here |
| **M2-1.1** | The owner's A1 capacity check | none (OCI, Always Free) | — |
| **M2-1.2** | The approved live step: Locust stop, settle, W, resume, back at B; then the follow-up reads | Locust traffic paused about 72 min on WSL2 | — |
| **M2-1.3** | The arithmetic and the report: G1–G5 with their inputs, the void rules, the pause and traffic gaps, any Incidents | none | **GATE M2-1**: GO, or a decision gate on a reduced stack |

## Verification

- **Offline:** the arithmetic script runs first on a fixture JSON with known sums (one pod with an init container,
  one with overhead, one with no limits), and its totals are checked by hand.
- **Live:** read-only reads, plus the approved port-forward and the two Locust HTTP calls. After the resume, B is
  back (80 ± 8 req/s per namespace for 5 min) and every Application is `Synced`/`Healthy`.

## Assumptions (open ones only)

- Locust 2.46.7 answers `GET /stop` as the M1b scripts used it (`cbrun.py:166`). Checked by the `/stats/requests`
  state after the call.
- node-exporter's view is the WSL VM, so node CPU and memory include non-k3s processes. The `ps` read names them.
- k3s on A1 reserves no allocatable resources by default: UNVERIFIED, read at M2-9.
- `MemAvailable` is the right "in use" basis on both kernels. It is the standard basis, and M2-9 measures A1 directly.

## Risks

- **Resuming the traffic may raise Incidents.** A step from 0 to B in the request rate can fire
  `NexusTrafficAnomaly`; R1 raised such Incidents (`~/nexus-evidence/m1b-9/r1/incident-*.yaml`). The operator, not
  the agent, writes them: L0 `Recorded` in `nexus-dev`, `Escalated` in `nexus-prod`. They are listed in the report
  and left in place. The owner deletes them if wanted.
- **The traffic gap** (about 72 min) shifts the Z-score baselines for about 18 min after the resume
  (`[15m] offset 3m`). No timed run may start in that time.
- **The 1 : 1 hyperthread-to-core count** may err in either direction. G3's 50 % margin is the only protection, and
  M2-9 measures A1 directly.
- **The placeholders** may be low. M2-6 and M2-7 recompute the rule.
- **The A1 capacity check** creates a real instance. Ticking the boot volume deletion matters: a left-over volume
  is still free inside 200 GB, but it counts against that total.
