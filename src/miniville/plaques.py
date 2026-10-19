"""Municipal Historical Plaques & Heritage Walking Tours (v0.42).

Every 10 days (`day % 10 == 6`), the **Miniville Historical Society** dedicates
a bronze **Municipal Historical Plaque** (`historical_plaques` table) at a
landmark venue (`plaque_dedicated`, `+2` standing for the honouree), and leads
a **Heritage Walking Tour** (`walking_tour`) where curious townsfolk tip the
docent guide a strictly conserved `$12–$20` wallet-to-wallet gratuity.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

DOCENT_TIP_MIN_CENTS = 1_200   # $12 conserved docent gratuity
DOCENT_TIP_MAX_CENTS = 2_000   # $20 conserved docent gratuity


def dedicate_plaque_and_tour(conn: sqlite3.Connection, tick: int,
                             seed: str = "miniville") -> dict[str, Any] | None:
    """Dedicate a Municipal Historical Plaque and lead a Heritage Walking Tour."""
    day = day_of(tick)
    if day % 10 != 6:
        return None

    already = conn.execute(
        "SELECT 1 FROM historical_plaques WHERE day = ? LIMIT 1", (day,)
    ).fetchone()
    if already:
        return None

    r = rng_for(seed, "historical_plaques", day)
    honouree = conn.execute(
        """SELECT a.id, a.name, a.occupation, COALESCE(a.standing, 0) AS standing,
                  a.work_place_id, a.home_place_id
           FROM agents a
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY COALESCE(a.standing, 0) DESC, a.age DESC, a.id ASC LIMIT 10"""
    ).fetchall()
    if not honouree:
        return None

    chosen = r.choice(honouree)
    hid = int(chosen["id"])
    pid = chosen["work_place_id"] or chosen["home_place_id"]
    pname = "Town Hall"
    if pid:
        prow = conn.execute("SELECT name FROM places WHERE id = ?", (pid,)).fetchone()
        if prow:
            pname = prow["name"]

    title = f"The {chosen['name']} Heritage Plaque at {pname}"
    inscription = (
        f"Dedicated on Day {day + 1} in honour of {chosen['name']} "
        f"({chosen['occupation'] or 'citizen'}) for lasting service to Miniville."
    )

    # Pick a docent tour guide and a tipping tourgoer (strictly conserved)
    guides = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND a.id != ?
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 15""",
        (hid,),
    ).fetchall()
    tip_cents = 0
    if len(guides) >= 2:
        patron = guides[0]
        docent = guides[-1]
        if int(patron["id"]) != int(docent["id"]) and int(patron["money_cents"]) >= 210_000:
            tip_cents = r.randint(DOCENT_TIP_MIN_CENTS, DOCENT_TIP_MAX_CENTS)
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ?, fun = MIN(100, fun + 6) WHERE agent_id = ?",
                (tip_cents, int(patron["id"])),
            )
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents + ?, social = MIN(100, social + 6) WHERE agent_id = ?",
                (tip_cents, int(docent["id"])),
            )

    conn.execute(
        "UPDATE agents SET standing = COALESCE(standing, 0) + 2 WHERE id = ?",
        (hid,),
    )
    conn.execute(
        """INSERT INTO historical_plaques(
               day, tick, place_id, honouree_id, title,
               inscription, tours_count, tip_cents)
           VALUES(?, ?, ?, ?, ?, ?, 1, ?)""",
        (day, tick, pid, hid, title, inscription, tip_cents),
    )

    emit(conn, tick, "life_event", place_id=pid, a=hid, importance=NOTABLE,
         text=f"was honoured with '{title}' during the Heritage Walking Tour (${tip_cents / 100:,.0f} docent tips)",
         tag="plaque_dedicated", title=title, tip_cents=tip_cents)
    remember(conn, hid, tick,
             f"The Historical Society unveiled '{title}' in my honour!",
             kind="reflection", importance=4)
    return {"day": day, "title": title, "honouree_id": hid, "tip_cents": tip_cents}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    res = dedicate_plaque_and_tour(conn, tick, seed)
    return {"plaques": 1 if res else 0}


def plaques_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tot = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(tip_cents), 0) AS tips
           FROM historical_plaques"""
    ).fetchone()
    rows = conn.execute(
        """SELECT hp.*, a.name AS honouree_name, p.name AS place_name
           FROM historical_plaques hp
           LEFT JOIN agents a ON a.id = hp.honouree_id
           LEFT JOIN places p ON p.id = hp.place_id
           ORDER BY hp.id DESC LIMIT 15"""
    ).fetchall()
    return {
        "total_plaques": int(tot["n"] or 0),
        "total_tips_cents": int(tot["tips"] or 0),
        "plaques": [dict(r) for r in rows],
    }
