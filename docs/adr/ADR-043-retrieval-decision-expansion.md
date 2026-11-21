# ADR-043: retrieval adds the Decision chunk of the top 2 runbooks hit

## Status: Proposed, 2026-11-20 (owner's fix round after the Block 5 trial runs, finding (a)). Before the `v0.6.0` freeze.

## Context
Block 5 trial run S3 (2026-11-19, Incident `inc-ac15ee36af8a7a62-1795120098`, R3 at L3) retrieved
`rb-bad-rollout#1`, `rb-image-pull#1` and `rb-traffic-spike#1`: three Symptoms sections. The Decision
section of `rb-bad-rollout` (`#3`: "never restart or scale a rollout that is failing … Escalate with git
revert") was not among them. R3 named the fault class right (`bad_rollout`) and proposed
`restart_workload`, which the Safety Gate refused at stage 6. The evidence query matches the Symptoms
wording (alert names, reasons, rollout state) far better than the Decision wording, so for a runbook that
is hit, the chunk that says what to do can rank below the cut-off of 3.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | After BM25 retrieval (top 3 above the score floor, `Index.retrieve` and `TOP_K` unchanged), `Index.expand_decisions` adds, for each of the first 2 distinct runbooks among the hits in rank order, that runbook's Decision chunk when the hits do not already hold it. | The Decision section is what the runbook tells the model to do; a runbook that is relevant enough to be hit should bring it. 2 bounds the prompt. |
| 2 | The added chunks come after the hits, with no score and `expanded: true`. | The hits' order, the top chunk and its runbook are unchanged, so Recall@k, MRR, the false-retrieval rate (`rag eval`) and `metrics.retrieval_manipulation` (top runbook) mean what they meant. |
| 3 | No hit adds nothing: `no_runbook` stays `no_runbook`. A runbook without a Decision section adds nothing. | Expansion never invents relevance. |
| 4 | One implementation: `experiments/harness/bm25.py`, which the Reasoner image copies unchanged (`reasoner/Dockerfile`). The Reasoner's `Retriever.retrieve` and the harness replay path (`experiments/calibration/prompt_ratio.py`, through `Retriever`) both get it. | The offline evaluation and the service cannot disagree. |
| 5 | The response metadata keeps `retrievedChunks` (every id sent, hits then added) and adds `expandedChunks` (the added ids). The operator writes `status.reasoner.expandedChunks` and puts `expandedChunks` in the call row's `proposals.usage` (jsonb, no schema change); `status.reasoner.usage` stays tokens and latency. | The chunks sent are part of the experimental condition, recorded with each call (as ADR-042 decision 6). |
| 6 | The prompt template is unchanged; the added chunks are shown like the others. `promptVersion` stays `prompt/v2`. A `runbookRef` to an added chunk is accepted: it was given. | The template text did not change. The ref check is on the chunks given. |

## Consequences
- The prompt grows by up to 2 Decision sections (each one short section). On the 2026-11-19 S1 bundle the
  estimate goes from 4,707 to 4,869 tokens, still trim level 1 under the 5,000 budget; a larger prompt
  trims the log excerpt first (ADR-042 decision 3).
- Runs before and after this change differ in what the model was given. The retrieval condition is part
  of the frozen values (#234): runs recorded before it are not comparable with runs after it.
- The spec (§15, "top 3") is amended by this ADR until spec v1.1.
- R1 does not read runbooks and is unaffected.
