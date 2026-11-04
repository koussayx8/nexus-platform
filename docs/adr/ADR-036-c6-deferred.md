# ADR-036: C6 (K8sGPT comparison) Deferred

**Status:** Accepted (owner decision, 2026-11-04)
**Date:** 2026-11-04
**Spec:** §20 (configuration C6), §21 (K8sGPT diagnosis accuracy), §22 (K8sGPT CLI, MUST), NF-22; re-plan G12

## Decisions

| # | Decision |
| --- | --- |
| 1 | **No experimental K8sGPT comparison.** Configuration C6 is not required for the evaluation. The project does not run `k8sgpt analyze --explain` at evidence time, and M4 has no K8sGPT capture task. |
| 2 | **A theoretical study replaces it.** The comparison with K8sGPT is argued in the written work, not measured. |
| 3 | **Results.** `experiments/analysis` no longer computes K8sGPT accuracy. Table T14 is kept at its number and says "Deferred: theoretical study"; `results.json` carries `c6: "deferred: theoretical study"`. The fixture runs carry no `k8sgpt.json`. |
| 4 | **Plans.** Re-plan G12 moves to a new improvements backlog: "K8sGPT or similar as an extra, redacted evidence source; after M4 or after the PFE". C6 is removed from the M4 outline. |
| 5 | **Unchanged.** The hypothesis, the arms R0–R3, C1–C5, the containment and effectiveness metrics, and the architecture. K8sGPT stays out of the cluster (NF-22). |

## Consequences

- **Spec v1.1 note (G17):** record that §20's C6 row, §21's "K8sGPT diagnosis accuracy" metric, the K8sGPT scoring rubric in `ground-truth` (§20), the §22 "K8sGPT CLI" MUST row and the "Capture … K8sGPT" step of the run flow are deferred, not required. Until v1.1 this ADR and the owner's decision govern (re-plan X10).
- `scripts/export-results.py` keeps its optional `--k8sgpt` input, unused; `experiments/harness/arms.py` keeps its C6 entry (no reasoner arm). Removing both is left to the spec v1.1 cleanup.
