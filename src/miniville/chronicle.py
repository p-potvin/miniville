"""Daily chronicle: distills the day's events into a readable entry."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from .events import describe
from .seasons import fmt_date, holiday_on, season_of
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
    lines = [
        f"# Day {day+1} ({weekday}, {fmt_date(day)}, {season_of(day).capitalize()}) "
        "— Miniville Chronicle",
        "",
    ]
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


def day_digest(conn: sqlite3.Connection, day: int, max_events: int = 20) -> str:
    """Compact prompt-ready digest of a day for an LLM narrator."""
    rows = conn.execute(
        "SELECT * FROM events WHERE day=? AND importance>=2 ORDER BY importance DESC, id LIMIT ?",
        (day, max_events)).fetchall()
    pop = conn.execute("SELECT COUNT(*) c FROM agents WHERE alive=1").fetchone()["c"]
    town = conn.execute(
        "SELECT data FROM events WHERE day=? AND kind='town_event'", (day,)).fetchall()
    mood = conn.execute(
        """SELECT s.mood, COUNT(*) c FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1 GROUP BY s.mood ORDER BY c DESC""").fetchall()
    lines = [f"Day {day+1} in Miniville ({pop} residents).",
             f"Date: {fmt_date(day)}, {season_of(day)}"]
    holiday = holiday_on(day)
    if holiday:
        lines.append(f"Holiday: {holiday.name}")
    lines.extend([
             f"Mood of the town: " + ", ".join(f"{m['mood']} x{m['c']}" for m in mood)]
    )
    for t in town:
        lines.append(f"Town news: {json.loads(t['data']).get('text','')}")
    lines.append("Events (most significant first):")
    for r in rows:
        lines.append(f"- {describe(conn, r)}")
    return "\n".join(lines)


def write_narrative(conn: sqlite3.Connection, day: int, text: str,
                    source: str) -> None:
    conn.execute(
        "INSERT INTO narratives(day,source,text) VALUES(?,?,?)",
        (day, source, text.strip()))
    conn.commit()
