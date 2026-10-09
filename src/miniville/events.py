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
    if k == "gossip":
        return f"{a} told {b}: \"{d.get('summary','...')}\" ({p or 'around town'})"
    if k == "favor":
        if d.get("declined"):
            return f"{b} asked {a} for a favor ({d.get('favor','?')}) and was gently turned down ({p or 'around town'})"
        return f"{a} {d.get('favor','helped out')} for {b} ({p or 'around town'})"
    if k == "favor_repaid":
        return f"{a} repaid {b} for the favor ({d.get('favor','?')}) ({p or 'around town'})"
    if k == "affair":
        return f"{a} and {b} were seen being a little too close ({p or 'around town'})"
    if k == "betrayal":
        w = nm(d.get("with_id"))
        return f"{a} found out about {b} and {w}" + (f" ({p})" if p else "")
    if k == "work_buddy":
        return f"{a} and {b} became work buddies at {p or 'the shop'}"
    if k == "life_event":
        # couple events read naturally with both names, canonical order, so
        # duplicate rows merge into one line: "A and B moved in together"
        if d.get("tag") in ("cohabitation", "marriage") and b:
            return f"{' and '.join(sorted((a, b)))} {d.get('text','something happened')}"
        if a:
            return f"{a}: {d.get('text','something happened')}"
        return d.get("text", "something happened")
    if k == "town_event":
        return f"Town: {d.get('text','')} ({p or 'everywhere'})"
    if k == "world":
        return d.get("text", k)
    return f"{k}: {d}"


def describe_many(conn: sqlite3.Connection, rows) -> list[str]:
    """describe() each row, merging identical lines into '... (xN)'.

    Historical duplicates can exist in the ledger (e.g. the same cohabitation
    emitted twice before the guard); the reader should see one line.
    """
    order: list[str] = []
    counts: dict[str, int] = {}
    for r in rows:
        t = describe(conn, r)
        if t not in counts:
            order.append(t)
        counts[t] = counts.get(t, 0) + 1
    return [t if counts[t] == 1 else f"{t} (x{counts[t]})" for t in order]
