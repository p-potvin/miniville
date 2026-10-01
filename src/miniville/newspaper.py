"""The Miniville Gazette — a weekly front page distilled from the chronicle.

The simulation already writes a per-day chronicle. This module rolls seven of
those days into a single newspaper edition: a headline drawn from the week's
most important event, then short sections (arrivals, weddings & births, work,
town life, and the odd scandal). Editions are stored so the observer UI can
browse the archive.

Prose is template-based so the paper never depends on an LLM; `polish()` can
optionally hand the draft to the vault-inference gateway for a rewrite.
"""
from __future__ import annotations

import sqlite3

from .db import get_meta
from .events import describe

DAYS_PER_WEEK = 7

# event tag -> (section title, verb phrase for the roundup)
SECTIONS = {
    "immigration": "Arrivals",
    "birth": "Weddings & Births",
    "marriage": "Weddings & Births",
    "cohabitation": "Weddings & Births",
    "hire": "Working Life",
    "fired": "Working Life",
    "sick": "Town Life",
    "move": "Town Life",
    "affair": "The Scandal Sheet",
    "betrayal": "The Scandal Sheet",
    "separation": "The Scandal Sheet",
    "favor": "Neighbors",
    "favor_repaid": "Neighbors",
    "work_buddy": "Working Life",
    "holiday": "Town Life",
    "season": "Town Life",
    "coming_of_age": "Town Life",
}


def week_of(day: int) -> int:
    return day // DAYS_PER_WEEK


def _tag_of(row: sqlite3.Row) -> str:
    try:
        import json
        return (json.loads(row["data"] or "{}") or {}).get("tag", "")
    except Exception:
        return ""


def _headline(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "A quiet week in Miniville"
    top = max(rows, key=lambda r: (r["importance"], -r["id"]))
    return describe(conn, top).rstrip(".")


def publish_week(conn: sqlite3.Connection, week: int,
                 seed: str = "miniville") -> str:
    """Compose and store the front page for `week`. Returns the text."""
    start, end = week * DAYS_PER_WEEK, week * DAYS_PER_WEEK + DAYS_PER_WEEK - 1
    rows = conn.execute(
        "SELECT * FROM events WHERE day BETWEEN ? AND ? AND importance >= 2 "
        "ORDER BY importance DESC, id", (start, end)).fetchall()

    lines = [f"THE MINIVILLE GAZETTE — Week {week + 1}", ""]
    lines.append(f"**{_headline(conn, rows)}**")
    lines.append("")

    buckets: dict[str, list[str]] = {}
    for r in rows:
        tag = _tag_of(r)
        section = SECTIONS.get(tag)
        if not section:
            continue
        buckets.setdefault(section, []).append(describe(conn, r))

    for section in ("Arrivals", "Weddings & Births", "Working Life",
                    "The Scandal Sheet", "Neighbors", "Town Life"):
        items = buckets.get(section)
        if not items:
            continue
        lines.append(f"{section}:")
        for it in items[:4]:
            lines.append(f"  - {it}")
        lines.append("")

    pop = conn.execute(
        "SELECT COUNT(*) c FROM agents WHERE alive=1").fetchone()["c"]
    lines.append(f"— Population this week: {pop}. "
                 f"{len(rows)} notable events on record.")

    text = "\n".join(lines)
    conn.execute(
        "INSERT INTO newspapers(week,text,created_tick) VALUES(?,?,?) "
        "ON CONFLICT(week) DO UPDATE SET text=excluded.text, "
        "created_tick=excluded.created_tick",
        (week, text, int(get_meta(conn, "tick", "0") or 0)))
    conn.commit()
    return text


def polish(text: str, model: str = "") -> str:
    """Optional LLM rewrite of a draft front page via vault-inference.

    Returns the original text unchanged when the gateway is unreachable, so the
    paper is always publishable.
    """
    from .narrator import _call_vw
    prompt = ("You are the editor of a small-town newspaper. Tighten this front "
              "page into crisp copy, keeping every fact and the section "
              "headings:\n\n" + text)
    try:
        out = _call_vw(prompt, model, max_tokens=900)
        return out or text
    except Exception:
        return text


def latest(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM newspapers ORDER BY week DESC LIMIT 1").fetchone()
