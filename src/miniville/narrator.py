"""Optional local-LLM narration via Ollama (localhost:11434).

Never affects simulation state — it only turns important events into prose.
Bounded by max_calls; narrating is opt-in via CLI flag or env var.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request

from .events import describe

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e2b-it-qat"

PROMPT = (
    "You are the town chronicler of Miniville, a scenic small town. "
    "Rewrite the following events as a short warm newspaper blurb "
    "(2-3 sentences max, no lists):\n\n"
)


def narrate_events(conn: sqlite3.Connection, day: int, model: str = DEFAULT_MODEL,
                   max_calls: int = 5) -> list[str]:
    """Narrate up to max_calls notable events of a day. Returns prose lines."""
    rows = conn.execute(
        "SELECT * FROM events WHERE day=? AND importance>=3 ORDER BY importance DESC, id LIMIT ?",
        (day, max_calls)).fetchall()
    out = []
    for r in rows:
        line = describe(conn, r)
        out.append(_call_ollama(PROMPT + line, model))
    return out


def _call_ollama(prompt: str, model: str, timeout: int = 120) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("response", "").strip()
