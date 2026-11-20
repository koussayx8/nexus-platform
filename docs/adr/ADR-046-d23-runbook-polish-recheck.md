# ADR-046: D23 — the runbook polish is #224; the retrieval re-check of 2026-11-20 changes nothing

## Status: Proposed, 2026-11-20 (owner's overnight list, item 1). Before the `v0.6.0` freeze.

## Context
TASKS.md D23: "Runbooks: no new ones. Before the Block 9 freeze, polish `rb-bad-rollout` (own words, source cited:
runbooks.prometheus-operator.dev, KubePodCrashLooping) and the `rb-gray-failure` Diagnosis line, then re-check
retrieval once." The owner asked for that polish tonight.

**The polish was already done**, by #224 (`d61a0ba`, 2026-11-15, "docs(runbooks): polish rb-gray-failure Diagnosis and
rewrite rb-bad-rollout (D23)"), merged `3070b18`:
- `rb-bad-rollout` v2: our own wording; the crash-loop causes follow the kube-prometheus KubePodCrashLooping runbook,
  cited in the `source:` front-matter field
  (`https://runbooks.prometheus-operator.dev/runbooks/kubernetes/kubepodcrashlooping/`; front matter is not indexed).
- `rb-gray-failure` v3: the Diagnosis line covers a full hang (p95 can stay flat while the in-flight count rises and
  the completed-request rate drops), as its Symptoms and Decision already did.
- Its retrieval re-check moved the proposed floor 3.0675 → 3.1739 (the Reasoner ConfigMap followed) and the corpus
  hash `b9190372…` → `fad76a74…`. Only the D23 checkbox was never ticked.

Since then the index hash `fad76a74d15f075c8a8818d63fbce6d9911cdb45161ffc4a0984ef53d917e6d8` has been the one every
Block 5 trial, every bake-off round (1–4) and ADR-045 used.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | **No second rewrite tonight.** The runbooks stay at `rb-bad-rollout` v2 and `rb-gray-failure` v3. | D23's polish is done (#224). Another rewrite would change the corpus hash after the bake-off chose the models on `fad76a74…`; that is the owner's call, not an unattended one. If the owner wants a further polish, it is a new decision with its own re-check. |
| 2 | **The re-check, run again on `dev` (`17f47eb`, 2026-11-20):** `rag lint runbooks` 0 errors; `rag index` 40 chunks, corpus hash `fad76a74…`; `rag eval` (the method that set 3.1739) writes a `results.json` **byte-identical** to the committed one: proposed floor 3.1739, Recall@1 0.7917, Recall@3 0.9167, MRR 0.8583, Recall@1 at the floor 0.5417, false retrieval at the floor 0.0. | The floor, `scoreFloor` and every test that pins the corpus hash stay as they are. |
| 3 | **D23 is done** (TASKS.md). | Polish by #224; retrieval re-checked once (#224) and confirmed unchanged (this ADR). |

## Chunks the 10 bake-off bundles retrieve (prompt/v3 path, `bakeoff run --round 4 --dry-run`, on `dev` at `17f47eb`)
No runbook, query builder or retrieval code changed on this branch, so before and after are the same list:
no bundle changes.

| Bundle | Truth (action / kind / fault class) | Chunks |
| --- | --- | --- |
| S1 | restart_workload/-/gray_failure | `['rb-gray-failure#1', 'rb-gray-failure#2', 'rb-gray-failure#3']` |
| S3 | escalate/git_revert/bad_rollout | `['rb-bad-rollout#1', 'rb-image-pull#1', 'rb-traffic-spike#1', 'rb-bad-rollout#3', 'rb-image-pull#3']` |
| S5 | escalate/dependency_investigation | investigate/dependency_failure | `[]` |
| S6 | no_action/-/traffic_spike | `['rb-traffic-spike#1', 'rb-traffic-spike#2', 'rb-traffic-spike#3']` |
| S6-cpu | no_action/-/traffic_spike | cpu_saturation | `['rb-cpu-saturation#1', 'rb-traffic-spike#2', 'rb-cpu-saturation#3', 'rb-traffic-spike#3']` |
| N1 SYNTHETIC | escalate/investigate/image_pull | `['rb-image-pull#1', 'rb-bad-rollout#1', 'rb-traffic-spike#1', 'rb-image-pull#3', 'rb-bad-rollout#3']` |
| N2 SYNTHETIC | escalate/resource_limit_change/unknown | `['rb-bad-rollout#1', 'rb-bad-rollout#3']` |
| N3 SYNTHETIC | escalate/dependency_investigation | investigate/dns_failure | `[]` |
| N4 SYNTHETIC | escalate/dependency_investigation | investigate/cert_expiry | `[]` |
| N5 SYNTHETIC | scale_deployment/-/cpu_saturation | `['rb-image-pull#1', 'rb-cpu-saturation#1', 'rb-traffic-spike#2', 'rb-image-pull#3', 'rb-cpu-saturation#3']` |

## Consequences
- The frozen index hash stays `fad76a74…`; #234's frozen values need no change for D23.
- N2 (finding (d) of ADR-045: only the bad-rollout runbook is retrieved and every model follows it to `git_revert`)
  is not addressed by a runbook rule: a rule aimed at a bake-off case would be teaching to the test.
