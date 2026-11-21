# ADR-045: prompt/v3 — a 16K budget, the reference wording, NVIDIA NIM only

## Status: Accepted, 2026-11-20 (owner, after bake-off round 4; the R2/R3 choice is final). Before the `v0.6.0` freeze.

The owner asked for this as "ADR-044"; that number is taken (`ADR-044-evidence-v1.2-and-escalation-suggestion.md`,
merged in #278), so it is ADR-045.

## Context
- **What ran so far.** The Block 3 and Block 5 trials ran R3 as `openai/gpt-oss-120b` on Groq with prompt/v2 (Block 3, S1)
  and prompt/v2's 5,000-token excerpt; prompt/v2.1 (rule 8, ADR-044) came after them. Groq's free tier (8,000 tokens a
  minute) set that budget (ADR-042).
- **Bake-off round 3** (`~/nexus-evidence/bakeoff-2026-11-20/round3/`, 140 calls: the "big" prompt of about 16,000
  tokens, 10 real trial bundles, 7 NIM models) found that every invalid reference was a near-miss that our own
  prompt caused, not invented evidence:
  - the literal text `"[]"` inside `runbookRefs` (rule 5 said "use [] when …");
  - log ranges such as `logs.current[0:53]`;
  - citations of `logs.excerpt`, which exists in the prompt's view of the bundle, not in the bundle;
  - shortcuts such as `pods[0].state`.
- **Decision (owner):** NIM only, one backup per arm; the Groq configs are retired.
- **Bake-off round 4** ran prompt/v3 (this ADR) on the same 10 bundles, twice each (results below), and decided
  the models.

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | **`prompt/v3`.** One excerpt budget of **16,000 prompt tokens** for every model, the bake-off's big prompt: trim levels of 200, 150, 120, 90, 60 and 40 lines a pod, 400 then 300 characters a line, and every event (the collector keeps at most 50), then four smaller levels only to make a pathological bundle fit the guard. | What round 3 measured is what runs. One budget keeps the prompt identical across arms (spec §15). |
| 2 | **The request guard** is 40,000 tokens: prompt estimate + **the model's own output cap** (`maxOutputTokens`, at most 16,384 in the configuration), not the file-wide 1,024. | A thinking model's 16K output must be reserved; every NIM model of the bake-off has a context far above 40,000. |
| 3 | **Rule 5** now says: evidenceRefs are exact paths into the bundle, one per entry, with single indexes (`metrics.zscores.latencyP95`, `logs.current[2]`, `pods[0].containers[0].reason`); never a range such as `logs.current[0:53]`; never a path not in the bundle. runbookRefs are the chunk ids written in square brackets before each runbook chunk (`rb-bad-rollout#3`); cite only chunks used; when none applies or the runbooks say no_runbook, `"runbookRefs": []`, never a list holding the text `"[]"`. **Rule 7** adds that `logs.excerpt` is metadata about the excerpt and is never cited. Every other rule keeps its meaning. | Fix the wording that caused the near-misses. **The ref check is unchanged and strict**: a range, `logs.excerpt`, `pods[0].state` and `["[]"]` are still refused (`invalid_refs`). |
| 4 | **`CHARS_PER_TOKEN` stays 2.5.** Round 3's `calls.csv` records the provider's prompt tokens for every call; characters a token (`prompt_estimate` × 2.5 / `prompt_tokens`): Gemma 4 31B min 2.049, median 2.659 (19 calls); Nemotron 3 Ultra min 2.061, median 2.792 (20); Kimi K3.1 Pro min 2.454, median 3.169 (16). 2.5 stays because the medians are above it, because on NIM the estimate only sizes our own excerpt (no provider limit is near it), and because the worst measured case still fits the guard: 16,000 × 2.5 / 2.049 ≈ 19,500 real tokens + a 16,384 output cap = about 35,900, inside 40,000 and every model's context. | At 2.5 prompt/v3's excerpt is exactly round 3's big prompt (a full two-pod bundle at level 0), so round 4 differs from round 3 only by the reference wording: a clean comparison. |
| 5 | **`extraBody`**, nvidia provider only: an optional JSON object merged into the request body; keys that would override `model`, `messages`, `temperature`, `seed`, `max_tokens` or `response_format` are a configuration error, as is `extraBody` on another provider. Recorded in `settings.extra_body` (keys only). | A model's thinking switch is a provider option (`chat_template_kwargs.enable_thinking`). `r2-backup` uses it to turn Nemotron 3 Ultra's thinking off; the bake-off also ran Gemma 4 with it on. A returned `reasoning_content` is stored as today (ADR-035 addendum C). |
| 6 | **Named configs, NIM only, one backup per arm, final after round 4:** `r2-primary` `nvidia/nemotron-3.5-super-vl-120b-a12b`, a **preview** (120 s, 8,192); `r2-backup` `nvidia/nemotron-3-ultra-550b-a55b` with thinking off (`extraBody`), **untested**: only if NIM retires 3.5 Super, and baked off before use (120 s, 8,192); `r3-primary` `nvidia/nemotron-3-ultra-550b-a55b`, thinking on (its default, as tested; 300 s, 8,192); `r3-backup` `google/gemma-4-31b-it`, thinking off (its default, as tested; 300 s, 8,192). `active: {"R2": "r2-primary", "R3": "r3-primary"}`. `r2-groq` and `r3-groq` are deleted; the groq provider code and its tests stay. `arms.py` expects these configs and model ids (provider nvidia, no `reasoning_effort`, which is Groq's option). | Round 4's results and the rule below. R2 is a preview by the owner's choice: the only small model with 0 hangs and 0 errors, with `r2-backup` as its fallback. |
| 7 | **A model id shared by two configured entries resolves to nothing** (`Config.entry_for_model`): `r2-backup` and `r3-primary` are both `nvidia/nemotron-3-ultra-550b-a55b`. Requests name an arm (`R2`, `R3`) or a config; no live path resolves by model id (the operator sends the arm name the harness writes). | An ambiguous id must never pick a config by file order. |

## Bake-off round 4 (prompt/v3; 10 bundles × 2 repeats; `~/nexus-evidence/bakeoff-2026-11-20/round4` and `round4-r2`)

| Model | Live-effective | Hangs | Median latency |
| --- | --- | --- | --- |
| Nemotron 3.5 Super (`nvidia/nemotron-3.5-super-vl-120b-a12b`, preview) | 15/20 | 0 | 18 s |
| Nemotron 3 Ultra (`nvidia/nemotron-3-ultra-550b-a55b`) | 14/20 | 0 | 22 s |
| Gemma 4 31B (`google/gemma-4-31b-it`) | 13/20 | 4 | 117 s |
| Gemma 4 31B, thinking on | 12/20 | 7 | 178 s |
| Kimi K3.1 Pro (`moonshotai/kimi-k3.1-pro`) | 7/20 | 13 | 249 s |

**The rule, fixed before round 4:** the highest live-effective (schema-valid, every reference resolves, and correct)
with at most 2 hangs of 20, not a preview, and a median of at most 300 s. Only Nemotron 3 Ultra passed for R3.

**Findings.**
- (a) On the free endpoint, availability decided more than quality: since round 3 Gemma's median latency doubled and
  Kimi's hangs tripled, while both were the most accurate when they answered.
- (b) The reference wording worked: references resolved on every answered call but one (`pods[0].state`).
- (c) The small model matched the large one on these bundles.
- (d) N2: when retrieval returns only the bad-rollout runbook, every model followed it to `git_revert`.

## Consequences
- The operator's `reasonerTimeoutSeconds` is 300 (the cap, ADR-035 addendum D; the bootstrap template says so and the
  owner sets it in the live ConfigMap): the client waits 305 s and Diagnosing ends at 330 s. The strict arm check
  (ADR-035 B5) holds R3 to `r3-primary` and `nvidia/nemotron-3-ultra-550b-a55b`, and refuses `r2-backup` although it
  runs the same model id.
- The live `nexus-operator-config` must name the arm (`reasonerModel: R3`), as the harness writes it at every reset:
  the Groq model ids no longer resolve.
- `prompt/v3` and `evidence/v1.2` are the frozen versions; #234 §1 is updated once, at the freeze.
- Runs on prompt/v2 or v2.1 (the Block 3 and 5 trials, gpt-oss-120b on Groq) are not comparable with prompt/v3 runs.

## Addendum (2026-11-21): night reliability run, 2026-11-20/21

**What ran.** prompt/v3 on the same 10 bundles, six runs about 45–80 minutes apart from 21:54 local, 60 calls a model
(30 on the real scenarios, 30 on the novel ones). Evidence: `~/nexus-evidence/bakeoff-2026-11-20/night/run1` … `run6`.

| Model | Live-effective | Real | Novel | Hangs | Median a run | Max |
| --- | --- | --- | --- | --- | --- | --- |
| Nemotron 3 Ultra (R3, `r3-primary`) | 42/60 | 28/30 | 14/30 | 0 | 13–20 s | 53 s |
| Nemotron 3.5 Super (R2, `r2-primary`) | 42/60 | 30/30 | 12/30 | 0 | 19–23 s | 69 s |
| Gemma 4 31B (R3 backup, `r3-backup`) | 48/60 | 27/30 | 21/30 | 6 | 37–191 s | 274 s |

"Median a run" is the range of the six per-run medians.

**Findings.**
- (e) Both Nemotrons had 0 hangs in 120 calls with stable latency: the reason they were chosen holds.
- (f) Gemma answers the novel cases best, but the free tier served it unpredictably (a median of 37–191 s between runs,
  10% hangs). This repeats finding (a).
- (g) On the real scenarios the small R2 matched the large R3. This repeats finding (c).
- (h) One Ultra run dropped to 5/10 at temperature 0, so repeated runs matter: 5 valid runs per cell.

**Decision.** Unchanged: `active: {"R2": "r2-primary", "R3": "r3-primary"}`, with the backups of decision 6.
