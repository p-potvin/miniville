"""Per-resident memory stream, retrieval, and reflection.

Follows the Generative Agents pattern (Park et al., 2023): every salient event a
resident witnesses is appended to a personal memory stream; memories are later
retrieved by a weighted score of *recency*, *importance*, and *relevance*; and
periodically the top memories are distilled into a higher-level reflection that
becomes a memory in its own right.

Embeddings are optional. When the vault-inference gateway is reachable we embed
memory text through its OpenAI-compatible `/v1/embeddings` endpoint so relevance
is a real cosine similarity; when it is not, retrieval degrades to
recency x importance and the simulation is unaffected.
"""
from __future__ import annotations

import array
import json
import math
import os
import sqlite3
import urllib.request

from .db import get_meta, set_meta
from .events import describe

VW_EMBED_URL = os.environ.get(
    "MINIVILLE_VW_EMBED_URL", "http://127.0.0.1:8000/v1/embeddings")
VW_EMBED_MODEL = os.environ.get("MINIVILLE_VW_EMBED_MODEL", "")
VW_PROJECT = os.environ.get("MINIVILLE_VW_PROJECT", "miniville")

# Generative Agents retrieval weights (recency, importance, relevance)
W_RECENCY, W_IMPORTANCE, W_RELEVANCE = 0.5, 0.3, 0.2
RECENCY_DECAY = 0.995


def embed_text(text: str, model: str = VW_EMBED_MODEL,
               timeout: int = 30) -> list[float] | None:
    """Embed via the vault-inference gateway. None when it is unreachable."""
    payload: dict = {"input": text}
    if model:
        payload["model"] = model
    req = urllib.request.Request(
        VW_EMBED_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-vw-project": VW_PROJECT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return list(data["data"][0]["embedding"])
    except Exception:
        return None


def _pack(vec: list[float] | None) -> bytes | None:
    return array.array("f", vec).tobytes() if vec else None


def _unpack(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def remember(conn: sqlite3.Connection, agent_id: int, tick: int, text: str,
             kind: str = "event", importance: int = 1,
             embedding: list[float] | None = None) -> int:
    """Append one memory to a resident's stream. Returns the memory id."""
    cur = conn.execute(
        """INSERT INTO memories(agent_id,tick,day,kind,text,importance,embedding)
           VALUES(?,?,?,?,?,?,?)""",
        (agent_id, tick, tick // 48, kind, text, importance, _pack(embedding)))
    return int(cur.lastrowid or 0)


def record_event_memories(conn: sqlite3.Connection, tick: int,
                          embed: bool = False) -> int:
    """Turn newly-emitted events into personal memories for their participants.

    Idempotent: the highest event id already folded in is tracked in meta, so
    re-running a tick never double-counts.
    """
    upto = int(get_meta(conn, "memory_upto_event", "0") or 0)
    rows = conn.execute(
        "SELECT * FROM events WHERE id > ? ORDER BY id", (upto,)).fetchall()
    if not rows:
        return 0
    n = 0
    for e in rows:
        text = describe(conn, e)
        vec = embed_text(text) if embed else None
        for aid in {e["a_id"], e["b_id"]}:
            if aid:
                remember(conn, aid, tick, text, kind=e["kind"],
                         importance=e["importance"], embedding=vec)
                n += 1
    set_meta(conn, "memory_upto_event", str(rows[-1]["id"]))
    conn.commit()
    return n


def retrieve(conn: sqlite3.Connection, agent_id: int, k: int = 5,
             now_tick: int | None = None,
             query_embedding: list[float] | None = None) -> list[sqlite3.Row]:
    """Top-k memories by recency x importance x relevance (Generative Agents)."""
    if now_tick is None:
        now_tick = int(get_meta(conn, "tick", "0") or 0)
    rows = conn.execute(
        "SELECT * FROM memories WHERE agent_id=? ORDER BY tick DESC LIMIT 400",
        (agent_id,)).fetchall()
    scored = []
    for m in rows:
        recency = RECENCY_DECAY ** max(0, now_tick - m["tick"])
        importance = min(1.0, m["importance"] / 5.0)
        relevance = 0.5
        if query_embedding:
            mv = _unpack(m["embedding"])
            if mv:
                relevance = max(0.0, _cosine(query_embedding, mv))
        score = (W_RECENCY * recency + W_IMPORTANCE * importance
                 + W_RELEVANCE * relevance)
        scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:k]]


def reflect(conn: sqlite3.Connection, agent_id: int, day: int,
            k: int = 8) -> str | None:
    """Distill a resident's strongest recent memories into one reflection.

    Template-based by default so the simulation never depends on an LLM; the
    reflection is stored as a memory of kind 'reflection'.
    """
    mems = retrieve(conn, agent_id, k=k)
    if len(mems) < 3:
        return None
    name = conn.execute("SELECT name FROM agents WHERE id=?",
                        (agent_id,)).fetchone()
    who = name["name"] if name else f"agent {agent_id}"
    kinds: dict[str, int] = {}
    for m in mems:
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    top = [m["text"] for m in mems[:3]]
    themes = ", ".join(f"{c} {kk}" for kk, c in
                       sorted(kinds.items(), key=lambda x: -x[1])[:3])
    text = (f"{who} reflects on the last few days: {themes}. "
            f"Most on their mind — " + "; ".join(top) + ".")
    remember(conn, agent_id, int(get_meta(conn, "tick", "0") or 0), text,
             kind="reflection", importance=4)
    conn.commit()
    return text


def reflect_all(conn: sqlite3.Connection, day: int, k: int = 8,
                limit: int = 0) -> int:
    """Reflect for every living resident with enough recent memories."""
    q = ("SELECT DISTINCT m.agent_id FROM memories m JOIN agents a ON a.id=m.agent_id "
         "WHERE m.day>=? AND a.alive=1 ORDER BY m.agent_id")
    ids = [r["agent_id"] for r in conn.execute(q, (day - 1,)).fetchall()]
    if limit:
        ids = ids[:limit]
    n = 0
    for aid in ids:
        if reflect(conn, aid, day, k=k):
            n += 1
    return n


def memory_digest(conn: sqlite3.Connection, agent_id: int, k: int = 6) -> str:
    """Human-readable memory list for `inspect` and the observer UI."""
    mems = retrieve(conn, agent_id, k=k)
    if not mems:
        return "  (no memories yet)"
    out = []
    for m in mems:
        tag = "~" if m["kind"] == "reflection" else " "
        out.append(f"  mem{tag}[d{m['day'] + 1}] {m['text']}")
    return "\n".join(out)
