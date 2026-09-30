# Narration layer — who writes Miniville's prose

Three narrator sources, in preference order. All are optional; the sim never
depends on any of them.

## 1. In-session agent (preferred — zero cost, best quality)

Any agent session can narrate. The flow:

```powershell
.\.venv\Scripts\python.exe -m miniville.cli digest --day 3      # compact prompt-ready summary
# agent reads digest, composes prose, then:
"<prose>" | .\.venv\Scripts\python.exe -m miniville.cli narrate-write --day 3 --source "swe-1.6"
```

- `digest` emits the day's notable events + town mood + town news (max 20).
- `narrate-write` stores prose in `narratives(day, source, text)`. `--file` also works.
- No external calls at all. The daily backup session should narrate yesterday
  before doing anything else (see STATE.md resume note).

## 2. Manual trigger (operator fallback)

If no agent session is running, the operator can paste the digest into any LLM
chat and pipe the result back via `narrate-write --file`. One command each way.

## 3. HF Inference (paid, hard-capped)

`narrate --day N --max M --provider hf` uses `huggingface_hub.InferenceClient`
(default model `openai/gpt-oss-20b:deepinfra`). Token from
`..\.access\huggingface_token.txt` or `HF_TOKEN`. Cumulative spend lives in
`meta.narration_cost_usd` via the API's `estimated_cost` field; once it reaches
the $1.50 cap (`MINIVILLE_NARRATION_CAP_USD` overrides) the provider degrades
to Ollama permanently until the key is reset.

Cost observed: ~$0.0001 per blurb — the cap covers ~15k blurbs, far beyond a
daily narrative. gpt-oss-20b reasons before answering, so `max_tokens` is 640
to leave room for both reasoning and content.

## 4. Ollama (local fallback, bounded)

`--provider ollama`, or the automatic fallthrough when HF fails/caps out.
Default model `gemma4:e2b-it-qat` on `localhost:11434`. GPU cost is real on
this PC — prefer option 1 or 2. BULK loops still need operator approval per
REQUEST_RATE_LIMITING.

## 5. Raw (last resort)

`--provider raw` prints the digest lines untouched; also the terminal
fallthrough when Ollama is unreachable.

## Output contract

`narratives` rows are additive history — never edited, multiple sources per day
allowed. Readers (CLI, future UI) show the latest or all.
