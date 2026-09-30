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

## 3. Ollama (local, bounded)

`narrate --day N --max M` calls `localhost:11434` (default model
`gemma4:e2b-it-qat`). Fine for a few calls; BULK narration loops require
operator approval per REQUEST_RATE_LIMITING. GPU cost is real on this PC —
prefer option 1.

## 4. HF Inference (optional, not yet implemented)

Operator has ~$2/month HF quota. Single daily call ≈ a few cents. Would need:
explicit approval + a per-run request cap + a chosen cheap model. Deferred
until someone actually wants unattended prose without an agent session.

## Output contract

`narratives` rows are additive history — never edited, multiple sources per day
allowed. Readers (CLI, future UI) show the latest or all.
