"""Town Book Club, Serialized Reading Circles & Author Signings (v0.37).

Every 5 days at `Miniville Public Library` (or `The Scenic Bean`), the
**Miniville Literary Circle** selects a featured book (`book_club_selections`
table):

* **Local Author Signings (`author_signing`)** — when a resident writer has a
  literary work in `keepsakes` (or belongs to *The Lantern & Quill Writers'
  Guild*), the Book Club features their book and hosts a signing night. A
  comfortable reader (`money_cents >= 220_000`) buys a signed edition for a
  strictly conserved `$16–$26` wallet-to-wallet royalty transfer, boosting
  `keepsakes.appreciations` (`+2`) and warming literary friendships.
* **Seasonal Reading Circles (`book_club_meeting`)** — members gather to debate
  the selection, lifting `fun` (`+6`) and `social` (`+5`).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .seasons import season_of
from .timekeeper import day_of

ROYALTY_MIN_CENTS = 1_600   # $16 conserved book-signing royalty
ROYALTY_MAX_CENTS = 2_600   # $26 conserved book-signing royalty

_CLASSIC_TITLES: tuple[str, ...] = (
    "Almanac of the Northern Riverbend",
    "Winter Lanterns on Main Street",
    "The Orchard Keeper's Notebook",
    "Letters from Greenhill Ridge",
    "The Clockmaker of Old Mill Quarter",
    "Field Guide to Lakeshore Birds",
)


def run_book_club(conn: sqlite3.Connection, tick: int,
                  seed: str = "miniville") -> dict[str, Any] | None:
    """Host a Literary Circle selection and author signing every 5 days."""
    day = day_of(tick)
    if day % 5 != 4:
        return None

    already = conn.execute(
        "SELECT 1 FROM book_club_selections WHERE day = ? LIMIT 1", (day,)
    ).fetchone()
    if already:
        return None

    r = rng_for(seed, "book_club", day)
    season = season_of(day)
    venue = conn.execute(
        """SELECT id, name FROM places
           WHERE name IN ('Miniville Public Library', 'The Scenic Bean')
           ORDER BY id ASC LIMIT 1"""
    ).fetchone()
    venue_id = int(venue["id"]) if venue else None
    venue_name = venue["name"] if venue else "Miniville Public Library"

    readers = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (a.hobbies_json LIKE '%read%' OR a.hobbies_json LIKE '%book%'
                  OR a.hobbies_json LIKE '%poet%' OR a.hobbies_json LIKE '%writ%'
                  OR a.skills_json LIKE '%writ%')
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 30"""
    ).fetchall()
    if len(readers) < 2:
        readers = conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0
               ORDER BY s.money_cents DESC, a.id ASC LIMIT 15"""
        ).fetchall()
    if not readers:
        return None

    selector = r.choice(readers)
    sel_id = int(selector["id"])

    # Prefer a local resident's book/craft keepsake if available
    local_book = conn.execute(
        """SELECT k.id, k.title, k.creator_id, a.name AS author_name
           FROM keepsakes k
           JOIN agents a ON a.id = k.creator_id
           WHERE a.alive = 1 AND (k.kind = 'book' OR k.craft IN ('writing', 'poetry', 'storytelling', 'history'))
           ORDER BY k.appreciations ASC, k.id DESC LIMIT 10"""
    ).fetchall()

    author_id = None
    author_name = "Traditional"
    royalty_cents = 0

    if local_book:
        picked = r.choice(local_book)
        title = picked["title"]
        author_id = int(picked["creator_id"])
        author_name = picked["author_name"]
        conn.execute(
            "UPDATE keepsakes SET appreciations = COALESCE(appreciations, 0) + 2 WHERE id = ?",
            (int(picked["id"]),),
        )
        # Conserved book-signing purchase from a reader to the local author
        rich_readers = [
            rd for rd in readers
            if int(rd["id"]) != author_id and int(rd["money_cents"]) >= 220_000
        ]
        if rich_readers:
            buyer = rich_readers[0]
            bid = int(buyer["id"])
            royalty_cents = r.randint(ROYALTY_MIN_CENTS, ROYALTY_MAX_CENTS)
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (royalty_cents, bid),
            )
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                (royalty_cents, author_id),
            )
    else:
        title = r.choice(_CLASSIC_TITLES)

    rids = [int(x["id"]) for x in readers[:10]]
    if rids:
        ph = ",".join("?" * len(rids))
        conn.execute(
            f"""UPDATE agent_state
                SET fun = MIN(100, fun + 6),
                    social = MIN(100, social + 5),
                    stress = MAX(0, stress - 3)
                WHERE agent_id IN ({ph})""",
            rids,
        )

    conn.execute(
        """INSERT INTO book_club_selections(
               day, tick, season, title, author_id, selector_id,
               venue_id, readers_count, royalty_cents)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, season, title, author_id, sel_id, venue_id, len(rids), royalty_cents),
    )

    if author_id and author_id != sel_id:
        lo, hi = min(sel_id, author_id), max(sel_id, author_id)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,7.0,9.0,0.0,'friend',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.5),
                 affinity = MIN(100.0, relationships.affinity + 4.5),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        emit(conn, tick, "life_event", place_id=venue_id, a=author_id, b=sel_id,
             importance=NOTABLE,
             text=f"signed copies of '{title}' for the Miniville Book Club at {venue_name} (${royalty_cents / 100:,.0f} royalties)",
             tag="author_signing", title=title, royalty_cents=royalty_cents)
        remember(conn, author_id, tick,
                 f"The Miniville Book Club featured my work '{title}' at {venue_name}!",
                 kind="reflection", importance=4)
    else:
        emit(conn, tick, "life_event", place_id=venue_id, a=sel_id,
             importance=MINOR,
             text=f"led the Miniville Book Club discussion of '{title}' ({author_name}) at {venue_name}",
             tag="book_club_meeting", title=title)
        remember(conn, sel_id, tick,
                 f"Hosted the Book Club discussion of '{title}' at {venue_name}.",
                 kind="personal", importance=3)

    return {
        "day": day,
        "title": title,
        "author_id": author_id,
        "readers_count": len(rids),
        "royalty_cents": royalty_cents,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    res = run_book_club(conn, tick, seed)
    return {"meetings": 1 if res else 0}


def book_club_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT b.*, aa.name AS author_name, sa.name AS selector_name,
                  p.name AS venue_name
           FROM book_club_selections b
           LEFT JOIN agents aa ON aa.id = b.author_id
           LEFT JOIN agents sa ON sa.id = b.selector_id
           LEFT JOIN places p ON p.id = b.venue_id
           ORDER BY b.id DESC LIMIT 20"""
    ).fetchall()
    tot = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(royalty_cents), 0) AS roy
           FROM book_club_selections"""
    ).fetchone()
    return {
        "total_selections": int(tot["n"] or 0),
        "total_royalties_cents": int(tot["roy"] or 0),
        "selections": [dict(r) for r in rows],
    }
