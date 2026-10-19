"""Town Theater Troupe & Playbill Productions (v0.40).

Every 6 days at `Grand Theater` (or `Miniville Community Center`), the
**Miniville Players** stage an original playbill production
(`theater_productions` table):

* **Playwrights & Lead Actors (`stage_production`)** — pairs a local writer or
  storyteller with a dramatic/musical lead actor to stage a seasonal play
  (*Autumn Moon Over Lakeshore*, *The Baker of Old Mill Quarter*, *Midwinter
  at the Quaint Corner*, *Springtime on Greenhill Ridge*).
* **Conserved Box-Office Royalties (`theater_premiere`)** — comfortable
  audience members (`money_cents >= 210_000`) buy premiere tickets for a
  strictly conserved `$14–$24` wallet-to-wallet box-office transfer paid to the
  lead actor/playwright, lifting audience `fun` (`+8`) and `social` (`+5`), and
  awarding `+2` civic standing to the cast.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .seasons import season_of
from .timekeeper import day_of

TICKET_MIN_CENTS = 1_400   # $14 conserved premiere ticket
TICKET_MAX_CENTS = 2_400   # $24 conserved premiere ticket

_PLAYBILLS: dict[str, tuple[str, ...]] = {
    "spring": (
        "Springtime on Greenhill Ridge",
        "The Ferryman's Promise",
        "Blossoms at Lush Meadow",
    ),
    "summer": (
        "Midsummer Night at Lakeshore Marina",
        "The Brass Lantern Mystery",
        "Picnic on Main Street",
    ),
    "autumn": (
        "Autumn Moon Over the Old Mill",
        "The Harvest Clockmaker",
        "Whispers in the Orchard",
    ),
    "winter": (
        "Midwinter at the Quaint Corner",
        "Snowfall on the Riverbend",
        "The Bellringer's Eve",
    ),
}


def stage_production(conn: sqlite3.Connection, tick: int,
                     seed: str = "miniville") -> dict[str, Any] | None:
    """Stage a Miniville Players theater production every 6 days."""
    day = day_of(tick)
    if day % 6 != 4:
        return None

    already = conn.execute(
        "SELECT 1 FROM theater_productions WHERE day = ? LIMIT 1", (day,)
    ).fetchone()
    if already:
        return None

    r = rng_for(seed, "theater_troupe", day)
    season = season_of(day)
    venue = conn.execute(
        """SELECT id, name FROM places
           WHERE name IN ('Grand Theater', 'Miniville Community Center')
           ORDER BY id ASC LIMIT 1"""
    ).fetchone()
    venue_id = int(venue["id"]) if venue else None
    venue_name = venue["name"] if venue else "Grand Theater"

    thespians = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (a.hobbies_json LIKE '%theat%' OR a.hobbies_json LIKE '%music%'
                  OR a.hobbies_json LIKE '%sing%' OR a.hobbies_json LIKE '%writ%'
                  OR a.hobbies_json LIKE '%poet%' OR a.skills_json LIKE '%story%')
           ORDER BY a.id ASC LIMIT 30"""
    ).fetchall()
    if len(thespians) < 2:
        thespians = conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0 ORDER BY a.id ASC LIMIT 15"""
        ).fetchall()
    if len(thespians) < 2:
        return None

    playwright, lead_actor = r.sample(list(thespians), 2)
    pwid = int(playwright["id"])
    laid = int(lead_actor["id"])
    title = r.choice(_PLAYBILLS.get(season, _PLAYBILLS["summer"]))

    patrons = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND a.id NOT IN (?, ?) AND s.money_cents >= 210000
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 12""",
        (pwid, laid),
    ).fetchall()

    box_office_cents = 0
    for idx_p, pat in enumerate(patrons[:2]):
        bid = int(pat["id"])
        ticket = r.randint(TICKET_MIN_CENTS, TICKET_MAX_CENTS)
        recipient_id = laid if idx_p == 0 else pwid
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
            (ticket, bid),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (ticket, recipient_id),
        )
        box_office_cents += ticket

    audience_ids = [pwid, laid] + [int(x["id"]) for x in patrons[:8]]
    if audience_ids:
        ph = ",".join("?" * len(audience_ids))
        conn.execute(
            f"""UPDATE agent_state
                SET fun = MIN(100, fun + 8),
                    social = MIN(100, social + 5),
                    stress = MAX(0, stress - 4)
                WHERE agent_id IN ({ph})""",
            audience_ids,
        )

    conn.execute(
        "UPDATE agents SET standing = COALESCE(standing, 0) + 2 WHERE id IN (?, ?)",
        (pwid, laid),
    )
    conn.execute(
        """INSERT INTO theater_productions(
               day, tick, season, title, playwright_id, lead_actor_id,
               venue_id, audience_count, box_office_cents)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, season, title, pwid, laid, venue_id, len(audience_ids), box_office_cents),
    )

    lo, hi = min(pwid, laid), max(pwid, laid)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,8.0,10.0,0.0,'friend',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 4.0),
             affinity = MIN(100.0, relationships.affinity + 5.0),
             interactions = relationships.interactions + 1,
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )

    text = (
        f"starred in {playwright['name']}'s play '{title}' at {venue_name} "
        f"before {len(audience_ids)} townsfolk (${box_office_cents / 100:,.0f} box office)"
    )
    emit(conn, tick, "life_event", place_id=venue_id, a=laid, b=pwid,
         importance=NOTABLE, text=text, tag="theater_premiere",
         title=title, box_office_cents=box_office_cents)
    remember(conn, laid, tick,
             f"Took a curtain call in '{title}' written by {playwright['name']} at {venue_name}!",
             kind="reflection", importance=4)
    remember(conn, pwid, tick,
             f"{lead_actor['name']} brought my play '{title}' to life on stage at {venue_name}!",
             kind="reflection", importance=4)
    return {
        "day": day,
        "title": title,
        "playwright_id": pwid,
        "lead_actor_id": laid,
        "audience_count": len(audience_ids),
        "box_office_cents": box_office_cents,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    res = stage_production(conn, tick, seed)
    return {"productions": 1 if res else 0}


def theater_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tot = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(box_office_cents), 0) AS box
           FROM theater_productions"""
    ).fetchone()
    rows = conn.execute(
        """SELECT tp.*, pa.name AS playwright_name, la.name AS lead_actor_name,
                  p.name AS venue_name
           FROM theater_productions tp
           LEFT JOIN agents pa ON pa.id = tp.playwright_id
           LEFT JOIN agents la ON la.id = tp.lead_actor_id
           LEFT JOIN places p ON p.id = tp.venue_id
           ORDER BY tp.id DESC LIMIT 15"""
    ).fetchall()
    return {
        "total_productions": int(tot["n"] or 0),
        "total_box_office_cents": int(tot["box"] or 0),
        "productions": [dict(r) for r in rows],
    }
