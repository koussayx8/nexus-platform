> **Draft for the M2-1b plan gate (2026-10-16), rev 2. Frozen at approval; current state lives in TASKS.md.**
> Rev 2 applies the owner's revisions a–e (2026-10-16): the replay pass rule, checkpoint A as a deviation, the
> generated allow list, the lock and the values commits, and the schedule. Rev 3 (2026-10-16) adds the
> `derive_values.py` acceptance test, aligns PR7's DB cost with ADR-028's definition, states what depends on the
> log-derived columns, and records where the rebuilt scripts are saved. Line references are at `dev`
> `a9dbfb38403004e3cd1f1f600d580bbd16b4212d` or at tag `v0.3.0`, as marked. Prices were read from Oracle's public
> price list API on 2026-10-16T09:11Z.

# M2-1b plan: ADR-030 (b), the M1b protocol replicated on an OCI x86 trial VM (rev 3)

## Context

ADR-030 (b): replicate the M1b protocol on one x86 Flex VM paid from the OCI trial credit, running `v0.3.0`
(`86cea844dff5dac8cbd3f61f57a3ffc94aac2838`). The protocol is:
1. the R1 ramp;
2. C, B and the sample-api and DB limits, re-derived on that host;
3. the clean-baseline checkpoints;
4. the S5 run.

**Fixed at GATE M2-0** (M2 plan, Decisions):
- The VM runs `v0.3.0` plus a pre-registered, parameter-only diff: re-derived limits, Locust users and autostart.
- Two branches, `replication/x86` and `replication/x86-dev-state`, and a diff check that accepts only the parameter
  paths and the ref lines.
- The scripts run on the VM, as in M1b's single-host topology.
- Two named commits.
- **Stop rule:** not calibrated by 2026-11-01 → (b) is recorded as not done.
- **Billing guard:** every trial resource is deleted before 2026-11-09.

This plan proposes everything the two gates fix. It starts nothing.

## Decisions requested

**At this plan gate.** These are needed before the branches exist or before any replay runs:
- the approach, the schedule, the shape and the access model;
- **PR1**, the autostart handling: it is in the branch content that the VM is bootstrapped from;
- **the allow-list generator's rules**: the generator, not the list, is what gets reviewed;
- **the replay pass rule**: fixed before any replay.

**At the pre-registration gate (GATE M2-1b, decision).** Frozen there, before any R1:

| # | Pre-registration item | Proposal (section) |
|---|---|---|
| PR1 | Autostart handling | Drop `--autostart`; Locust idles until a timed run starts it (Autostart). *Decided at the plan gate; recorded in the pre-registration* |
| PR2 | The diff-check script and its fixtures | `experiments/replication/diffcheck.py` reads a **generated** `allow.tsv`; 17 fixtures, plus the generator's own tests (Diff check) |
| PR3 | Branch and protection steps | The branches are cut and locked before the bootstrap. The lock is lifted only for a named values commit, which is forward-merged to `replication/x86-dev-state` with a merge commit; the diff check runs before the lock is restored (Branches) |
| PR4 | The attempt count | One valid attempt per step, R1 at most two; one repeat after a void (Attempts) |
| PR5 | The pass and void rules | M1b's rules unchanged, plus a shape check and **deviation DEV-1, checkpoint A not replicated** (Rules) |
| PR6 | The two named commits | The replication head the VM was bootstrapped from, and the `dev` harness commit (Named commits) |
| PR7 | The values derivation | `derive_values.py`: B, the users and both CPU limits from R1's recorded outputs by fixed formulas (Values) |
| — | Host facts | `nproc`, `MemTotal`, the CPU model, the cgroup layout (U8), the iptables rules (U9) and `MemTotal` at 16 GB (U10), read on the bootstrapped VM and recorded as pre-registration inputs |

## Gate reports a–l (commands run this session, read-only except the write-once export in j)

**a) Precondition: VERIFIED.** `dev` = `origin/dev` = `a9dbfb38403004e3cd1f1f600d580bbd16b4212d`; the tree is
clean. `git rev-parse 'v0.3.0^{}'` = `origin/main` = `86cea844dff5dac8cbd3f61f57a3ffc94aac2838`.

**b) M1b's timed-run and analysis scripts: located; partly VERIFIED, the rest UNKNOWN.**

Where they are:
- No M1b session scratchpad survives. `/tmp/claude-1000/-home-azure-nexus-platform/` holds only five sessions from
  2026-10-16.
- The copies are in the evidence bundles (`find ~/nexus-evidence -name '*.py'`).

How each copy was checked:
- **Bytecode.** The run directory's `__pycache__/common.cpython-312.pyc` was compiled at run start from that
  directory's `common.py`. Its header (mtime and size) matches the file, and `marshal.dumps` of the pyc's code object
  equals that of `compile()` on today's file (`bytecode-equal True`, all five bundles).
- **mtime.** The file's mtime (UTC) is before the run's first `run.jsonl` record, and later than nothing the run
  wrote. A deliberate `touch` would defeat it; nothing suggests one.
- **Patch.** `patch -o` of the earlier version with the bundle's own diff reproduces the run version byte for byte
  (`cmp`).
- **Traceback.** `s5c.err`'s traceback quotes lines 7 and 71, and today's `s5c.py` has the same text on those lines.

| Step (M1b record) | Script, sha256 prefix | Evidence | Status |
|---|---|---|---|
| R1 attempt 2 (VALID, C ≥ 200, B = 80) | `experiments/calibration/ramp.py`, blob `64aba469…` | `r1-rerun/start-record.md`: the run used `dev` `2761a9a9…`, blob `64aba469e44c…`. `git rev-parse` gives the same blob at `2761a9a`, `v0.3.0` and `dev` | **VERIFIED** |
| R1 attempt 2 | The wrapper `r1run.py` (writes `r1-run.jsonl`), `r1analyze.py` (`result.txt`) and their `common.py` | **Rebuilt** from the transcripts (report k): r1a2 `r1run.py` `23399c72…`, `r1analyze.py` `dae4de71…`, `common.py` `18506600…` | **UNKNOWN until the replay** (Replay) |
| Checkpoint A (VALID, PASS) | The orchestrator and `analyze.py` | The session `5543b716` scratchpad is gone | **UNKNOWN**; not needed, because checkpoint A is not replicated (DEV-1) |
| Clean windows 1 and 2 (200m era) | The monitors | The session `aeff76de` scratchpad is gone | **UNKNOWN**; not needed (superseded) |
| Checkpoint B, 2026-10-12 (VOID) | `cb-run3-20261012T0811Z/scripts/*` | Its `README.md`: "Reconstructed 2026-10-13 verbatim from the agent's session" | A reconstruction, **not** the run version; not used |
| Checkpoint B repeat, 2026-10-13 11:35Z (VALID, FAIL A1) | `cbrun.py` `6dc9ed674562`, `common.py` `00f82361479b` | `cbrun.py` mtime 11:33:59Z, before the first record at 11:35:02Z. `cbrun.orig.py` + `cbrun.repeat.diff` = `cbrun.py`. `common.py`: bytecode-equal, compiled at 11:34:12Z | **VERIFIED** |
| **Checkpoint B, ADR-028, 2026-10-13 13:24Z** (VALID, FAIL A1, A2, D1, D2) | `cbrun.py` `b5fb3ff717e8`, `common.py` `00f82361479b`, `clocksample3.py` `96cf06f9296b`, `clockanalyze3.py` `3ad1b4e4e435`; `stop.py` `553fa23d1a09` (post-run stop) | `cbrun.py` mtime 13:23:26Z, before the first record at 13:24:16Z. `cbrun.repeat.py` + `cbrun.adr028.diff` = `cbrun.py`. `common.py`: bytecode-equal, compiled at 13:23:35Z. The clock scripts are byte-identical in every bundle | **VERIFIED** |
| **S5, 2026-10-14 06:22Z** (PASS, valid) | `s5a2.py` `041d57180c3a`, `s5b.py` `f70dcb16a017`, `s5c.py` `0ee921a9cace`, `common.py` `1410a749ca1f`, the same clock scripts; `s5c-analysis.py` `ac68affac15d` (the read-only rerun after the crash) | `s5a2.py` mtime 06:21:59Z, before the first record at 06:22:13Z. `common.py`: bytecode-equal, compiled at 06:22:11Z. `s5b`, `s5c` and the clock scripts are unchanged since 2026-10-13T18:12:42Z. The `s5c.py` traceback lines match. `s5c-analysis.py` mtime 06:47:55Z, after the crash, as recorded (ADR-025 addendum, deviation 2) | **VERIFIED** |

Facts for the harness:
- The two `common.py` files differ (`00f82361…` for checkpoint B, `1410a749…` for S5).
- `cbrun.py:10` and `s5a2.py:8` hard-code `B = 80.0; USERS = 160`.
- `cbrun.py:20` and `:122` build the systemd cgroup path `kubepods.slice/kubepods-burstable.slice/…`.
- No other host literal appears in these scripts (`grep -o`).

**c) The ref lines: CONTRADICTED. Gate report k listed too few.**
- M2-0's report k named `bootstrap.sh:151-162` and `:439-446`.
- At `v0.3.0`, `git grep -E 'origin/main|experiment/dev-state'` hits 30 files, and `git grep -w main` adds the
  ArgoCD `targetRevision: main` lines and two `git fetch origin main …` lines.
- Applying the generator's rules (Diff check) to these hits gives **38 substituted lines**:
  - **9** in `platform/argocd/`: 8 `targetRevision: main` and `sample-api-dev`'s `experiment/dev-state`;
  - **21** in `scripts/bootstrap.sh`: lines 151–154, 157, 159, 161–162, 439–441, 444–447, 493, 494, 497, 522, 531
    and 532;
  - **8** in `scripts/verify-state.sh`: lines 101, 133–136, 199, 386 and 387.
- Every other hit is kept, with its reason:
  - comment lines, such as `verify-state.sh:95,380`, `bootstrap.sh:509`, `overlays/dev/*:1`, `apps-stable.jq`;
  - `main` as a jq variable name (`--arg main`, `$main`);
  - the sample-api Python module `main`;
  - files not executed on the VM: docs, `TASKS.md`, `CHANGELOG.md`, `CLAUDE.md`, `.github/`,
    `scripts/capture-state.sh` and the test fixtures.
- The generator's run, not this summary, produces the list that is checked.

**d) Locking right after the pre-registration commit conflicts with the derived values: CONTRADICTED; resolved in
PR3.** C, B and the limits exist only after R1, so the values commit must land after the lock.

**e) Cutting both branches from the tag conflicts with `bootstrap.sh`'s guard: CONTRADICTED; resolved in PR3.**
- `bootstrap.sh:161-162` requires the `main` stand-in to be an ancestor of the dev-state stand-in.
- So `replication/x86-dev-state` is cut from `replication/x86`'s head, and every values commit reaches it by a merge
  commit.

**f) `experiment/dev-state` adds no deployed content at M1b: VERIFIED.**
- `git diff --quiet v0.3.0 origin/experiment/dev-state` → equal trees.
- At S5's dev-state `f480a6e7…`, `git diff --stat v0.3.0 f480a6e7 -- apps/ platform/` is empty.

**g) CI on the replication branches: VERIFIED, none.** At `v0.3.0`, `ci.yml` and `operator.yml` build on `push` to
`main` only. `repo-checks.yml` runs on `main` and `dev` and on pull requests to them. A push to `replication/*`
builds no image.

**h) Prometheus retention.**
- `prometheus_tsdb_lowest_timestamp_seconds` → 2026-09-30T06:00Z (read 09:15Z), with `retention: 15d`.
- R1 attempt 2's data (2026-10-09) would expire around 2026-10-24.
- The export in report j removes that deadline for r1analyze.py's own series.

**i) Checkpoint A has no equivalent on a fresh host.** It is recorded as deviation DEV-1 (Rules).

**j) The R1 export, write-once: DONE (read-only queries).**
- **Path:** `~/nexus-evidence/m1b-9/r1-export/20261016T112847Z/`. Files 0400, directory 0500.
- **`MANIFEST.json` sha256:** `8b8e6938e093ef060d8e0ca8b841932f2dca7a921c084917a40c1ca957ad6596`.
- **Content:** the raw samples of the five series that the rebuilt `r1analyze.py` queries, one instant query of
  `<selector>[1800s]` per 30-min chunk, saved verbatim:
  - `prometheus_rule_group_iterations_missed_total{rule_group=~".*nexus-detection.*"}`: 351 samples;
  - `http_requests_total{job="sample-api",namespace="nexus-dev",handler!~"/health|/ready|/metrics"}`: 2,808;
  - `http_request_duration_seconds_bucket{…,handler="/items",le="0.1"}`: 1,404;
  - `…{le="0.05"}`: 1,404;
  - `http_request_duration_seconds_count{…,handler="/items"}`: 1,404.
- **Range:** (2026-10-09T16:55:00Z, 19:55:00Z]. That is REFb (17:55:56Z) minus one hour, to past END-REFb
  (19:01:06Z) plus 30 min.
- **Also recorded:** Prometheus build info and the sha256 of each rebuilt script. All 31 file hashes check against
  the manifest.
- **Not exportable:** `r1analyze.py:55-62`'s `kubectl logs` input (`db_slots_exhausted` and `db_unavailable` per
  step). Both dev pods (`lddq8`, `tjqn4`) have restarted 10 times, and their current and previous logs start at
  2026-10-16T10:57:55Z.
- **Not exported:** the series `ramp.py` queried live. Its per-step values are taken as recorded in
  `ramp/steps.csv`.

**k) How the R1 scripts were rebuilt.** A scratchpad script, `rebuild_r1.py`, takes only these parts of the
transcript tool calls:
- the heredoc file bodies:
  - `common.py` from session `5543b716` at 2026-10-09T09:06:47Z;
  - `r1run.py` at 13:23:34Z;
  - `r1analyze.py` at 13:25:02Z;
- the string-replacement patch programs at 13:23:43Z, 17:53:28Z, 17:54:15Z and 19:01:55Z, together with the two
  `sed` path edits.

Nothing else in those commands runs. Checks so far:
- No other write to these files appears in either transcript up to the launch at 17:53:50Z, or after it except
  the 19:01:55Z raw-counter patch.
- The rebuilt `r1a2/common.py` gives the `EV =` line that the transcript's own `grep` printed.

Transcript sha256 prefixes: `702f3821…` `5f5f14f3e03e9c1a`, `5543b716…` `34766626fc8bf467`.

**Saved write-once** (rev 3) at `~/nexus-evidence/m2-1b/rebuilt/20261016T131740Z/` (files 0400, directories 0500;
`MANIFEST.json` sha256 `d1f459f479e2a32455c3af3199f67b6812318074c5dc01702ad423c3dd89c78c`):
- `r1a2/r1analyze.py`, `r1a2/r1run.py` and `r1a2/common.py`. Their sha256 equal the hashes in the Replay section
  (checked at save time);
- `rebuild_r1.py`;
- the seven transcript commands it reads, under `inputs/`.

**l) `ramp.py` can re-judge recorded steps.** `ramp.py knee <steps.csv>` (lines 652-668) recomputes the full verdict
from a steps file with the thresholds as flags. So each step's verdict, the knee and C can be replayed exactly with
the VERIFIED blob.

## Schedule

The owner creates the VM by 2026-10-20. The bootstrap waits for the harness PR to merge. No R1 runs before GATE
M2-1b pre-registration. Every date is a latest date. A step that misses its date is recorded as not done, and the
deletion date overrides every other step.

| Latest date | Step | Who |
|---|---|---|
| 2026-10-17 Sat | Plan gate: approach, PR1, the generator's rules, the replay pass rule | **owner** |
| 2026-10-19 Mon | Console: the service limit for `VM.Standard3.Flex` (E4 and E5 as fallbacks) at 6 OCPUs and 16 GB (U4); the credit left (U5); the cost estimate (U6); a budget alert at the first euro; the owner's IP (U7). The A1 capacity check (M2-1) | **owner** |
| **2026-10-20 Tue** | **Create the VM** (Access), not yet bootstrapped: security list, the `nexus-agent` user (no sudo, in `adm`), its key, the host-key fingerprint. The agent checks that SSH answers and touches nothing | **owner**; agent |
| 2026-10-21 Wed | The harness PR to `dev`: the verbatim and derived scripts, the rebuilt R1 scripts with **the replay run under the fixed rule and its record**, the generator and `allow.tsv`, the diff check with fixtures, `derive_values.py` with tests. Typed merge | agent; **owner** merges |
| 2026-10-22 Thu | Branches generated from `v0.3.0` (ref commit and PR1), diff check PASS on both, pushed (approved). Rulesets: lock and protect | agent; **owner** rulesets |
| 2026-10-22 Thu | **Bootstrap** the VM from `replication/x86` (sudo); iptables check (U9); the kubeconfig for `nexus-agent` | **owner** |
| 2026-10-23 Fri | `verify-state.sh` exits 0 on the VM; host facts read (nproc, `MemTotal`, CPU model, U8, U10); harness copied and `sha256sum -c` passes; pre-registration PR opened | agent |
| **2026-10-24 Sat** | **GATE M2-1b, pre-registration** (decision). R1 may start only after it | **owner** |
| 2026-10-25 Sun | R1 attempt 1 (a void repeat by 10-26); `derive_values.py` | agent |
| 2026-10-27 Tue | Values commit V1 (PR3 6), rollout, `verify-state.sh` | agent; **owner** lock off and on |
| 2026-10-29 Thu | Only if a limit changed: R1 attempt 2 (a void repeat by 10-30), then V2 if B changes | agent; **owner** |
| **2026-11-01 Sun** | **Calibrated:** C, B and the limits live on both branches, `verify-state.sh` exits 0, the diff check passes. Otherwise (b) is recorded as not done, and the next step is the deletion | agent; **owner** gate |
| 2026-11-03 Tue | Checkpoint B (20-min warm-up at B), then run 3 (60 min) if it passes; void repeats included | agent |
| 2026-11-05 Thu | S5: the owner injects and restores (spec §20) | **owner** + agent |
| 2026-11-06 Fri | Evidence copied off the VM with a sha256 manifest; **GATE M2-1b, result** | agent; **owner** |
| **2026-11-08 Sun** | **Delete** the instance, its boot volume, any block volume, any reserved IP and any load balancer. Send a console screenshot of the empty lists; the agent confirms that SSH no longer answers | **owner** |

**Slack, recomputed.**

| Segment | Window | Work, every step valid / worst case | Slack |
|---|---|---|---|
| Plan gate → harness merge | 10-17 → 10-21 | 2 days (PR, replay, review) | 2 days |
| Harness merge → pre-registration | 10-21 → 10-24 | branches 1 day, bootstrap 1 day, facts and PR 1 day | 0 days. **Critical path** |
| Pre-registration → calibrated | 10-24 → 11-01 (8 days) | R1 1, V1 1 → **2 days** / R1 with a void repeat 2, V1 1, R1b with a void repeat 2, V2 1 → **6 days** | **6 / 2 days** |
| Calibrated → result | 11-01 → 11-06 (5 days) | checkpoint B and run 3 1–2, S5 1–2, result 1 → 3–5 days | 2 / 0 days |
| Result → deletion | 11-06 → 11-08 | 1 day | 1 day |

- A one-day slip before the pre-registration comes out of the 8-day calibration window.
- The VM bills from 2026-10-20, two days before it is used, because it is created before the bootstrap.

## Design

### The shape: as close as the trial allows to the WSL2 node

The WSL2 node: an Intel i7-9750H, 6 cores × 2 threads = 12 logical CPUs, `MemTotal` 15.54 GiB (M2-1 plan, report b).
On OCI x86, 1 OCPU = 1 core = 2 vCPUs, so **6 OCPUs = 12 vCPUs**.

| Shape (6 OCPU, 16 GB) | CPU | €/OCPU-h | €/GB-h | €/h | €/day | 19 days (10-20 → 11-08) | Worst case, 20 days |
|---|---|---|---|---|---|---|---|
| **`VM.Standard3.Flex`** (proposed) | Intel Xeon, SMT 2 (the same vendor and threading as the WSL2 host) | 0.0372 | 0.001395 | 0.2455 | 5.89 | **€112.0** | €117.8 |
| `VM.Standard.E4.Flex` (fallback 1) | AMD EPYC, SMT 2 | 0.02325 | 0.001395 | 0.1618 | 3.88 | €73.8 | €77.7 |
| `VM.Standard.E5.Flex` (fallback 2) | AMD EPYC, SMT 2 | 0.0279 | 0.00186 | 0.1972 | 4.73 | €89.9 | €94.6 |

- **Prices:** Oracle's public price list (`apexapps.oracle.com/…/products/?currencyCode=EUR`, read 2026-10-16T09:11Z).
  Parts: `B94176`/`B94177` "Standard - X9" for `Standard3.Flex` (that mapping is UNVERIFIED, U6); `B93113`/`B93114`
  E4; `B97384`/`B97385` E5.
- **Boot volume:** 50 GB balanced, about €2 a month, and free inside the 200 GB Always Free total. **Egress:** the
  first 10 TB a month is free (`B88327`).
- **Against the €250 credit:** the worst case is €118 (47 %). The credit already spent is UNKNOWN (U5).
- **Region:** the home region. **Image:** Canonical Ubuntu 24.04 x86_64. **k3s and CNI:** `v1.34.6+k3s1` and
  flannel, from `bootstrap.sh` at `v0.3.0`.

### Agent access to the VM

| Item | Proposal |
|---|---|
| Network | One VCN and one public subnet. Security-list ingress: **TCP 22 from the owner's public IP /32 only**; the default `0.0.0.0/0:22` rule is removed. No 6443, 80, 443 or NodePort ingress. Egress open |
| Public address | One ephemeral public IP (free); no reserved IP, no load balancer |
| OS users | The owner's own user (sudo) runs `bootstrap.sh`. **`nexus-agent`** has no sudo, belongs to `adm` (the audit log is `root:adm` 0640, `bootstrap.sh:228-234`), and uses key-only SSH |
| SSH key | The owner generates an ed25519 key for this VM only. The private half goes to `~/.ssh/nexus-x86` (0600) on WSL2. The owner gives the host-key fingerprint; the agent connects with `StrictHostKeyChecking yes` and `IdentitiesOnly yes`, and never prints a key |
| Kubeconfig | The owner copies `/etc/rancher/k3s/k3s.yaml` to `/home/nexus-agent/.kube/config` (0600, `https://127.0.0.1:6443`, context `default`). It stays on the VM, and the agent's `kubectl` runs there over SSH |
| OCI | **No OCI credential for the agent**: no CLI config, no API key, no instance principal |
| Scripts | Copied from the named `dev` commit (`git archive <sha> experiments/ \| ssh … tar x`); `sha256sum -c` on the VM before every run; runs are detached (`setsid`) |
| Evidence | On the VM in `~nexus-agent/nexus-evidence/m2-1b/` (0700); copied to WSL2 `~/nexus-evidence/m2-1b/` at every gate with a sha256 manifest |

- **Rejected alternative:** OCI Bastion. Sessions last at most 3 h and are created in the console.
- **The deny list does not see remote commands.** `ssh … kubectl apply …` matches no `kubectl apply` rule. On the
  VM the agent runs only the harness from the named commit and read-only `kubectl`. Adding `ssh`-wrapped deny
  patterns is the owner's decision (ADR-021).

### Autostart handling (PR1, decided at the plan gate)

| Option | Effect | Verdict |
|---|---|---|
| (A) Keep `--autostart --users=160`, later 2B | Most faithful. But B = 80 traffic (WSL2's B) starts when the bootstrap ends, before R1, and again after any reboot | — |
| **(B) Drop `--autostart`**; `--users=` set to 2B by the values commit | Locust idles until a timed run starts it. Every M1b timed-run script starts the swarm itself: `ramp.py` stops first and starts from stopped (#118), `cbrun.py` sends `/stop` and then a fresh `/swarm`, `s5a2.py` sends `/swarm`. Only M1b's S5 leaned on autostart, after a reboot, a recorded deviation | **Proposed** |

The diff is one deleted line (`platform/load/master.yaml:62` at `v0.3.0`, `- --autostart`). It is part of the
branches the VM is bootstrapped from, so it is decided at the plan gate.

### The allow-list generator (reviewed at the plan gate) and the diff check (PR2)

**`experiments/replication/gen_allow.py`** is what gets reviewed. Its output, `allow.tsv`, is committed, and a test
regenerates it and requires byte equality.

1. It resolves `v0.3.0^{}` and requires `86cea844dff5dac8cbd3f61f57a3ffc94aac2838`.
2. It collects every hit of three `git grep -n` runs at that commit:
   - (A) `origin/main`, all paths;
   - (B) `experiment/dev-state`, all paths;
   - (C) `-w main` in the paths the VM executes or ArgoCD reads: `platform/`, `apps/`, `overlays/`, `operator/k8s/`,
     `scripts/bootstrap.sh`, `scripts/verify-state.sh`, `scripts/lib/`.
3. **Each hit is classified by an ordered rule table.** The first matching rule wins, and **a hit that matches no
   rule makes the generator fail**:

| # | Rule (path, line) | Action | Reason |
|---|---|---|---|
| K1 | Outside the executed paths in step 2 (C), or `scripts/capture-state.sh`, or `*/tests/fixtures/*` | keep | not run or read on the VM |
| K2 | A comment line (first non-blank character `#`) | keep | no effect |
| K3 | `\bmain\b` as a jq or shell variable: `--arg main`, `$main`, `local main`, `main=$(` | keep | a name, not a ref |
| K4 | `apps/sample-api/` Python and the Dockerfile (`main.py`, `main:app`, `import main`) | keep | the module name |
| S1 | `targetRevision: main$` under `platform/argocd/` | substitute `main` → `replication/x86` | the tracked branch |
| S2 | `targetRevision: experiment/dev-state` under `platform/argocd/` | substitute → `replication/x86-dev-state` | the tracked branch |
| S3 | `git fetch origin main experiment/dev-state` (including inside `printf` and `echo` strings) | substitute both names | the fetched branches |
| S4 | `"nexus-prod main prod"` (`verify-state.sh`) | substitute `main` → `replication/x86` | the branch read for prod |
| S5 | any other `origin/main` or `experiment/dev-state` token in an executed line | substitute `origin/main` → `origin/replication/x86`, `experiment/dev-state` → `replication/x86-dev-state` | the reads in report c |

4. **Output:**
   - `allow.tsv`: path, line number at `v0.3.0`, the exact old text, the exact new text, and the rule id;
   - a keep report: every kept hit with its rule.
5. **`--apply`** writes the substituted files, so the ref commit's content is the generator's output, not a hand
   edit.

On `v0.3.0` the rules are expected to give the 38 lines of report c. The run's own output is the authority.

**The diff check, `experiments/replication/diffcheck.py`,** reads `allow.tsv` plus three hand-listed entries, the only
lines outside the generator:
- `param` `apps/sample-api/k8s/base/deployment.yaml:66` `cpu: 1000m` → `^              cpu: [1-9][0-9]*m$`;
- `param` `apps/dependency-db/statefulset.yaml:71` `cpu: 2000m` → the same pattern;
- `param` `platform/load/master.yaml:63` `- --users=160` → `^            - --users=[1-9][0-9]*$`;
- `delete` `platform/load/master.yaml:62` `- --autostart`.

Its checks:
1. `v0.3.0` resolves to `86cea844…`, and the branch descends from it.
2. `git diff --raw --no-renames` shows only `M`, mode 100644.
3. `git diff -U0` is parsed hunk by hunk. Every removed line is keyed by (path, old line number, exact old text) and
   pairs with exactly the allowed new text, pattern, or deletion.
4. The output is PASS, or FAIL with every offending line; exit 0, 1 or 2.

**Fixtures.** Each builds a throwaway repository with a base commit tagged `v0.3.0` and the expected SHA injected:

| # | Branch content | Expect |
|---|---|---|
| F1 | identical to the base | PASS |
| F2 | every `allow.tsv` line applied | PASS |
| F3 | `param` lines with new values | PASS |
| F4 | `--autostart` deleted | PASS |
| F5 | an image digest changed in an overlay | FAIL |
| F6 | a `ref` line pointing at another branch name | FAIL |
| F7 | a chart `targetRevision` changed | FAIL |
| F8 | the memory limit next to an allowed CPU line | FAIL |
| F9 | an allowed line plus one extra added line | FAIL |
| F10 | a file added | FAIL |
| F11 | a file deleted | FAIL |
| F12 | a file renamed | FAIL |
| F13 | a mode change | FAIL |
| F14 | trailing whitespace on an allowed line | FAIL |
| F15 | CRLF on an allowed line | FAIL |
| F16 | a branch not descended from the base | FAIL |
| F17 | the base tag moved | FAIL |

**The generator's own tests:**

| # | Test | Expect |
|---|---|---|
| G1 | An unclassified hit | fail |
| G2 | A comment containing `origin/main` | kept |
| G3 | `--arg main` | kept |
| G4 | A chart `targetRevision: 3.8.0` | not matched by any S-rule |
| G5 | The committed `allow.tsv` | equals the regenerated one at `v0.3.0` |

**When it runs:** in `repo-checks`; before every push to either branch; at every (b) gate on both remote heads.

### Branches and protection (PR3)

1. **After the harness PR merges** (before the bootstrap and the pre-registration):
   - `git switch -c replication/x86 86cea844dff5dac8cbd3f61f57a3ffc94aac2838`;
   - commit 1, `chore(replication): track the replication branches`, is `gen_allow.py --apply` output only;
   - commit 2, `chore(replication): Locust idles until a timed run starts it`, holds PR1 (B).
2. `git switch -c replication/x86-dev-state replication/x86` (report e).
3. The diff check passes on both. Then two approved pushes.
4. **The owner, in GitHub (Settings → Rules):**
   - a ruleset on `refs/heads/replication/x86`: **Restrict updates** (lock), **Restrict deletions**, **Block force
     pushes**, no bypass;
   - a ruleset on `refs/heads/replication/x86-dev-state`: **Restrict deletions** and **Block force pushes**, no
     bypass, like ruleset 23998158.
5. **The agent checks:**
   - `git ls-remote origin 'refs/heads/replication/*'` equals the pushed heads;
   - `gh api repos/koussayx8/nexus-platform/rules/branches/replication/x86` (an `ask` call) lists `update`,
     `deletion` and `non_fast_forward`.
6. **A values commit: the only reason the lock lifts.**
   1. `derive_values.py` (PR7) runs on the recorded R1 outputs on the VM. Its output (the three values, the patched
      lines, and the sha256 of its inputs) goes into the evidence.
   2. The agent writes the **named values commit** `chore(replication): values V<n> from derive_values.py <output
      sha256>`. Its content is the output applied by the script, nothing else. The diff check passes locally, and the
      agent reports the commit's full SHA at the gate.
   3. The owner disables the lock. The agent pushes that commit to `replication/x86` (approved).
   4. The agent merges it into `replication/x86-dev-state` **with a merge commit** (`git merge --no-ff`, message
      `chore(replication): merge values V<n> into replication/x86-dev-state`). It checks that the merge commit's
      tree equals V<n>'s tree, then pushes (approved).
   5. **The diff check runs on both remote heads.**
   6. Only then does the owner restore the lock. The agent re-reads the rules and the heads.
   - At most two values commits: V1 after R1 attempt 1; V2 only after an R1 attempt 2 (PR4).
7. **What lands on `replication/x86-dev-state`:** merge commits of values commits, and nothing else.
   - S5 is injected inside the database (spec §20; ADR-025 addendum), so no inject or reset commit exists.
   - `nexus-dev` stays at L0, as in M1b's S5.

### The values derivation (PR7)

`experiments/replication/derive_values.py` reads the recorded R1 outputs (`ramp/verdict.json`, `ramp/steps.csv`, the
R1 result) and writes the values. **No value is chosen by hand.**

| Value | Formula | Source |
|---|---|---|
| B | `floor(min(0.4 × C, S* / 2))`, with C = `verdict.json` `capacity` and S\* = the highest passing step with DB CPU < 0.25 cores, working set < 410 MiB and 0 `db_slots_exhausted` | ADR-026, `attempt2/result.txt` |
| `--users` | 2 × B | ADR-026 (one user = 1 req/s, two equal classes) |
| sample-api CPU limit | 1000m × the maximum `container_processes{container="sample-api"}` over R1 (one GIL-bound process gives 1000m) | ADR-027 |
| DB CPU limit | `ceil(20 × c_max / 0.1 s + bg)` whole cores, where: 20 = the DB slots (5 per pod × 2 pods × 2 namespaces); c_max = the largest gross per-`/items` DB CPU cost over R1 steps with `/items` ≥ 10 req/s, step DB CPU ÷ `/items` req/s (ADR-028's definition: 0.241 cores ÷ about 32/s = 7.52 ms); bg = the DB CPU over the 5 min before R1 with Locust stopped | ADR-028 (20 × 7.5 ms per 100 ms = 1.5 cores, + 0.02 background, rounded up to 2) |

- If both limits equal `v0.3.0`'s (1000m, 2000m), V1 carries only `--users`, and there is no R1 attempt 2.
- **Three inputs were not recorded by M1b's R1:**
  - `container_processes` for sample-api;
  - `/items` req/s per step;
  - the idle DB CPU before R1.

  On x86 they are recorded as extra output only, by `ramp.py`'s per-step counters and the wrapper's start snapshot
  (DEV-3).
- **The DB cost on x86 comes from R1 steps only.** ADR-028's 7.5 ms came from a checkpoint B window at the 500m
  limit (50 % throttled; `cb-run3-repeat-20261013T1133Z/NOTE-2026-10-13-db-cost.md`). R1 at 6.26 ms was barely
  throttled. On x86, R1 runs at `v0.3.0`'s 2000m, so its c_max may be lower than ADR-028's.

**Acceptance test (in the harness PR, `test_derive_values.py`).** It must pass before the harness PR merges.

| # | Fed | Must give | Inputs, and what is missing |
|---|---|---|---|
| AT1 | M1b's recorded R1 attempt-2 outputs: `ramp/verdict.json` (`eaf4149c…`), `ramp/steps.csv` (`1ea5a94f…`), and the per-step `slots_exh` column of `result.txt` (`fb476200…`) | C = 200, S\* = 200, **B = 80**, **users = 160** | All present. **Named:** the per-step `db_slots_exhausted` counts come from `result.txt`, not from the logs, which are gone (report j). They are cross-checked by `steps.csv`'s `items_failures` = 0 at every step (see "What depends on the log-derived columns") |
| AT2 | ADR-027's input: `container_processes` = 1 for sample-api | **1000m** | **Missing as a recorded file:** ADR-027 reports "`container_processes` = 1 in all four pods" (read 2026-10-08); no series export or evidence file holds it. It is a fixture typed from the ADR, cited by line |
| AT3 | ADR-028's inputs: 20 slots; c_max = 7.5 ms; bg = 0.02 cores | `ceil(20 × 0.0075 / 0.1 + 0.02)` = `ceil(1.52)` = 2 → **2000m** | **Missing as recorded per-step inputs:** the `/items` req/s behind 7.5 ms (the note says "about 32 `/items`/s"), and the window and query behind "about 0.02 cores at idle". Both are fixtures typed from ADR-028 and the 2026-10-13 note, cited by line |
| AT4 | Boundaries: the sum exactly a whole number of cores (no extra core); c_max from a step with `/items` < 10 req/s (excluded); `capacity` null (no values, exit non-zero) | as stated | synthetic |

- AT2 and AT3 test the formulas against the decisions ADR-027 and ADR-028 recorded. They do not re-derive those
  ADRs from raw data, because that data was not recorded per step.

### Attempts (PR4): fixed in advance

| Step | Valid attempts | Repeats after a void |
|---|---|---|
| R1 | 1. A second only if V1 changes a limit; then C and B come from it (V2) | 1 per attempt |
| Checkpoint B | 1 | 1 |
| Run 3 | 1, only if checkpoint B passes (as in M1b) | 1 |
| S5 | 1, whatever checkpoint B's outcome (as in M1b) | 1 |

- A second void ends the step as **not done**.
- No fix between attempts: a failure is reported, never tuned (ADR-030 Consequences).
- At most 5 valid runs and 10 runs in all.

### Rules (PR5): M1b's, unchanged except where marked

**Void set, every timed run** (ADR-027 checkpoint A list; the raw missed-evaluation counter, ADR-027 R1 attempt 2):
1. `boot_id`, or k3s `MainPID` or `NRestarts`, changes.
2. A container restart or a new pod uid on the measured path.
3. An Application revision changes, or a `verify-state.sh` run overlaps the window.
4. More than 3 missed `nexus-detection` evaluations, by the raw counter.
5. Drift-corrected D_c > 90 s, with |r| ≤ 0.05 at both ends.
6. The host-speed canary sustained above +20 %. During R1, only while the worker uses under 0.5 core.
7. Locust outside ±10 % of B in either namespace.
8. Heavy local work. On the VM only the harness runs.
9. A dashboard, a UI, or another port-forward open.
10. **New, the shape check:** `nproc`, `MemTotal` or the CPU model differs from the pre-registered values.

**Pass rules per step:**

| Step | Rule | Source |
|---|---|---|
| R1 | `ramp.py` as in attempt 2: `--namespace nexus-dev --classes DevUser --start 10 --step 10 --max 200 --settle 60 --measure 120 --spawn-rate 5 --p95-factor 2 --fail-max 0.01 --achieved-min 0.95 --throttle-max 0.10 --rate-diff-max 0.05`. C, S\* and B by PR7 | ADR-026; `r1-rerun/start-record.md` |
| Checkpoint B | Precondition: `cpu.max` at both levels, for sample-api and the DB. 20-min window at B. **Gated:** A1 ≤ 3 % per namespace; A2, every pod ≤ 0.5 % at both levels; D1 ≤ 0.5 % at both levels; D2 ≤ 12. **Recorded:** A3, A4 | ADR-027, ADR-028 |
| Run 3 | 60 min at B: zero Nexus alerts, and the ADR-020 DB criterion (absolute, < 0.25 cores). Reported regardless | ADR-026, ADR-028 |
| S5 | The M1b pre-fault check. Injection exactly per §20 on `app_dev` by the owner. **Pass:** `NexusErrorRateAnomaly` fires for `nexus-dev`, and exactly one Incident created after t0 ends `Recorded` / `level_observe`; DB uid and restartCount unchanged after the reset. The first-fire time is recorded against +105 s (promtool) and +119.5 s (WSL2) | M1b-9 plan; ADR-025 addendum |

**Pre-registered deviations from M1b:**

| # | Deviation | Reason |
|---|---|---|
| **DEV-1** | **Checkpoint A is not replicated** | Checkpoint A (ADR-027) ran at the old B after the 200m → 1000m roll, to gate R1's re-run. A fresh x86 host has no B before R1, and `v0.3.0` already carries 1000m and 2000m, so its trigger does not exist there. Its precondition (`cpu.max` at both levels) is part of checkpoint B (`cbrun.py`). Its scripts are also not recoverable (report b) |
| DEV-2 | Locust idles by default (PR1) | Report and Autostart |
| DEV-3 | `B` and `USERS` set from PR7 in the timed-run scripts; offset-safe timestamps in `s5c.py`. Record-only additions for PR7's inputs: sample-api `container_processes` and `/items` req/s per R1 step, and the idle DB CPU in the R1 start snapshot | The harness (below), Values |
| DEV-4 | Conditional: the R1 wrapper is rebuilt rather than replayed, if the replay fails | Replay |

- **R1's `--max 200` stays.** If x86 also reaches 200, C is again a lower bound. That is a result.
- **The result** is a per-step table, M1b's verdict next to x86's. There is no single verdict for the replication.

### Replay of the rebuilt R1 scripts: the pass rule, fixed before any replay

**Frozen inputs** (sha256, read 2026-10-16):

| Input | sha256 |
|---|---|
| Export `MANIFEST.json` (report j) | `8b8e6938e093ef060d8e0ca8b841932f2dca7a921c084917a40c1ca957ad6596` |
| `attempt2/r1-run.jsonl` | `6ae7cfd1558c337851d2b128a76441ba701397091838fbe539e726d68294e562` |
| `attempt2/ramp/steps.csv` | `1ea5a94f5df85b932fe9f30ee3b0cfb37a7121f2a86a6e4ef6cfff0b62d44e78` |
| `attempt2/ramp/verdict.json` | `eaf4149c4d47155b7103622ac1b060adbf115e4f88d905e561858513cd4aff21` |
| `attempt2/result.txt` (the expected output) | `fb4762002b05f074b11c29184bf6d3eedc6ee278fe35380717eb6743a93e4e88` |
| Rebuilt `r1a2/r1analyze.py`, `r1a2/r1run.py`, `r1a2/common.py` | `dae4de71c47434a891dd55b54074fa8182ad02b8703655e5e9bd1c7524d73cbb`, `23399c725d5ee568523bfa35eae28aeff31770f3e6b715fe28d0af7b238a161e`, `1850660018f1149a7732d57dba0066ce9cb35c527d4a8a121e18e823637d3bc8` |

**Method.**
- The rebuilt `r1analyze.py` runs unchanged. A replay `common.py` differs from the rebuilt one in three places only,
  shown by diff:
  - `q()` answers from the export;
  - `kc()` returns no log lines and fails on anything else;
  - `EV` points to a read-only copy of `attempt2/`.
- `q()` evaluates the four query forms used (an instant selector with a 5-min lookback, `increase`, `rate` and
  `sum by (pod)`) over the exported raw samples, with Prometheus's extrapolation.
- `ramp.py knee` (blob `64aba469…`) runs on `steps.csv` with attempt 2's thresholds.
- **One replay.** A repeat is allowed only for a defect in the replay `common.py`, shown by its diff and by a test
  on the export. Never for a change to the rebuilt scripts.

**Pass rule.** Every item below must hold. The text compared is stdout against `result.txt`, line by line.

| # | Item | Required |
|---|---|---|
| E1 | `ramp.py knee steps.csv --p95-factor 2 --fail-max 0.01 --achieved-min 0.95 --throttle-max 0.10` | Its JSON equals `verdict.json` field by field: **each step's verdict** (`last_ok`, `knee_target`, `unjudged_target`, `reasons`, `flags`), **the knee**, **C** (`capacity` 200, `capacity_lower_bound`), `baseline` |
| E2 | The validity block: rules 1, 2, 4, 5, 7 and 10; rule 3's raw counter line (`4721.0 -> 4724.0`) and its `3.0` line with D, D_c and r; `ramp exit`; `VALIDITY: VALID` | **exact** |
| E3 | The `verdict:` line | **exact** |
| E4 | Every table cell that comes from `steps.csv`: step, swarm_ok, selected and other requests, rates, agreement, p95, failures, throttle, DB CPU, working set, worker columns | **exact** |
| E5 | The S\* qualification of every step, and the `C =`, `S* =` and `B = … = 80; binding term: 0.4 C` lines | **exact** |
| T1 | "users per pod (dev)": each value, with pod names equal | within **±0.1 user** of the recorded value, one unit in its printed place |
| T2 | "/items 50–100 per pod (dev)": each value, with pod names equal | within **±0.1 percentage point**, one unit in its printed place |
| N1 | `slots_exh` and `db_unavail` | **Untested.** The log input is gone (report j). The replay prints 0, the record says 0, and the result says "untested", not "match". The cross-check below is reported next to it |
| W1 | `r1run.py` (it orchestrates live, so it cannot be re-run): every record type in `r1-run.jsonl` (`start_snapshot`, `refb`, `ramp_started`, `minute`, `ramp_exit`, `end_a`, `end_b`) has exactly the key set that the rebuilt script's `w({...})` calls write | **exact** (a structural check, stated as such) |
| W2 | `r1run.py`'s `MERGE`, `BLOB` and `LOCUST_DIGEST` | equal `start-record.md`'s values |

**What depends on the log-derived columns** (`slots_exh` and `db_unavail`; read from `ramp.py` and the rebuilt
`r1analyze.py`):
- **Step verdicts, the knee and C: none.**
  - `ramp.py knee` reads only `steps.csv`'s `FIELDS` (`ramp.py:103-131`), and none of them comes from the logs.
  - An `/items` 503 reaches the knee through Locust's `items_failures`. The logs only name its cause
    (`ramp.py:51-52`).
- **S\*, and through it B: yes, `slots_exh` only.**
  - `r1analyze.py:103` qualifies a step for S\* only if `slots == 0`.
  - For attempt 2 the dependency cannot change B. B = floor(min(0.4 × 200, S\*/2)) stays 80 while S\* ≥ 160, and
    `items_failures` = 0 at every step, 150–200 included.
  - sample-api answers every `db_slots_exhausted` with a 503 (M1 plan, report f; ADR-020), so a non-zero `slots_exh`
    would show as a non-zero `items_failures`.
- **`db_unavail`:** it feeds only the binding-resource read-out, which prints only when a knee exists. Attempt 2 has
  none, so it affects no value.

- **All pass:** the rebuilt scripts are recorded as "VERIFIED by replay; log-derived columns untested". The x86 R1
  uses them, adapted only as DEV-3 allows.
- **Any fail:** U1 stays UNKNOWN, DEV-4 applies, and the x86 R1 result is labelled "wrapper rebuilt".

### The harness on `dev` (M2-1b.1)

- **`experiments/timed-runs/m1b/`** holds verbatim copies of the VERIFIED scripts, with `SHA256SUMS` and
  `PROVENANCE.md` (report b). It is never edited, and `repo-checks` runs `sha256sum -c`:
  - `checkpoint-b/`: `cbrun.py`, `common.py`, `stop.py`;
  - `s5/`: `s5a2.py`, `s5b.py`, `s5c.py`, `s5c-analysis.py`, `common.py`;
  - `clock/`: `clocksample3.py`, `clockanalyze3.py`;
  - `r1/`: the rebuilt `r1run.py`, `r1analyze.py` and `common.py`, with `rebuild_r1.py`, the replay `common.py` and
    the replay record.
- **`experiments/timed-runs/x86/`** is derived from `m1b/`. The diff check's tree mode compares the two against a
  harness allow list that permits only DEV-3:
  - `B` and `USERS` read from the command line;
  - `s5c.py`'s `ts9` replaced by `s5c-analysis.py`'s offset-safe parser, with a unit test for `Z` and `+02:00`;
  - in `r1run.py`, the provenance constants and the owner-name strings, and the idle DB CPU read in the start
    snapshot (record-only).
- **`ramp.py`'s per-step counters** (`db_slots_exhausted`, `db_unavailable`, sample-api `container_processes`,
  `/items` req/s) are added as extra output only. The verdict code is unchanged, `test_ramp.py`
  passes unchanged, and a new test checks that `ramp.py knee` on attempt 2's `steps.csv` gives attempt 2's
  `verdict.json` (E1, kept as a regression test).
- Also in this PR: `gen_allow.py`, `allow.tsv`, `diffcheck.py`, `derive_values.py`, their tests.

### The two named commits (PR6)

| # | Commit | What it names |
|---|---|---|
| 1 | **The system under test**: the full SHA of `replication/x86`'s head that the VM was bootstrapped from (commits 1–2, PR3) | `v0.3.0` plus the generated ref lines and PR1. Each values commit V<n> and its merge into `-dev-state` is named at its own gate, with the `derive_values.py` output and the diff-check output |
| 2 | **The harness**: the full SHA of the `dev` merge commit holding `experiments/timed-runs/`, `ramp.py`, the generator, the diff check and `derive_values.py` | The VM copy is checked against its `SHA256SUMS` before every run |

The pre-registration also records:
- `v0.3.0^{}` = `86cea844…`;
- the host facts;
- `allow.tsv`'s sha256;
- PR1–PR7, DEV-1 to DEV-4;
- the schedule.

## Phases and gates

| Step | Content | Cluster effect | Gate |
|---|---|---|---|
| **M2-1b.0** | This plan (PR to `dev`) | none | **M2-1b plan gate**: approach, PR1, the generator rules, the replay rule |
| **M2-1b.1** | The harness PR: verbatim and derived scripts, the R1 replay and its record, the generator, the diff check, `derive_values.py`, tests | none | merge (typed) |
| **M2-1b.2** | Branches, pushes, rulesets; the owner's VM (by 10-20) and bootstrap (after M2-1b.1); host facts | x86 VM (owner) | — |
| **M2-1b.3** | Pre-registration PR: PR1–PR7, DEV-1 to DEV-4, host facts, both commits | none | **GATE M2-1b, pre-registration** (decision) |
| **M2-1b.4** | R1 → `derive_values.py` → V1 (→ R1 attempt 2 → V2) → calibrated by 2026-11-01 | x86 VM | values gates |
| **M2-1b.5** | Checkpoint B → run 3 → S5 → evidence → result | x86 VM | **GATE M2-1b, result** |
| **M2-1b.6** | Deletion by 2026-11-08 (owner) | VM gone | confirmed in the result |

## Verification

- **Offline:**
  - the generator's tests (G1–G5) and the diff check's (F1–F17);
  - `sha256sum -c` on `m1b/`, and the tree diff check of `x86/` against `m1b/`;
  - the parser test, `test_ramp.py` with the E1 regression, and the `derive_values.py` tests;
  - the R1 replay under the fixed rule; `repo-checks.sh`.
- **On the VM:** `verify-state.sh` exits 0 against the replication branches; the diff check on both remote heads;
  `sha256sum -c` and the shape facts before every run.
- **The replication itself:** the pass and void rules above, reported per step.

## UNKNOWNs (each with what settles it)

| # | UNKNOWN | Settled by |
|---|---|---|
| U1 | The rebuilt R1 wrapper and `r1analyze.py` | The replay under the fixed rule (M2-1b.1); otherwise DEV-4 |
| U2 | Checkpoint A's orchestrator | Not needed (DEV-1) |
| U3 | The clean-window monitors | Not needed (superseded) |
| U4 | The trial's service limit for `VM.Standard3.Flex` (and E4, E5) at 6 OCPUs | Owner, console, by 2026-10-19 |
| U5 | The credit left of €250 | Owner, console, by 2026-10-19 |
| U6 | That `Standard3.Flex` is billed as "Standard - X9" | Owner, the create page's estimate, by 2026-10-20 |
| U7 | The owner's public IP: fixed or dynamic | Owner, by 2026-10-19 |
| U8 | k3s's cgroup layout on the VM (`kubepods.slice`), which `cbrun.py`'s precondition reads | Read on the bootstrapped VM; a **pre-registration input** |
| U9 | Whether the image's host `iptables` rules block k3s's node-local traffic | Owner at bootstrap (`sudo iptables -S`), then `verify-state.sh`; a **pre-registration input** |
| U10 | The VM's `MemTotal` at 16 GB | Read on the VM; a **pre-registration input** |

## Assumptions (open ones only)

- `bootstrap.sh` at `v0.3.0`, with the generated ref lines, bootstraps a fresh Ubuntu 24.04 x86 VM without the
  M2-3 fixes. The known gaps: the operator restarts until step g, and `observability` can exceed its timeout
  (`NEXUS_WAIT_TIMEOUT_OBSERVABILITY=1800`).
- The amd64 images pinned at `v0.3.0` are still pullable.
- The replay's re-implementation of `rate`/`increase` matches Prometheus within T1 and T2's tolerances.

## Risks

- **The path to the pre-registration has no slack** (10-21 → 10-24). A late harness merge eats into the calibration
  window, which has 2 days of slack in the worst case.
- **The VM bills two days before it is used** (from 10-20), about €12.
- **Two clusters, one repository.** WSL2 tracks `main` while the VM tracks `replication/*`. The generator, the diff
  check and `verify-state.sh` on the VM guard the ref lines.
- **Until the owner's rulesets are on,** nothing in GitHub stops a push to the replication branches.
- **Remote commands bypass the deny list** (Access).
- **N1 is untested:** the replay cannot check the log-derived columns.
- **Cost:** the worst case is €118. The real charge risk is an upgrade to Pay As You Go, which nobody makes.
- **A1 and x86 share one account.** The owner names the deleted instance in the result.
