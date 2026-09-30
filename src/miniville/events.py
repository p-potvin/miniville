"""Event ledger helpers."""
from __future__ import annotations

import json
import sqlite3

from .timekeeper import day_of

# importance levels
TRIVIAL, MINOR, NOTABLE, MAJOR, HISTORIC = 1, 2, 3, 4, 5


def emit(conn: sqlite3.Connection, tick: int, kind: str,
         place_id: int | None = None, a: int | None = None, b: int | None = None,
         importance: int = TRIVIAL, **data) -> int:
    cur = conn.execute(
        """INSERT INTO events(tick,day,kind,place_id,a_id,b_id,importance,data)
           VALUES(?,?,?,?,?,?,?,?)""",
        (tick, day_of(tick), kind, place_id, a, b, importance, json.dumps(data)))
    return cur.lastrowid


def describe(conn: sqlite3.Connection, e: sqlite3.Row) -> str:
    """Human-readable one-liner for an event row."""
    def nm(aid):
        if aid is None:
            return None
        r = conn.execute("SELECT name FROM agents WHERE id=?", (aid,)).fetchone()
        return r["name"] if r else f"#{aid}"
    def pl(pid):
        if pid is None:
            return None
        r = conn.execute("SELECT name FROM places WHERE id=?", (pid,)).fetchone()
        return r["name"] if r else f"#{pid}"
    d = json.loads(e["data"])
    a, b, p = nm(e["a_id"]), nm(e["b_id"]), pl(e["place_id"])
    k = e["kind"]
    if k == "encounter":
        tone = d.get("tone", "neutral")
        return f"{a} and {b} shared a {tone} moment at {p}"
    if k == "relationship":
        return f"{a} and {b} are now {d.get('label','?')} ({p or 'around town'})"
    if k == "life_event":
        return f"{a}: {d.get('text','something happened')}"
    if k == "town_event":
        return f"Town: {d.get('text','')} ({p or 'everywhere'})"
    if k == "world":
        return d.get("text", k)
    return f"{k}: {d}"
