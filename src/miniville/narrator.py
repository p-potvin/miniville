"""Optional LLM narration. Never affects simulation state.

Provider chain: Hugging Face Inference (paid, budget-capped) -> local Ollama
(localhost:11434) -> raw digest line. Budget tracking lives in the meta table
(`narration_cost_usd`); once cumulative cost reaches CAP_USD the provider
permanently degrades to Ollama until the meta key is reset.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from pathlib import Path

from .events import describe
from .db import get_meta, set_meta

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma4:e2b-it-qat"
HF_MODEL = "openai/gpt-oss-20b:deepinfra"
HF_TOKEN_PATH = Path(os.environ.get(
    "MINIVILLE_HF_TOKEN_FILE",
    r"C:\Users\Administrator\Desktop\Github Repos\.access\huggingface_token.txt"))
CAP_USD = float(os.environ.get("MINIVILLE_NARRATION_CAP_USD", "1.50"))
# conservative per-token cost (USD) for gpt-oss-20b on inference providers
COST_PER_IN_TOK, COST_PER_OUT_TOK = 0.00000015, 0.0000006

PROMPT = (
    "You are the town chronicler of Miniville, a scenic small town. "
    "Rewrite the following events as a short warm newspaper blurb "
    "(2-3 sentences max, no lists):\n\n"
)


def _load_hf_token() -> str | None:
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"]
    try:
        return HF_TOKEN_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _spent(conn: sqlite3.Connection) -> float:
    return float(get_meta(conn, "narration_cost_usd", "0") or 0)


def _call_hf(prompt: str, model: str, max_tokens: int = 640) -> tuple[str, float]:
    """Returns (text, cost_usd). Raises on failure.

    gpt-oss-20b is a reasoning model — it spends tokens on reasoning_content
    before content, so max_tokens must cover both (640 ≈ ~4 short blurbs worth).
    """
    from huggingface_hub import InferenceClient
    client = InferenceClient(api_key=_load_hf_token())
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    usage = getattr(resp, "usage", None)
    est = getattr(usage, "estimated_cost", None) if usage else None
    if est:
        cost = float(est)
    elif usage and getattr(usage, "prompt_tokens", None):
        cost = (usage.prompt_tokens * COST_PER_IN_TOK
                + usage.completion_tokens * COST_PER_OUT_TOK)
    else:
        cost = (len(prompt) // 4) * COST_PER_IN_TOK + max_tokens * COST_PER_OUT_TOK
    return (resp.choices[0].message.content or "").strip(), cost


def _call_ollama(prompt: str, model: str, timeout: int = 120) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("response", "").strip()


def narrate_events(conn: sqlite3.Connection, day: int,
                   provider: str = "hf",
                   hf_model: str = HF_MODEL,
                   ollama_model: str = OLLAMA_MODEL,
                   max_calls: int = 5) -> list[str]:
    """Narrate up to max_calls notable events of a day. Returns prose lines.
    provider: 'hf' (cap-enforced) | 'ollama' | 'raw'."""
    rows = conn.execute(
        "SELECT * FROM events WHERE day=? AND importance>=3 ORDER BY importance DESC, id LIMIT ?",
        (day, max_calls)).fetchall()
    out = []
    for r in rows:
        line = describe(conn, r)
        text, src = _narrate_line(conn, line, provider, hf_model, ollama_model)
        out.append(f"[{src}] {text}")
    return out


def _narrate_line(conn: sqlite3.Connection, line: str, provider: str,
                  hf_model: str, ollama_model: str) -> tuple[str, str]:
    if provider == "raw":
        return line, "raw"
    if provider == "hf" and _spent(conn) < CAP_USD and _load_hf_token():
        try:
            text, cost = _call_hf(PROMPT + line, hf_model)
            set_meta(conn, "narration_cost_usd",
                     f"{_spent(conn) + cost:.6f}")
            conn.commit()
            return text, "hf"
        except Exception:
            pass  # fall through to ollama
    try:
        return _call_ollama(PROMPT + line, ollama_model), "ollama"
    except Exception:
        return line, "raw"


def spend_report(conn: sqlite3.Connection) -> str:
    spent = _spent(conn)
    return f"narration spend: ${spent:.4f} / ${CAP_USD:.2f} cap"
