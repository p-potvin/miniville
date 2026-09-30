"""Daily chronicle: distills the day's events into a readable entry."""
from __future__ import annotations

import sqlite3
from collections import Counter

from .events import describe
from .timekeeper import DAY_NAMES

HEADLINES = {
    "relationship": ["new bonds in town", "hearts and rivalries"],
    "life_event": ["milestones"],
    "town_event": ["around town"],
}


def write_day(conn: sqlite3.Connection, day: int, seed: str) -> str:
    rows = conn.execute(
        "SELECT * FROM events WHERE day=? ORDER BY importance DESC, id", (day,)).fetchall()
    pop = conn.execute("SELECT COUNT(*) c FROM agents WHERE alive=1").fetchone()["c"]
    weekday = DAY_NAMES[day % 7]
    lines = [f"# Day {day+1} ({weekday}) — Miniville Chronicle", ""]
    lines.append(f"*{pop} residents. {len(rows)} recorded happenings.*")
    lines.append("")

    kinds = Counter(r["kind"] for r in rows)
    notable = [r for r in rows if r["importance"] >= 3][:12]
    minor = [r for r in rows if r["importance"] == 2]

    if notable:
        lines.append("## Headlines")
        for r in notable:
            lines.append(f"- {describe(conn, r)}")
        lines.append("")
    if minor:
        lines.append("## Around town")
        for r in minor[:10]:
            lines.append(f"- {describe(conn, r)}")
        lines.append("")
    lines.append("## By the numbers")
    for k, c in kinds.most_common():
        lines.append(f"- {k}: {c}")
    text = "\n".join(lines)

    conn.execute(
        "INSERT INTO chronicle(day,text) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET text=excluded.text",
        (day, text))
    conn.commit()
    return text
