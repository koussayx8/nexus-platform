> **Draft for the M2-1b plan gate (2026-10-16), rev 1. Frozen at approval; current state lives in TASKS.md.**
> Line references are at `dev` `a9dbfb38403004e3cd1f1f600d580bbd16b4212d` or at tag `v0.3.0`, as marked. Prices were
> read from Oracle's public price list API on 2026-10-16T09:11Z.

# M2-1b plan: ADR-030 (b), the M1b protocol replicated on an OCI x86 trial VM (rev 1)

## Context

ADR-030 (b): replicate the M1b protocol on one x86 Flex VM paid from the OCI trial credit, running `v0.3.0`
(`86cea844dff5dac8cbd3f61f57a3ffc94aac2838`). The protocol is:
1. the R1 ramp;
2. C, B and the sample-api and DB limits, re-derived on that host;
3. the clean-baseline checkpoints;
4. the S5 run.

**Fixed at GATE M2-0** (M2 plan, Decisions):
- The VM runs `v0.3.0` plus a pre-registered, parameter-only diff: re-derived limits, Locust users and autostart.
- Two branches, `replication/x86` and `replication/x86-dev-state`; a diff check that accepts only the parameter
  paths and the ref lines.
- The scripts run on the VM, as in M1b's single-host topology.
- Two named commits.
- **Stop rule:** not calibrated by 2026-11-01 → (b) is recorded as not done.
- **Billing guard:** every trial resource is deleted before 2026-11-09.

This plan proposes everything the pre-registration gate fixes. It starts nothing.

## Decisions requested

**At this plan gate:** the approach, the schedule, the shape, the access model, and the work on `dev` before the
pre-registration (Phases, M2-1b.1).

**At the pre-registration gate (GATE M2-1b, decision):** proposals PR1–PR6, frozen there.

| # | Pre-registration item | Proposal (section) |
|---|---|---|
| PR1 | Autostart handling | Drop `--autostart`; Locust idles until a timed run starts it (Autostart) |
| PR2 | The diff-check script and its fixtures | `experiments/replication/diffcheck.py`, an exact allow-list, 17 fixtures (Diff check) |
| PR3 | Branch and protection steps | `replication/x86-dev-state` is cut from `replication/x86`'s head, not from the tag. The lock is lifted only at the named parameter gates (Branches) |
| PR4 | The attempt count | One valid attempt per step, R1 at most two; one repeat after a void (Attempts) |
| PR5 | The pass and void rules | M1b's rules unchanged, plus a shape check (Rules) |
| PR6 | The two named commits | The replication head, and the `dev` harness commit (Named commits) |

## Gate reports a–i (commands run this session, read-only)

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
- **mtime.** The file's mtime (UTC, `find -printf %T`) is before the run's first `run.jsonl` record, and later than
  nothing the run wrote. This is the strongest evidence left for an entry script run as `__main__`. A deliberate
  `touch` would defeat it; nothing suggests one.
- **Patch.** `patch -o` of the earlier version with the bundle's own diff reproduces the run version byte for byte
  (`cmp`).
- **Traceback.** `s5c.err`'s traceback quotes lines 7 and 71, and today's `s5c.py` has the same text on those lines.

| Step (M1b record) | Script, sha256 prefix | Evidence | Status |
|---|---|---|---|
| R1 attempt 2 (VALID, C ≥ 200, B = 80) | `experiments/calibration/ramp.py`, blob `64aba469…` | `r1-rerun/start-record.md`: the run used `dev` `2761a9a9…`, blob `64aba469e44c…`. `git rev-parse` gives the same blob at `2761a9a`, `v0.3.0` and `dev`; nothing has touched it since | **VERIFIED** |
| R1 attempt 2 | The run wrapper (writes `r1-run.jsonl`: snapshots, the void set) and `r1analyze.py` (S\*, the per-step table, `result.txt`) | The session `702f3821` scratchpad is gone. Its transcript holds the heredocs and the in-place patches up to the launch at 2026-10-09T17:54:15Z | **UNKNOWN** (reconstructable, not verified) |
| Checkpoint A (VALID, PASS) | The orchestrator and `analyze.py` | The session `5543b716` scratchpad is gone; it is in the transcript (launch 2026-10-09T09:27:22Z) | **UNKNOWN**. Proposed: not replicated (report i) |
| Clean windows 1 and 2 (200m era) | The monitors | The session `aeff76de` scratchpad is gone | **UNKNOWN**; not needed (superseded) |
| Checkpoint B, 2026-10-12 (VOID) | `cb-run3-20261012T0811Z/scripts/*` | Its `README.md`: "Reconstructed 2026-10-13 verbatim from the agent's session"; the originals were lost at a WSL restart | Reconstruction, **not** the run version; not used |
| Checkpoint B repeat, 2026-10-13 11:35Z (VALID, FAIL A1) | `cbrun.py` `6dc9ed674562`, `common.py` `00f82361479b` | `cbrun.py` mtime 11:33:59Z, before the first record at 11:35:02Z. `cbrun.orig.py` + `cbrun.repeat.diff` = `cbrun.py`. `common.py`: bytecode-equal, compiled at 11:34:12Z | **VERIFIED** |
| **Checkpoint B, ADR-028, 2026-10-13 13:24Z** (VALID, FAIL A1, A2, D1, D2) | `cbrun.py` `b5fb3ff717e8`, `common.py` `00f82361479b`, `clocksample3.py` `96cf06f9296b`, `clockanalyze3.py` `3ad1b4e4e435`; `stop.py` `553fa23d1a09` (post-run stop) | `cbrun.py` mtime 13:23:26Z, before the first record at 13:24:16Z. `cbrun.repeat.py` + `cbrun.adr028.diff` = `cbrun.py`. `common.py`: bytecode-equal, compiled at 13:23:35Z. The clock scripts are byte-identical in every bundle. `stop.py` mtime 15:56:54Z, after the run, as it should be | **VERIFIED** |
| **S5, 2026-10-14 06:22Z** (PASS, valid) | `s5a2.py` `041d57180c3a`, `s5b.py` `f70dcb16a017`, `s5c.py` `0ee921a9cace`, `common.py` `1410a749ca1f`, the same clock scripts; `s5c-analysis.py` `ac68affac15d` (the read-only rerun after the crash) | `s5a2.py` mtime 06:21:59Z, before the first record at 06:22:13Z. `common.py`: bytecode-equal, compiled at 06:22:11Z. `s5b`, `s5c` and the clock scripts are unchanged since 2026-10-13T18:12:42Z. The `s5c.py` traceback lines match. `s5c-analysis.py` mtime 06:47:55Z, after the crash, as recorded (ADR-025 addendum, deviation 2) | **VERIFIED** |

Two facts for the harness:
- **The two `common.py` files differ** (`00f82361…` for checkpoint B, `1410a749…` for S5), so each protocol keeps
  its own copy.
- **B is hard-coded:**
  - `cbrun.py:10` and `s5a2.py:8` set `B = 80.0; USERS = 160`;
  - `cbrun.py:20` and `:122` build the systemd cgroup path `kubepods.slice/kubepods-burstable.slice/…`;
  - no other host literal (`koussay`, `/home/azure`, the node IP) appears in these scripts (`grep -o`).

**c) The ref lines: CONTRADICTED. Gate report k listed too few.** M2-0's report k named `bootstrap.sh:151-162` and
`:439-446`. At `v0.3.0` (`git grep`), the branch reads that must move are:

| File (at `v0.3.0`) | Lines | What they read |
|---|---|---|
| `platform/argocd/root.yaml` | 24 | `targetRevision: main` |
| `platform/argocd/applications/{dependency-db,nexus,platform,sample-api-prod}.yaml` | 17, 17, 16, 17 | `targetRevision: main` |
| `platform/argocd/applications/kyverno.yaml` | 34 | `targetRevision: main` (the values source; line 28 is the chart version `3.8.0` and stays) |
| `platform/argocd/applications/observability.yaml` | 26, 30 | `targetRevision: main` (line 19 is the chart version `86.2.2` and stays) |
| `platform/argocd/applications/sample-api-dev.yaml` | 17 | `targetRevision: experiment/dev-state` |
| `scripts/bootstrap.sh` | 151–154, 157, 159, 161–162 | merge-order guard |
| `scripts/bootstrap.sh` | 439–441, 444–447 | AppProject and `root.yaml` from `origin/main` |
| `scripts/bootstrap.sh` | **493, 494, 497** | **the bootstrap templates from `origin/main`** (not in report k) |
| `scripts/bootstrap.sh` | **509, 522, 531, 532** | **step h's expected commits** (not in report k) |
| `scripts/verify-state.sh` | **95, 101, 133–136, 199, 380, 386, 387** | **the expected commits and the Git-pinned digests** (not in report k) |

- Not moved:
  - `scripts/capture-state.sh` (9 reads): not part of the protocol, so it is not run on the VM;
  - `scripts/lib/apps-stable.jq` (comments only);
  - `platform/namespaces/namespaces.yaml:2` (a comment).
- Without the `verify-state.sh` lines, `verify-state.sh` on the VM would compare the Applications with `origin/main`,
  which M2 moves, and fail for the wrong reason.

**d) Locking `replication/x86` right after the pre-registration commit conflicts with the parameters: CONTRADICTED.**
C, B and the limits are derived on the VM after R1. So the users line, and any limit that changes, can only be
committed after the lock. PR3 proposes how.

**e) Cutting both branches from the tag conflicts with `bootstrap.sh`'s guard: CONTRADICTED.**
- `bootstrap.sh:161-162` (`v0.3.0`) requires `origin/main` to be an ancestor of `origin/experiment/dev-state`; with
  the ref lines moved, `replication/x86` must be an ancestor of `replication/x86-dev-state`.
- Two branches each cut from `v0.3.0` with their own commits fail that guard, and the bootstrap stops.
- PR3 cuts `replication/x86-dev-state` from `replication/x86`'s head. Both still descend from `v0.3.0`.

**f) `experiment/dev-state` adds no deployed content at M1b: VERIFIED.**
- `git diff --quiet v0.3.0 origin/experiment/dev-state` → equal trees (`0ceb290e…`, the M1b forward-merge).
- At S5's dev-state `f480a6e7…`, `git diff --stat v0.3.0 f480a6e7 -- apps/ platform/` is empty. The 11 differing
  files are docs and the S5 pin-guard scripts.
- So cutting `replication/x86-dev-state` from `replication/x86` deploys what M1b's `sample-api-dev` ran.

**g) CI on the replication branches: VERIFIED, none.** At `v0.3.0`, `ci.yml` and `operator.yml` build on `push` to
`main` only. `repo-checks.yml` runs on `push` to `main` and `dev` and on pull requests to them. A push to
`replication/*` starts no workflow and builds no image. The images stay at `v0.3.0`'s amd64 digests.

**h) Prometheus still holds M1b's R1 and checkpoint A data, until about 2026-10-24: VERIFIED for today.**
- `prometheus_tsdb_lowest_timestamp_seconds` (service proxy, 09:15Z) → 2026-09-30T06:00Z.
- Retention is `15d` (`platform/observability/kube-prometheus-stack-values.yaml:41`).
- A replay of a reconstructed R1 analysis against live Prometheus (b) must finish **by 2026-10-23**. For checkpoint
  B (2026-10-13) the limit is about 2026-10-27, and for S5 (2026-10-14) about 2026-10-28.

**i) Checkpoint A has no equivalent on a fresh host: proposal.**
- Checkpoint A ran at the old B, after the 200m → 1000m roll, to gate the R1 re-run (ADR-027).
- A fresh x86 host has no B before R1, and `v0.3.0` is already at 1000m and 2000m. Its precondition (`cpu.max` at
  both levels) is part of checkpoint B (`cbrun.py`).
- Proposal: the x86 checkpoints are **checkpoint B and run 3**; checkpoint A is not replicated.

## Schedule

Worked back from **2026-11-01** (calibrated) and **2026-11-09** (every trial resource deleted). Every date is a
latest date. A step that misses its date is recorded as not done, and the deletion date overrides every other step.

| Latest date | Step | Who |
|---|---|---|
| 2026-10-16 Fri | This plan PR | agent |
| 2026-10-19 Mon | Plan gate. In the console: the trial's service limit for `VM.Standard3.Flex` (and E4 and E5 as fallbacks) at 6 OCPUs and 16 GB; the credit left; a budget alert at the first euro if not yet set; the A1 capacity check (M2-1) | **owner** |
| 2026-10-21 Wed | M2-1b.1 on `dev` (Phases): the harness PR, the diff check with fixtures, PR1's decision | agent; owner merges (typed) |
| **2026-10-23 Fri** | The R1 wrapper reconstruction and its replay check against live Prometheus (report h's deadline). Otherwise it stays UNKNOWN and PR5's fallback applies | agent |
| 2026-10-23 Fri | **GATE M2-1b, pre-registration** (decision): PR1–PR6, the shape, the path and ref-line lists, both named commits | **owner** |
| 2026-10-24 Sat | Branches cut, diff check PASS on both, pushed (approved) | agent |
| 2026-10-24 Sat | Lock `replication/x86` and protect `replication/x86-dev-state` (rulesets) | **owner** |
| 2026-10-24 Sat | Create the VM (Access); run `bootstrap.sh` from `replication/x86`; create the agent user, its key and its kubeconfig | **owner** |
| 2026-10-25 Sun | `verify-state.sh` exits 0 on the VM; shape facts read; harness copied to the VM and `sha256sum -c` passes | agent |
| 2026-10-27 Tue | R1 (one attempt plus a void repeat), then C, B, S\* and the limits derived; the parameter gate | agent; **owner** gate |
| 2026-10-29 Thu | Parameter commits: the lock is lifted, the diff check passes, the commit is pushed and fast-forwarded to `-dev-state`, and the lock goes back on. Rollout. R1 is re-run only if a limit changed | agent; **owner** lifts and restores the lock |
| **2026-11-01 Sun** | **Calibrated:** C, B and the limits live on both branches, `verify-state.sh` exits 0, the diff check passes. Otherwise (b) is recorded as not done, and the next step is the deletion | agent; **owner** gate |
| 2026-11-03 Tue | Checkpoint B (20-min warm-up at B), then run 3 (60 min) if it passes | agent |
| 2026-11-05 Thu | S5: the owner injects and restores (spec §20) | **owner** + agent |
| 2026-11-06 Fri | Evidence copied off the VM with a sha256 manifest; **GATE M2-1b, result** | agent; **owner** gate |
| **2026-11-08 Sun** | **Delete** the instance, its boot volume, any block volume, any reserved IP and any load balancer. Send a console screenshot of the empty lists; the agent confirms that SSH no longer answers | **owner** |
| 2026-11-09 Mon | The trial's last day. Nothing paid exists | — |

## Design

### The shape: as close as the trial allows to the WSL2 node

The WSL2 node: an Intel i7-9750H, 6 cores × 2 threads = 12 logical CPUs, `MemTotal` 15.54 GiB (report b of the M2-1
plan). On OCI x86, 1 OCPU = 1 core = 2 vCPUs, so **6 OCPUs = 12 vCPUs**.

| Shape (6 OCPU, 16 GB) | CPU | €/OCPU-h | €/GB-h | €/h | €/day | 15 days (10-24 → 11-08) | 24 days (worst case, to 11-09) |
|---|---|---|---|---|---|---|---|
| **`VM.Standard3.Flex`** (proposed) | Intel Xeon, SMT 2 (the same vendor and threading as the WSL2 host) | 0.0372 | 0.001395 | 0.2455 | 5.89 | **€88.4** | €141.4 |
| `VM.Standard.E4.Flex` (fallback 1) | AMD EPYC, SMT 2 | 0.02325 | 0.001395 | 0.1618 | 3.88 | €58.3 | €93.2 |
| `VM.Standard.E5.Flex` (fallback 2) | AMD EPYC, SMT 2 | 0.0279 | 0.00186 | 0.1972 | 4.73 | €71.0 | €113.6 |

- **Prices:** Oracle's public price list (`apexapps.oracle.com/…/products/?currencyCode=EUR`, read 2026-10-16T09:11Z).
  Parts: `B94176`/`B94177` "Standard - X9" for `Standard3.Flex` (that mapping is UNVERIFIED: the owner checks the
  console's estimate); `B93113`/`B93114` E4; `B97384`/`B97385` E5.
- **Memory:** 16 GB is the nearest Flex size to 15.54 GiB. The VM's `MemTotal` is read and recorded; a
  difference is a host fact, not a deviation.
- **Boot volume:** 50 GB, balanced.
  - Its storage is €0.023715 per GB-month plus 10 VPUs × €0.001581 per GB-month: about €2 a month, under €1 for
    the trial.
  - It is free inside the 200 GB Always Free total.
- **Egress:** the first 10 TB a month is free (`B88327`).
- **Against the €250 credit:** the worst case, every day to the deadline, is €141 (Standard3), 57 % of the credit.
  The credit already spent since 2026-10-11 is UNKNOWN (owner).
- **Region:** the home region, the same as A1 (one account, ADR-030).
- **Image:** Canonical Ubuntu 24.04 x86_64, the same OS release as WSL2.
- **k3s and CNI:** `v1.34.6+k3s1` and flannel, both set by `bootstrap.sh` at `v0.3.0` (ADR-030 (d)).

### Agent access to the VM

| Item | Proposal |
|---|---|
| Network | One VCN and one public subnet. Security-list ingress: **TCP 22 from the owner's public IP /32 only**; the default `0.0.0.0/0:22` rule is removed. No 6443, 80, 443 or NodePort ingress. Egress open (GitHub, ghcr.io, Docker Hub, the k3s install) |
| Public address | One ephemeral public IP (free); no reserved IP, no load balancer |
| OS users | The owner's own user (sudo) runs `bootstrap.sh`. **`nexus-agent`** has no sudo, belongs to `adm` (the audit log is `root:adm` 0640, `bootstrap.sh:228-234`), and uses key-only SSH |
| SSH key | The owner generates an ed25519 key for this VM only, installs the public half for `nexus-agent`, and places the private half at `~/.ssh/nexus-x86` (0600) on WSL2. The owner gives the host-key fingerprint from the console; the agent connects with `StrictHostKeyChecking yes` and `IdentitiesOnly yes`. The agent never prints a key |
| Kubeconfig | The owner copies `/etc/rancher/k3s/k3s.yaml` to `/home/nexus-agent/.kube/config` (0600, server `https://127.0.0.1:6443`, context `default`). It stays on the VM. The agent's `kubectl` runs on the VM over SSH, so the API server is never reachable from outside |
| OCI | **No OCI credential for the agent**: no CLI config, no API key, no instance principal. Every console action is the owner's |
| Scripts | The harness is copied from the named `dev` commit (`git archive <sha> experiments/ \| ssh … tar x`). `sha256sum -c` runs on the VM before every run. Runs are detached (`setsid`), so an SSH drop does not stop them |
| Evidence | Written on the VM to `~nexus-agent/nexus-evidence/m2-1b/` (0700); copied to WSL2 `~/nexus-evidence/m2-1b/` at every gate with a sha256 manifest |

- **Rejected alternative:** OCI Bastion (no public IP at all). Each session lasts at most 3 h and is created in the
  console, which is impractical across multi-hour runs.
- **The deny list does not see remote commands.** `.claude/settings.json` matches the outer command, so
  `ssh … kubectl apply …` matches no `kubectl apply` rule. On the VM the agent runs only the harness from the named
  commit and read-only `kubectl`. Adding `ssh`-wrapped deny patterns is the owner's decision (ADR-021 governs the
  list).

### Autostart handling (PR1)

| Option | Effect | Verdict |
|---|---|---|
| (A) Keep `v0.3.0`'s `--autostart --users=160`, later 2B | Most faithful. But B = 80 traffic (WSL2's B) starts as soon as the bootstrap ends, before R1, and again after any reboot | — |
| **(B) Drop `--autostart`** and set `--users=` to 2B after R1 | Locust idles until a timed run starts it. Every M1b timed-run script already starts the swarm itself: `ramp.py` stops first and starts from stopped (#118), `cbrun.py` sends `/stop` and then a fresh `/swarm`, and `s5a2.py` sends `/swarm`. Only M1b's S5 leaned on autostart, after a reboot, and that is recorded as a deviation | **Proposed** |

- The diff is one deleted line (`platform/load/master.yaml:62` at `v0.3.0`, `- --autostart`) plus the `--users` value
  (`:63`).
- After a reboot the swarm stays stopped, so a timed run cannot start on unattended traffic.

### The diff check (PR2)

`experiments/replication/diffcheck.py`, with `experiments/replication/allow.tsv` and `test_diffcheck.py`. It is
Python and `unittest`, as `experiments/calibration/` is, so `repo-checks` can run it offline.

**What it checks:**
1. `v0.3.0` resolves to `86cea844dff5dac8cbd3f61f57a3ffc94aac2838`. A moved tag fails.
2. The branch descends from it (`git merge-base --is-ancestor`).
3. `git diff --raw --no-renames` shows only `M` entries with mode 100644. Any `A`, `D`, `R`, `T` or mode change
   fails.
4. `git diff -U0 --no-renames --no-ext-diff --no-textconv` is parsed hunk by hunk.
   - Every removed line is keyed by (path, old line number, exact old text) and must be on the allow list.
   - Its added line must equal the entry's new text (`ref`), match its anchored regex (`param`), or be absent
     (`delete`).
   - The added and removed counts must pair exactly.
5. The output is PASS, or FAIL with every offending line. Exit 0, 1, or 2 on a usage error.

**The allow list** (`allow.tsv`, frozen at the pre-registration):
- **`ref`:** every line in report c, old text → the same text with `main` → `replication/x86` and
  `experiment/dev-state` → `replication/x86-dev-state`, spelled out line by line. No pattern, so a chart version
  can never match.
- **`param`:**
  - `apps/sample-api/k8s/base/deployment.yaml:66` `cpu: 1000m` → `^              cpu: [1-9][0-9]*m$`;
  - `apps/dependency-db/statefulset.yaml:71` `cpu: 2000m` → the same pattern;
  - `platform/load/master.yaml:63` `- --users=160` → `^            - --users=[1-9][0-9]*$`.
  - The memory limits and the requests are not on the list.
- **`delete`:** `platform/load/master.yaml:62` `- --autostart` (PR1 (B) only).

**Fixtures.** Each test builds a throwaway repository in a temp directory, with a base commit tagged `v0.3.0` and the
expected SHA injected:

| # | Branch content | Expect |
|---|---|---|
| F1 | identical to the base | PASS |
| F2 | every `ref` line moved | PASS |
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
| F13 | a mode change (`+x`) | FAIL |
| F14 | trailing whitespace on an allowed line | FAIL |
| F15 | CRLF on an allowed line | FAIL |
| F16 | a branch not descended from the base | FAIL |
| F17 | the base tag moved to another commit | FAIL |

**When it runs:** in `repo-checks` with its fixtures; before every push to either branch; at every (b) gate, on
`origin/replication/x86` and `origin/replication/x86-dev-state`.

### Branches and protection (PR3)

1. After the pre-registration gate: `git switch -c replication/x86 86cea844dff5dac8cbd3f61f57a3ffc94aac2838`.
   - Commit 1, `chore(replication): track replication/x86 and replication/x86-dev-state`, holds the `ref` lines only.
   - Commit 2, `chore(replication): Locust idles until a timed run starts it`, holds PR1 (B).
2. `git switch -c replication/x86-dev-state replication/x86`, from its head (report e).
3. The diff check passes on both. Then two approved pushes: `git push origin replication/x86` and
   `git push origin replication/x86-dev-state`.
4. **The owner, in GitHub (Settings → Rules):**
   - A ruleset on `refs/heads/replication/x86`: **Restrict updates** (lock), **Restrict deletions**, **Block force
     pushes**. No bypass actors.
   - A ruleset on `refs/heads/replication/x86-dev-state`: **Restrict deletions** and **Block force pushes**, no
     bypass, like ruleset 23998158.
5. The agent checks:
   - `git ls-remote origin 'refs/heads/replication/*'` equals the named heads;
   - `gh api repos/koussayx8/nexus-platform/rules/branches/replication/x86` (an `ask` call) lists `update`,
     `deletion` and `non_fast_forward`.
6. **A parameter commit** (report d). These are the only times the lock lifts, and each one is a gate:
   1. The owner sets the lock ruleset to Disabled.
   2. The agent commits exactly one commit, `chore(replication): <param> from R1 on x86`, and the diff check passes.
   3. Approved pushes: `git push origin replication/x86`, then the fast-forward
      `git push origin <sha>:replication/x86-dev-state`.
   4. The owner sets the lock back to Active. The agent re-reads the rules and the heads.
   - At most two such gates: B (the users) with any limit; then a second only if R1 is re-run after a limit change
     (PR4).
7. **What lands on `replication/x86-dev-state`:** fast-forwards to `replication/x86`'s head, and nothing else.
   - S5 is injected inside the database (spec §20; ADR-025 addendum, `ALTER ROLE app_dev NOLOGIN`), so no inject or
     reset commit exists.
   - `nexus-dev` stays at L0, as in M1b's S5.

### Attempts (PR4): fixed in advance

| Step | Valid attempts | Repeats after a void |
|---|---|---|
| R1 | 1. A second only if a re-derived limit differs from `v0.3.0`'s; then C and B come from that run | 1 per attempt |
| Checkpoint B | 1 | 1 |
| Run 3 | 1, only if checkpoint B passes (as in M1b) | 1 |
| S5 | 1, whatever checkpoint B's outcome (as in M1b, where checkpoint B failed and S5 ran) | 1 |

- A second void ends that step as **not done**.
- No fix between attempts: a failure is reported, never tuned. That is the point of the replication (ADR-030
  Consequences).
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
| R1 | `ramp.py` defaults as in attempt 2: `--namespace nexus-dev --classes DevUser --start 10 --step 10 --max 200 --settle 60 --measure 120 --spawn-rate 5 --p95-factor 2 --fail-max 0.01 --achieved-min 0.95 --throttle-max 0.10 --rate-diff-max 0.05`. C = `verdict.json` `capacity` (a lower bound when `reached_max`). S\* = the highest passing step with DB CPU < 0.25 cores, working set < 410 MiB and 0 `db_slots_exhausted`. B = floor(min(0.4 C, S\*/2)) | ADR-026; `r1-rerun/start-record.md`; `attempt2/result.txt` |
| Limits | sample-api: ADR-027's principle, read from `container_processes` and threads (one process gives 1000m). DB: ADR-028's principle, with x86's per-request cost from R1. The derivation is written at the R1 gate before any commit | ADR-027, ADR-028 |
| Checkpoint B | Precondition: `cpu.max` at both levels, for sample-api and the DB. 20-min window at B. **Gated:** A1 ≤ 3 % per namespace; A2, every pod ≤ 0.5 % at both levels; D1 ≤ 0.5 % at both levels; D2 ≤ 12. **Recorded:** A3, A4 | ADR-027, ADR-028 |
| Run 3 | 60 min at B: zero Nexus alerts, and the ADR-020 DB criterion (absolute, < 0.25 cores). Reported regardless | ADR-026, ADR-028 |
| S5 | The M1b pre-fault check. Injection exactly per §20 on `app_dev` by the owner. **Pass:** `NexusErrorRateAnomaly` fires for `nexus-dev`, and exactly one Incident created after t0 ends `Recorded` / `level_observe`. DB uid and restartCount unchanged after the reset. The first-fire time is recorded against +105 s (promtool) and +119.5 s (WSL2) | M1b-9 plan; ADR-025 addendum |

- **R1's `--max 200` stays.** If x86 also reaches 200, C is again a lower bound and B = 80. That is a result.
- **The result** is a per-step table: M1b's verdict and x86's verdict side by side. There is no single verdict for
  the replication.

**If the R1 wrapper stays UNKNOWN** (report b): R1 on x86 runs `ramp.py` (VERIFIED) and a new wrapper that implements
the void set above as written. That is a declared deviation in the pre-registration. Its result is labelled
"wrapper rebuilt".

### The harness on `dev` (M2-1b.1, before the pre-registration)

- `experiments/timed-runs/m1b/` holds verbatim copies of the VERIFIED scripts, with a `SHA256SUMS` file and a
  `PROVENANCE.md` (bundle path, run, evidence class, as in report b). It is never edited, and `repo-checks` runs
  `sha256sum -c`:
  - `checkpoint-b/`: `cbrun.py`, `common.py`, `stop.py`;
  - `s5/`: `s5a2.py`, `s5b.py`, `s5c.py`, `s5c-analysis.py`, `common.py`;
  - `clock/`: `clocksample3.py`, `clockanalyze3.py`.
- `experiments/timed-runs/x86/` is derived from `m1b/`. The diff check's tree mode compares the two against a
  harness allow list, which permits only:
  1. `B` and `USERS` (`cbrun.py:10`, `s5a2.py:8`), set from R1. Better: read from the command line, one line each;
  2. `s5c.py`'s `ts9` replaced by `s5c-analysis.py`'s offset-safe parser, plus a unit test for `Z` and `+02:00`
     stamps;
  3. nothing else. The cgroup path stays, and the VM's layout is checked before checkpoint B (UNKNOWN U8).
- `ramp.py`'s per-step counters (`db_slots_exhausted`, `db_unavailable`) are added only as extra output. The
  verdict code stays unchanged, the existing `test_ramp.py` passes unchanged, and a new test replays attempt 2's
  `step-*.json` to the same `verdict.json`.
- **The R1 wrapper reconstruction:**
  1. Replay transcript `702f3821`'s writes and patches, in order, up to 2026-10-09T17:54:15Z, into the scratchpad.
  2. Run its analysis over `attempt2/r1-run.jsonl` against live Prometheus.
  3. It is VERIFIED only if `result.txt` matches byte for byte. **By 2026-10-23** (report h).

### The two named commits (PR6)

| # | Commit | What it names |
|---|---|---|
| 1 | **The system under test**: the full SHA of `replication/x86`'s head after commits 1–2 (PR3) | `v0.3.0` plus the `ref` lines and PR1. Each later parameter commit is named at its own gate, with its diff-check output |
| 2 | **The harness**: the full SHA of the `dev` merge commit that holds `experiments/timed-runs/`, `ramp.py` and the diff check | The VM copy is checked against its `SHA256SUMS` before every run |

The pre-registration also records:
- `v0.3.0^{}` = `86cea844…`;
- the shape, region, image, `nproc`, `MemTotal` and CPU model;
- `allow.tsv`;
- PR1–PR5;
- the schedule.

## Phases and gates

| Step | Content | Cluster effect | Gate |
|---|---|---|---|
| **M2-1b.0** | This plan (PR to `dev`) | none | **M2-1b plan gate** |
| **M2-1b.1** | On `dev`: the harness PR (verbatim copies, derived copies, tests), the diff check with F1–F17, the R1 wrapper reconstruction (by 2026-10-23) | none | merge (typed) |
| **M2-1b.2** | Pre-registration PR: PR1–PR6, the shape, the lists, both commits | none | **GATE M2-1b, pre-registration** (decision) |
| **M2-1b.3** | Branches, pushes, rulesets; the owner's VM and bootstrap; `verify-state.sh` on the VM | x86 VM (owner) | — |
| **M2-1b.4** | R1 → C, B and the limits → parameter commits → calibrated by 2026-11-01 | x86 VM | parameter gates |
| **M2-1b.5** | Checkpoint B → run 3 → S5 → evidence → result | x86 VM | **GATE M2-1b, result** |
| **M2-1b.6** | Deletion by 2026-11-08 (owner) | VM gone | confirmed in the result |

## Verification

- **Offline:** `test_diffcheck.py` (F1–F17); `sha256sum -c` on `m1b/`; the harness diff check of `x86/` against
  `m1b/`; the parser test; `test_ramp.py` plus the replay test; `repo-checks.sh`.
- **On the VM:** `verify-state.sh` exits 0 against the replication branches; the diff check on both remote heads;
  `sha256sum -c` before every run; the shape facts before every run.
- **The replication itself:** the pass and void rules above, reported per step.

## UNKNOWNs (each with what settles it)

| # | UNKNOWN | Settled by |
|---|---|---|
| U1 | The R1 wrapper and `r1analyze.py` (session `702f3821`) | The transcript replay matching `result.txt`, by 2026-10-23; otherwise PR5's fallback |
| U2 | Checkpoint A's orchestrator (session `5543b716`) | Not needed if report i is accepted |
| U3 | The clean-window monitors (session `aeff76de`) | Not needed (superseded) |
| U4 | The trial's service limit for `VM.Standard3.Flex` (and E4, E5) at 6 OCPUs | Owner: Console → Limits, Quotas and Usage |
| U5 | The credit left of €250 | Owner: Console → Billing |
| U6 | That `Standard3.Flex` is billed as "Standard - X9" | Owner: the cost estimate on the create page |
| U7 | The owner's public IP: fixed or dynamic | Owner. If dynamic, the security list is updated when it changes |
| U8 | k3s's cgroup layout on the VM (`kubepods.slice`, systemd driver), which `cbrun.py`'s precondition reads | A read-only `ls /sys/fs/cgroup/kubepods.slice` on the VM before checkpoint B |
| U9 | Whether the OCI Ubuntu image's host `iptables` rules block k3s's node-local traffic | Owner at bootstrap (`sudo iptables -S`); a `verify-state.sh` failure points to it |
| U10 | The VM's `MemTotal` at 16 GB | Read on the VM; recorded as a host fact |

## Assumptions (open ones only)

- `bootstrap.sh` at `v0.3.0`, with the ref lines moved, bootstraps a fresh Ubuntu 24.04 x86 VM without the M2-3
  fixes. The known gaps: the operator restarts until step g (the operator-config ordering), and `observability` can
  exceed its timeout (`NEXUS_WAIT_TIMEOUT_OBSERVABILITY=1800`). The Docker-mount preflight is WSL-only.
- The amd64 images pinned at `v0.3.0` are still pullable from ghcr.io and Docker Hub.
- One OCPU on these shapes is one core with two threads (ADR-030 (b)).

## Risks

- **Dates:** at most 8 days from the VM to "calibrated" (2026-10-24 → 11-01). A slow bootstrap or a void R1 uses up
  the slack. The stop rule then records (b) as not done.
- **Two clusters, one repository.** WSL2 keeps tracking `main` while the VM tracks `replication/*`. A wrong ref line
  would put the VM on `main`. The diff check and `verify-state.sh` on the VM catch it.
- **Until the owner's rulesets are on,** nothing in GitHub stops a push to the replication branches. The window
  is the hours between the push and step 4 of PR3.
- **The lock lifts twice at most,** each time at a gate, with the diff check before the push.
- **Remote commands bypass the deny list** (Access). The trust model on the VM is the agent's process discipline,
  as on WSL2 under ADR-021, without that backstop.
- **Cost:** the worst case is €141. The real charge risk is an upgrade to Pay As You Go, which nobody makes
  (ADR-030).
- **A1 and x86 share one account.** The A1 capacity check (M2-1) and the x86 VM sit in the same tenancy, so
  deleting the wrong instance is possible. The owner names the instance in the result.
