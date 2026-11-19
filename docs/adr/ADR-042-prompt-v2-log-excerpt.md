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
- S6: the crafted lines are written with `logger.error`, so pattern 1 keeps them (test `test_the_s6_lines_are_kept`). **Amended 2026-11-19 (owner):** the "about 14 minutes" was measured with Locust stopped. Under the baseline load (80 req/s a namespace, two pods) sample-api wrote one access line per request, so 200 lines spanned about 5 s: the S1 bundle of 2026-11-19 holds 400 current lines, all 2xx access lines (`/` 268, `/items` 68, `/ready` 27, `/metrics` 19, `/health` 18), and S6's three injected lines would be gone before evidence is collected. Fix: sample-api's access log keeps only non-2xx responses (`AccessLogFilter`, extending the fault-route filter); application logs are unchanged. With it, the bundle's lines are application lines and non-2xx access lines, and an S6 line stays in it until 200 later such lines are written.
- Thesis limitation: the model sees at most 40 lines a pod, and a cause outside the patterns and outside the most recent lines is not shown. R1 does not read logs and is unaffected.
- The provider may change later through configuration: Bedrock with the same `gpt-oss` models is pending an AWS account enablement. The budget is in `prompt.py`. A provider with a larger limit would change the budget, as a new prompt version.
- Live checks pending: one real-size probe, and the S1 run with R3.
