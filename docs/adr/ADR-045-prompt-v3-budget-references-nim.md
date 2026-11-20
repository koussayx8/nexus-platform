# ADR-045: prompt/v3 — a 16K budget, the reference wording, NVIDIA NIM only

## Status: Proposed, 2026-11-20 (owner, after bake-off round 3). Before the `v0.6.0` freeze. R3's pair is provisional until bake-off round 4.

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

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | **`prompt/v3`.** One excerpt budget of **16,000 prompt tokens** for every model, the bake-off's big prompt: trim levels of 200, 150, 120, 90, 60 and 40 lines a pod, 400 then 300 characters a line, and every event (the collector keeps at most 50), then four smaller levels only to make a pathological bundle fit the guard. | What round 3 measured is what runs. One budget keeps the prompt identical across arms (spec §15). |
| 2 | **The request guard** is 40,000 tokens: prompt estimate + **the model's own output cap** (`maxOutputTokens`, at most 16,384 in the configuration), not the file-wide 1,024. | A thinking model's 16K output must be reserved; every NIM model of the bake-off has a context far above 40,000. |
| 3 | **Rule 5** now says: evidenceRefs are exact paths into the bundle, one per entry, with single indexes (`metrics.zscores.latencyP95`, `logs.current[2]`, `pods[0].containers[0].reason`); never a range such as `logs.current[0:53]`; never a path not in the bundle. runbookRefs are the chunk ids written in square brackets before each runbook chunk (`rb-bad-rollout#3`); cite only chunks used; when none applies or the runbooks say no_runbook, `"runbookRefs": []`, never a list holding the text `"[]"`. **Rule 7** adds that `logs.excerpt` is metadata about the excerpt and is never cited. Every other rule keeps its meaning. | Fix the wording that caused the near-misses. **The ref check is unchanged and strict**: a range, `logs.excerpt`, `pods[0].state` and `["[]"]` are still refused (`invalid_refs`). |
| 4 | **`CHARS_PER_TOKEN` 2.0** (was 2.5). From round 3's `calls.csv` (`prompt_estimate` × 2.5 / `prompt_tokens`, per call): Gemma 4 31B min 2.049 (median 2.659, 19 calls), Nemotron 3 Ultra min 2.061 (2.792, 20), Kimi K3.1 Pro min 2.454 (3.169, 16). The smallest is below 2.5, so it is used, rounded down as in ADR-042's addendum. | The estimate must stay cautious for the model that tokenises densest. At 2.0 a full two-pod live bundle takes level 1 (150 lines a pod). |
| 5 | **`extraBody`**, nvidia provider only: an optional JSON object merged into the request body; keys that would override `model`, `messages`, `temperature`, `seed`, `max_tokens` or `response_format` are a configuration error, as is `extraBody` on another provider. Recorded in `settings.extra_body` (keys only). | Gemma 4 needs `{"chat_template_kwargs": {"enable_thinking": true}}`: NIM turns its reasoning off by default for `google/gemma-4-31b-it`. Its `reasoning_content` is stored as today (ADR-035 addendum C). |
| 6 | **Named configs, NIM only, one backup per arm:** `r2-primary` `nvidia/nemotron-3-ultra-550b-a55b` (120 s, 8,192); `r2-backup` `nvidia/nemotron-3.5-super-vl-120b-a12b`, a preview (120 s, 8,192); `r3-primary` `google/gemma-4-31b-it` with thinking on (300 s, 16,384); `r3-backup` `moonshotai/kimi-k3.1-pro` (300 s, 8,192). They replace the empty placeholders. `active` stays `{"R2": "r2-groq", "R3": "r3-groq"}` until round 4; then one more commit in this PR switches `active` to the NIM configs and removes `r2-groq` and `r3-groq`. The groq provider code and its tests stay. | The owner's choice; R3's pair is provisional. |

## Consequences
- With 300 s entries the operator's `reasonerTimeoutSeconds` must be set to 300 (the cap, ADR-035 addendum D): the
  client waits 305 s and Diagnosing ends at 330 s. The strict arm check (ADR-035 B5) holds a Gemma-served R3 to
  `r3-primary` and its model id once `arms.py` expects them.
- Until `active` is switched, a 16K prompt sent to Groq's free tier would be refused (413) and fall back to R1: this
  PR merges only with the round-4 switch commit.
- `prompt/v3` and `evidence/v1.2` are the frozen versions; #234 §1 is updated once, at the freeze.
- Runs on prompt/v2 or v2.1 (the Block 3 and 5 trials, gpt-oss-120b on Groq) are not comparable with prompt/v3 runs.
