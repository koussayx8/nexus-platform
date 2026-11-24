# M4 freeze record — `v0.6.0`, the idle test and the Block 10 setup (2026-11-22/23)

What was frozen, what the 6-hour idle test showed, what the owner read by hand before the queues, and the five
`clean_stddev` numbers the pre-check uses. Written 2026-11-24 from files outside Git (`~/nexus-evidence/`, not in the
repository) and from the manifests at the tag; every figure names its source. This file records; the rules are in
[`m4-protocol.md`](m4-protocol.md), the decisions in `TASKS.md` (D29, D30, D31).

## 1. The tag

| Item | Value | Source |
|---|---|---|
| Tag | `v0.6.0` = `c8a3635cca8e7b53ef7a3ccc0709b913e2aeb758` (merge of #330); an annotated tag, object `fc17aad30adc4a0c365765fa1aef3e06b480dc88` | `git rev-parse v0.6.0 v0.6.0^{}`, 2026-11-24 |
| Run checkout | `c8a3635cca8e7b53ef7a3ccc0709b913e2aeb758` | `~/nexus-evidence/block10-2026-11-23/run-checkout.txt` |
| Operator | `nexus-operator@sha256:7c9d15997ac78449f1580737885d0dd98107dcc8cb4ec17a17929148ccb295eb` | `operator/k8s/deployment.yaml` at `c8a3635` |
| Reasoner | `nexus-reasoner@sha256:7d216d96c9128dd7e30e3789989716938f533317adbe1b53a86aa843246e386c` | `reasoner/k8s/deployment.yaml` at `c8a3635` |
| sample-api | `sha256:2de0e02520e64613bf9c8a2dd7ce6bebb350ff7634b8210b2b85f505d7620251` | `overlays/dev` and `overlays/prod` `kustomization.yaml` at `c8a3635` |
| nexus-bot | `nexus-bot@sha256:c82c5f0ca29a5aed0f799b2d8efb4e641984f73f7f7e25a3841ee634079b5560` | `bot/k8s/deployment.yaml` at `c8a3635` |
| Ground truth | `471a3b17d33ca0cb691f05b4b379d87375471a2853075611640c23f8a00ec850` (`experiments/ground-truth.json`) | `by-hand.txt` |
| Index hash, score floor, prompt | `fad76a74d15f075c8a8818d63fbce6d9911cdb45161ffc4a0984ef53d917e6d8`, 3.1739, `prompt/v3` | `by-hand.txt` (`/healthz`) |

The operator and sample-api digests replace the ones `m4-protocol.md` §1 quoted "as of `dev` at `4b077fb`" (`9f3380e9…`,
`3321d6fa…`); the protocol's instruction to read both again from the manifests at the tag is what this table does. The
digests were read from the manifests, not from the live cluster; the live operator and Reasoner digests are in
section 3.

## 2. The 6-hour idle test (D19, D29; `m4-protocol.md` §10)

Files: `~/nexus-evidence/idle-test-2026-11-22/` (`run1-before.txt`, `run1-after.txt`, `run1-verify-*.md`, `before.txt`,
`after.txt`, `verify-before.md`, `verify-after.md`).

| Run | Start (UTC) | End (UTC) | Length |
|---|---|---|---|
| 1 | 2026-11-22 17:09:01Z | 2026-11-22 22:40:32Z | 5.5 h |
| 2 | 2026-11-22 22:44:14Z | 2026-11-23 05:51:41Z | 7.1 h |

Run 1 ended short of six hours; the owner started run 2 four minutes after it. Together the two cover 12.6 h
(`TASKS.md` D30 says 12.5 h). Run 2 alone is longer than six hours and is the one `clean_stddev` is read from.

| Criterion (§10) | Result | Source |
|---|---|---|
| 1. Windows stayed up | The boot time read before each run is 2026-11-01 06:58:50 and 06:58:52 (local time, PowerShell). **No Windows boot time was read after either run:** the snapshot script's `powershell.exe` call fails inside WSL ("command not found", seen in `after.txt`), so the `Windows boot:` line is empty in all four files and the two readings above were added by hand to the before files. The owner reports the same boot at the end and no sleep events; that is the owner's statement, not a file | `run1-before.txt` line 143, `before.txt` line 143; owner |
| 2. WSL stayed up | `WSL boot: 2026-11-18 13:49:09` in all four files; uptime 361,192 s at the start of run 1 and 406,951 s at the end of run 2 (+45,759 s, 12.7 h). `journalctl --list-boots` lists boot 0 with the same id (`2fe844df…`, started 2026-11-17 05:44:19 CET) in all four files, with no boot inside either window | the four `*.txt` files |
| 3. `verify-state.sh` passes at the end | exit 0 | owner; the `verify-*.md` reports (not read here: mode 600) |
| 4. Every Incident explained | **No Incident was created in either window.** The `-- Incidents` list is the same twelve rows in all four files (identical checksums); the newest was created 2026-11-22T14:52:26Z, before run 1 began at 17:09Z, so there is nothing to explain | the `-- Incidents` block of each file |

Result: the test passes on criteria 2 and 4 from the files, on criterion 3 by the owner's report, and on criterion 1
on the owner's report only. The Windows reading after the run is the one gap; it does not change the result the
owner reported.

## 3. By-hand readings before the queues (2026-11-23)

`~/nexus-evidence/block10-2026-11-23/by-hand.txt` (read-only commands, 2026-11-23T08:15:06Z) and `r3-check.txt`.

| Reading | Value |
|---|---|
| `/healthz` | `ready`; corpus hash `fad76a74…e6d8`; `prompt/v3`; floor 3.1739; active `R2` = `r2-primary` (`nvidia/nemotron-3.5-super-vl-120b-a12b`), `R3` = `r3-primary` (`nvidia/nemotron-3-ultra-550b-a55b`) |
| Operator `reasonerTimeoutSeconds` | 300 (closes the "UNVERIFIED" in `m4-protocol.md` §1) |
| Live images | operator `sha256:7c9d1599…95eb`, Reasoner `sha256:7d216d96…386c`: the tag's digests |
| Kill Switch | `active` |
| Autonomy levels | `nexus-dev` 3, `nexus-prod` 1, `nexus-data` 0 |
| Open Incidents | none |
| C8 | off ("the GitHub token file absent") |
| `verify-state.sh` | exit 0 |
| One R3 call (`r3-check.txt`) | `proposal/v1`, `action: escalate`, `mode: llm`, `modelConfig: r3-primary`, model `nvidia/nemotron-3-ultra-550b-a55b`, 21,556 ms. The call was made by hand with a port-forward and `curl`, not by `scripts/r3-check.sh` (ADR-049 finding b) |

## 4. `clean_stddev` from run 2

`~/nexus-evidence/block10-2026-11-23/clean-stddev.txt`: the maximum, over run 2's window from 20 minutes after its
start, of `namespace:nexus_sample_api_<rule>:baseline_stddev15m{namespace="nexus-dev"}` (the method is in
[`block10-runsheet.md`](block10-runsheet.md)); 815 samples per signal.

| Signal | Max (the value used) | p99 | Pre-check limit at `stddev_factor` 4 (D31) |
|---|---|---|---|
| `cpu` | 0.006036 | 0.005843 | 0.02414 |
| `latencyP95` | 0.0005073 | 0.0004680 | 0.002029 |
| `inflight` | 0.7483 | 0.7483 | 2.993 |
| `errorRatio` | measured 0 (max and p99); **0.001 used**, a floor | — | 0.004 |
| `requests` | 0.008985 | 0.008985 | 0.03594 |

The `errorRatio` floor is the owner's decision: a clean idle gives 0, and any limit that is a multiple of 0 would
refuse every live value. The values and limits above are the ones in the live pre-check config (`cap_s` 2700,
`quiet_s` 360, `poll_s` 30, `stddev_factor` 4), whose copies are `~/nexus-batch-config/precheck.json`; the run
configs' hashes are in `config-sha256.txt` (original), `-d31.txt` (after the factor change, 11:35Z) and `-d31b.txt`
(after the cap change, 12:30Z).

## 5. The measured queues, as they ended

Files: `~/nexus-evidence/block10-2026-11-23/` (`suite/`, `c7/`, `a/`). Counts only; the per-run results belong to the
analysis, not to this record.

| Part | Result | Source |
|---|---|---|
| Containment suite | 220 dry-run requests (110 `active`, 110 `halted`): 206 as the matrix declared, 14 not (7 per state, ADR-049 finding c): 10 reached no answer because their target does not exist, 4 were refused by C3 where the matrix declares RBAC. None of the 14 was admitted | `suite/suite-active.json`, `suite-halted.json`, `suite/data-l0-manual.txt` |
| C7 `D/R0/S1/L3` | 3 valid of 4 attempts in the final queue (`~/nexus-batch-c7c`), 1 discarded; two earlier attempts were discarded under D31 before any valid run (below) | `c7/status.txt`, `c7/run1.json`, `c7/c7c-run1.json` |
| Phase A | 12 valid of 14 attempts: `A/R1/S1`, `A/R1/S5`, `A/R3/S5` 3 valid in 3; `A/R3/S1` 3 valid in 5 (attempt 3 discarded, `precheck_timeout`; attempt 4 void, `host_disturbance`). The first run's Incident was created 2026-11-23T14:59Z, the last run's 22:05Z (the numeric suffix of the Incident name is its creation time) | `a/status.txt`, `a/runs.tsv` |

### The C7 queues before the valid runs

| Queue | Attempt | Outcome | Cause | Config |
|---|---|---|---|---|
| `~/nexus-batch-c7` | #1 | discarded, `precheck_timeout` (waited 1,207 s) | `latencyP95` stddev 0.00122 above 1.5 × 0.0005073 = 0.00076, under the old config | `config-sha256.txt` |
| `~/nexus-batch-c7b` | #1 | discarded, `precheck_timeout` | `requests` stddev 0.5921 above 4 × 0.008985 = 0.0359: the reset's pod warm-up needs about 18 to 20 minutes to leave the 15-minute window, and the cap was 1,200 s | `config-sha256-d31.txt` |
| `~/nexus-batch-c7c` | #1–#4 | 3 valid, 1 discarded | `cap_s` 2700 | `config-sha256-d31b.txt` |

Both discarded attempts ended in the pre-check, before any injection (`injection_at` is null in `c7/run1.json`, the
first one), so no fault ran in them. Both causes and decisions are in `decisions.txt`.
Whether they count against C7's cap of 5 is the owner's reading of D31 ("C7 restarts in …c7b / …c7c"): the final
queue counts 4 attempts, and the two discarded queues are kept, uncounted, in their directories.
