"""Resident Sabbaticals, Personal Memoirs & Town Masterclasses (v0.43).

Every 7 days (`day % 7 == 4`), a seasoned resident (`age >= 42` or
`standing >= 10`) completes a **Sabbatical Lyceum** (`sabbaticals` table):

* **Town Masterclasses (`masterclass_taught`)** — the scholar hosts a public
  seminar at `Miniville Public Library` or `Miniville School`, passing their
  signature `knack` into an eager attendee's `skills_json`.
* **Personal Memoirs (`memoir_published`)** — the scholar pens a permanent
  bound memoir in `keepsakes` (`kind = 'book'`) and records a life reflection
  in `diary_entries`.
* **Conserved Lyceum Stipend** — when `town_account >= 200_000`, the town
  awards a strictly conserved `$35` (`3,500` cents) **Miniville Lyceum Stipend**
  from `town_account` to the instructor/author (`+2` standing).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .individual import _parse_json_list, clear_cache, profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

LYCEUM_STIPEND_CENTS = 3_500     # $35 conserved stipend from town_account
TOWN_LYCEUM_FLOOR = 200_000


def run_sabbatical_lyceum(conn: sqlite3.Connection, tick: int,
                          seed: str = "miniville") -> dict[str, Any] | None:
    """Run a weekly Sabbatical Memoir or Town Masterclass."""
    day = day_of(tick)
    if day % 7 != 4:
        return None

    already = conn.execute(
        "SELECT 1 FROM sabbaticals WHERE day = ? LIMIT 1", (day,)
    ).fetchone()
    if already:
        return None

    r = rng_for(seed, "sabbaticals", day)
    scholars = conn.execute(
        """SELECT id, name, age, occupation, household_id,
                  COALESCE(standing, 0) AS standing
           FROM agents
           WHERE alive = 1 AND is_child = 0
             AND (age >= 40 OR COALESCE(standing, 0) >= 8)
           ORDER BY COALESCE(standing, 0) DESC, age DESC, id ASC LIMIT 20"""
    ).fetchall()
    if not scholars:
        return None

    scholar = r.choice(scholars)
    sid = int(scholar["id"])
    prof = profile_of(conn, sid, seed)
    knack = str(prof.get("knack") or "their craft")

    lib = conn.execute(
        """SELECT id, name FROM places
           WHERE name IN ('Miniville Public Library', 'Miniville School')
           ORDER BY id ASC LIMIT 1"""
    ).fetchone()
    venue_id = int(lib["id"]) if lib else None
    venue_name = lib["name"] if lib else "Miniville Public Library"

    students = conn.execute(
        """SELECT id, name, skills_json FROM agents
           WHERE alive = 1 AND is_child = 0 AND id != ?
           ORDER BY age ASC, id ASC LIMIT 15""",
        (sid,),
    ).fetchall()

    kind = "masterclass" if (students and r.random() < 0.6) else "memoir"
    attendees_count = min(6, len(students))

    if kind == "masterclass" and students:
        prot = r.choice(students)
        pid = int(prot["id"])
        skills = _parse_json_list(prot["skills_json"])
        if knack.lower() not in {s.lower() for s in skills}:
            skills.insert(0, knack)
            conn.execute(
                "UPDATE agents SET skills_json = ? WHERE id = ?",
                (json.dumps(skills), pid),
            )
            clear_cache(conn)
        title = f"Masterclass on {knack.title()} by {scholar['name']}"
    else:
        kind = "memoir"
        title = f"Memoirs of {scholar['name']}: A Life in {knack.title()}"
        hist = json.dumps([{"event": "published_memoir", "by": scholar["name"], "tick": tick}])
        conn.execute(
            """INSERT INTO keepsakes(
                   owner_id, creator_id, venue_id, kind, title, craft,
                   created_tick, generations, appreciations, history_json)
               VALUES(?, ?, ?, 'book', ?, 'writing', ?, 1, 2, ?)""",
            (sid, sid, venue_id, title, tick, hist),
        )
        conn.execute(
            """INSERT INTO diary_entries(agent_id, tick, day, kind, origin, entry)
               VALUES(?, ?, ?, 'memoir', 'Miniville', ?)""",
            (sid, tick, day, f"Completed my personal memoir '{title}' and placed a copy in {venue_name}."),
        )

    stipend_paid = 0
    treasury = conn.execute(
        "SELECT balance_cents FROM town_account WHERE id = 1"
    ).fetchone()
    if treasury and int(treasury["balance_cents"]) >= TOWN_LYCEUM_FLOOR + LYCEUM_STIPEND_CENTS:
        conn.execute(
            "UPDATE town_account SET balance_cents = balance_cents - ? WHERE id = 1",
            (LYCEUM_STIPEND_CENTS,),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ?, fun = MIN(100, fun + 12) WHERE agent_id = ?",
            (LYCEUM_STIPEND_CENTS, sid),
        )
        stipend_paid = LYCEUM_STIPEND_CENTS

    conn.execute(
        "UPDATE agents SET standing = COALESCE(standing, 0) + 2 WHERE id = ?",
        (sid,),
    )
    conn.execute(
        """INSERT INTO sabbaticals(
               day, tick, scholar_id, kind, title, venue_id,
               attendees_count, stipend_cents)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, sid, kind, title, venue_id, attendees_count, stipend_paid),
    )

    tag = "masterclass_taught" if kind == "masterclass" else "memoir_published"
    emit(conn, tick, "life_event", place_id=venue_id, a=sid, importance=NOTABLE,
         text=f"{'taught' if kind == 'masterclass' else 'published'} '{title}' at {venue_name} (${stipend_paid / 100:,.0f} Lyceum stipend)",
         tag=tag, title=title, stipend_cents=stipend_paid)
    remember(conn, sid, tick,
             f"Shared '{title}' with the town at {venue_name}.",
             kind="reflection", importance=4)
    return {
        "day": day,
        "scholar_id": sid,
        "kind": kind,
        "title": title,
        "stipend_cents": stipend_paid,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    res = run_sabbatical_lyceum(conn, tick, seed)
    return {"sabbaticals": 1 if res else 0}


def sabbaticals_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tot = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(stipend_cents), 0) AS stp
           FROM sabbaticals"""
    ).fetchone()
    rows = conn.execute(
        """SELECT sb.*, a.name AS scholar_name, p.name AS venue_name
           FROM sabbaticals sb
           LEFT JOIN agents a ON a.id = sb.scholar_id
           LEFT JOIN places p ON p.id = sb.venue_id
           ORDER BY sb.id DESC LIMIT 15"""
    ).fetchall()
    return {
        "total_sabbaticals": int(tot["n"] or 0),
        "total_stipends_cents": int(tot["stp"] or 0),
        "recent": [dict(r) for r in rows],
    }
