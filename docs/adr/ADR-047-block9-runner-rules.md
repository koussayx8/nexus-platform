# ADR-047: Block 9 runner rules — the reset's baseline and load, the batch's code guard, the probe in Git

## Status: Proposed, 2026-11-20 (owner's overnight list, item 2). Before the `v0.6.0` freeze. Unit tests only; no live run.

## Context
The Block 5 trial runs (2026-11-19/20) left a list of runner lessons: the S3 reset had to be told which
`experiment/dev-state` commit to restore; a reset with `--locust` stopped Locust and left it idle, so the baseline
swarm had to be restarted by hand; the runner refused dirty or unmerged code (#266) but the batch driver did not; and
the Reasoner's isolation probe lived only in the Block 3 evidence folder, with an unquoted `* *` question that the
shell globbed (finding in `docs/demo/manual-scenarios.md`).

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | **`reset` needs `--baseline-ref` and `--git-dir`** (`runner.py plan|run reset`): without either the runner refuses (argparse error, exit 2) and prints nothing to run. | The S3 lesson: a reset that does not restore the commit recorded before the injection leaves the fault deployed. Without `--git-dir` the ref would be ignored silently. |
| 2 | **`reset --locust` restarts the baseline load**: after `GET /stop` it posts the frozen baseline swarm, `runner.BASELINE_SWARM` = 160 users, spawn rate 4, `DevUser` and `ProdUser` (ProdUser's `fixed_count` 80 + 80 DevUsers; ADR-026 addendum). A test reads the values from `platform/load/master.yaml` and the class names from the locustfile. | A reset never leaves Locust idle; the next pre-check needs the baseline. |
| 3 | **The batch driver runs reviewed code only**: `batch.py run --execute` calls `runner.code_guard` (#266) before it builds anything live, and refuses (exit 2) on uncommitted changes under `experiments/` or a HEAD that is not a commit on `origin/dev` or `origin/main`. A dry run needs no guard. | The same rule for the unattended path as for the manual one. |
| 4 | **The isolation probe is `scripts/isolation-probe.sh`**: each `auth can-i` question is split with `read -ra` and passed as a quoted array, so `* *` reaches kubectl as two literal `*`; the control endpoint is `integrate.api.nvidia.com:443`, the provider R2 and R3 call (ADR-045; Groq is retired). An offline test (`scripts/tests/test_isolation_probe.py`) runs it with a stub kubectl from a directory of files: every check holds, the literal `*` arrives, nothing is globbed, every call but the one `exec` is a read. The old version fails that test. shellcheck (CI) passes. | The probe is evidence for the thesis claim "no authority, no route"; it belongs in Git, and it must not print FAIL for a check that holds. |
| 5 | **The void-run rule is already in the batch and is not changed here.** A run is void, its reason recorded, when the boot_id changes (`boot_id_changed`) or the wall clock moves against the monotonic clock by more than `max_pause_gap_s` between its steps or at its end (`pause_gap`; a PC sleep or a VM pause), and when Locust is not `running` with its users at the end (`locust_end_check`). Locust does not restart itself (no self-heal, `platform/load/master.yaml`), so a Locust that stopped during the run is still stopped at the end check, and a VM suspend, which is what stops it (finding (g)), also trips the pause gap. | The rule exists and is tested (`test_batch.py`, `test_review_m4_validity.py`). Its threshold is the open question below. |

## Open (not decided here)
- **The pause-gap threshold.** The batch's default `MAX_PAUSE_GAP_S` is **1.0 s** (the M2-1 void rule V2), set per queue
  with `--max-pause-gap` and fixed by the pre-registration (G16). The owner's item says "more than 30 s". 1 s is stricter
  (it voids every run a 30 s threshold voids, and more); loosening it is the owner's decision at G16, not an unattended
  change.
- **A mid-run Locust poll.** Today the Locust check runs at the end of a run (and in the pre-checks). If a stop that is
  restarted by hand before the end must void the run too, the batch needs a poll between steps.
- **The batch's own reset** passes the cell's optional `baseline_ref`; a cell without one resets without restoring the
  tree. Whether `init` should refuse a cell (at least an S3 cell) without `baseline_ref` is open: #234 still lists
  "S3's patch and `baseline_ref`" as a TODO.
- **The live check of the new probe**: `scripts/isolation-probe.sh` has only run against the stub; the owner's run on the
  cluster confirms it.

## Consequences
- Manual resets must now name the baseline: `runner.py run reset --level 3 --context default --git-dir ~/nexus-exp
  --baseline-ref <commit> --execute`, the form the Block 5 notes already used.
- #234's frozen values are unchanged (they are updated once, at the freeze).
