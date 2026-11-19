# ADR-042: prompt/v2 — a per-pod log excerpt under a request budget

## Status: Accepted by the owner, 2026-11-18 (option B, Block 3). Before the `v0.6.0` freeze.

## Context
On 2026-11-17 the first live R3 call after the User-Agent fix (#242) was refused by Groq with HTTP 413. Incident `inc-a676334b7210ccba-1794936549`: `fallbackReason` `http_502: model_unavailable: http_413`. Groq's free tier allows 8,000 tokens a minute, and a single request over that is refused. `prompt/v1` put the whole bundle in the prompt, which on a live bundle is 200 log lines a pod (about 11,000 characters, roughly 2,750 tokens each, measured on all four pods). A probe with the fixture bundle, which has three log lines, gave 200 and 2,368 tokens.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | `prompt/v2`. Per pod, the prompt carries an excerpt. First come the lines that match `ERROR\|WARN\|Exception\|Traceback\|timeout\|refused\|OOMKilled\|connection` (case-insensitive), the most recent ones, up to 3/4 of the pod's budget. Then the most recent other lines fill the budget of 40 lines a pod. Previous and current are taken in time order, and the original order is kept. Each line is shown as `[i] line`, where `i` is its index in the full bundle. | Error lines carry the diagnosis, and recent lines carry the state. The index keeps every `evidenceRef` naming the same line in the full bundle, so the ref check (`service.resolves`) is unchanged. |
| 2 | Lines are cut at 300 characters; events are capped at the 20 newest. | One line or a long event list cannot take the budget. |
| 3 | Budget: about 5,000 prompt tokens, estimated at 3 characters a token (cautious). Over it, the excerpt is trimmed level by level: 40, 30, 20, 10, 5 and 0 lines a pod, with 20 down to 0 events. Hard guard: a request over 7,000 tokens (prompt plus the 1,024-token output reservation) is never sent. The Reasoner answers 422 `prompt_too_large` before any provider call, so the operator falls back to R1 with that reason. | Never send a request the provider must refuse. The margin covers the estimate's error. |
| 4 | Rule 7 in the prompt: the logs are an excerpt; escalate or abstain when the evidence given is not enough. | No second fetch, no tool loop (CLAUDE.md rule 8). |
| 5 | The full bundle is unchanged: the Flight Recorder stores it, retrieval queries it and refs are checked on it. The operator adds `logs.sources` (`{pod, current, previous}` line counts, in append order) so the excerpt can be built per pod. This is additive: the bundle stays `evidence/v1.1`. Bundles without it, such as the recorded replay bundles, are taken as one slice with the budget of the pods they may hold. | Per pod needs the boundaries, and the bundle did not mark them. |
| 6 | The response metadata records the selection: `logSelection.pods[]` (`pod`, `lines`, `kept_error_lines`, `kept_recent_lines`, `dropped`), `eventsShown`/`eventsTotal`, `promptTrimLevel` and `promptTokensEstimate`. | The excerpt is part of the experimental condition, so it is recorded with each call. |
| 7 | The prompt is the same for R2 and R3: `plan()` takes no arm. | Spec §15: identical across arms. |

## Consequences
- `promptVersion` becomes `prompt/v2`. Every measured run uses it, and the protocol's frozen-values table must say so (#234). Runs recorded with `prompt/v1` are not comparable with v2 runs.
- S6: the crafted lines are written with `logger.error`, so pattern 1 keeps them (test `test_the_s6_lines_are_kept`). The live log is mostly uvicorn `/metrics` access lines: 200 lines span about 14 minutes, so a line written within that window of evidence collection is in the bundle.
- Thesis limitation: the model sees at most 40 lines a pod, and a cause outside the patterns and outside the most recent lines is not shown. R1 does not read logs and is unaffected.
- The provider may change later through configuration: Bedrock with the same `gpt-oss` models is pending an AWS account enablement. The budget is in `prompt.py`. A provider with a larger limit would change the budget, as a new prompt version.
- Live checks pending: one real-size probe, and the S1 run with R3.

## Addendum (2026-11-19): the S1 run with R3, the recorded excerpt, the token ratio

- S1 with R3 at L3 on `nexus-dev` (Incident `inc-669532275a7b73d2-1795079469`) ended `Resolved` /
  `remediation_verified`: no fallback, `restart_workload`, confidence 0.9. The provider counted
  **5,134 prompt tokens** (407 completion); the call took 1,815 ms. That is the first request sent
  with `prompt/v2`.
- Decision 6 was not met in the record: the operator kept only fixed scalar keys of the Reasoner's
  metadata, so `logSelection`, `promptTrimLevel` and `promptTokensEstimate` were dropped. The operator
  now keeps them, cleaned, in `status.reasoner` and merged into `proposals.usage` (jsonb, no schema
  change), also from a 502/503/504 body.
- Ratio. 5,134 tokens is above the 5,000 budget the excerpt aims for, so 3.0 characters a token is not
  cautious. The measured ratio is the rebuilt prompt's characters over 5,134
  (`experiments/calibration/prompt_ratio.py`, from the Incident's stored bundle and its chunks
  `rb-gray-failure#1`, `#2`). `CHARS_PER_TOKEN` becomes that ratio rounded down to one decimal.
  **Measured** on the owner's export of the stored bundle (hash `sha256:094a9293…e6a6`, equal to the
  Incident's `bundleHash`; 2 pods, 400 + 200 log lines, all access lines, 0 events): the rebuilt prompt
  is **13,211 characters**, so **2.573 characters a token**. `CHARS_PER_TOKEN` is **2.5**. At 3.0 that
  prompt was estimated at 4,405 tokens, level 0; the provider counted 5,134. At 2.5 the same bundle is
  estimated at 5,285 tokens, so it gets level 1 (30 lines a pod).
- The 422 `prompt_too_large` path's `requestTokensEstimate` is kept too (`status.reasoner`,
  `proposals.usage`): it is the only record of a request that was never sent.
- Effect on real-size bundles (200 access lines a pod, the three largest chunks; offline, prompt.plan()):

  | chars/token | 2 pods | 3 pods |
  | --- | --- | --- |
  | 3.0 (until 2026-11-19) | level 0, 40 lines a pod | level 1, 30 |
  | 2.7 – 2.9 | level 0, 40 | level 2, 20 |
  | **2.5 (measured)** – 2.6 | level 1, 30 | level 2, 20 |
  | 2.1 – 2.4 | level 1–2, 30–20 | level 3, 10 |

- Still `prompt/v2`. Only the estimate changes, not the prompt's form; no measured run used v2
  before this one, and this S1 run is not a measured run.
