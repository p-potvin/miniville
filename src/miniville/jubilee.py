"""Annual Town Founders' Jubilee, Time Capsules & Key to Miniville (v0.39).

Every 20 days (`day > 0 and day % 20 == 0`), Miniville gathers at `Town Hall`
and `Lush Meadow Park` for the **Founders' Jubilee & Time Capsule Sealing**
(`time_capsules` table):

* **Time Capsule Sealing (`time_capsule_sealed`)** — preserves a snapshot of
  town culture in `items_json` (featured keepsake, latest WMNV 94.3 FM radio
  show, leading sports team, and a cherished household tradition).
* **Key to Miniville (`key_to_the_town`)** — honours the resident with the
  strongest record of neighbourly service and civic standing. When
  `town_account >= 200_000`, the honouree receives a strictly conserved `$50`
  (`5,000` cents) **Founders' Civic Honorarium** from `town_account` and `+3`
  standing.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .memory import remember
from .seasons import season_of
from .timekeeper import day_of

HONORARIUM_CENTS = 5_000         # $50 conserved Founders' Honorarium from town_account
TOWN_JUBILEE_FLOOR = 200_000


def celebrate_jubilee(conn: sqlite3.Connection, tick: int,
                      seed: str = "miniville") -> dict[str, Any] | None:
    """Celebrate the Founders' Jubilee, seal a Time Capsule, and award the Key to Miniville."""
    day = day_of(tick)
    if day == 0 or day % 20 != 0:
        return None

    already = conn.execute(
        "SELECT 1 FROM time_capsules WHERE day = ? LIMIT 1", (day,)
    ).fetchone()
    if already:
        return None

    season = season_of(day)
    hall = conn.execute(
        "SELECT id, name FROM places WHERE name = 'Town Hall' LIMIT 1"
    ).fetchone()
    hall_id = int(hall["id"]) if hall else None

    # Pick the Key to Miniville honouree (top civic standing + neighbourly deeds)
    honouree = conn.execute(
        """SELECT a.id, a.name, a.occupation, COALESCE(a.standing, 0) AS standing
           FROM agents a
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY COALESCE(a.standing, 0) DESC, a.id ASC LIMIT 1"""
    ).fetchone()
    if not honouree:
        return None
    hid = int(honouree["id"])

    # Gather cultural artifacts for the Time Capsule
    top_ks = conn.execute(
        "SELECT title FROM keepsakes ORDER BY appreciations DESC, id DESC LIMIT 1"
    ).fetchone()
    top_radio = conn.execute(
        "SELECT title FROM radio_broadcasts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    top_trad = conn.execute(
        "SELECT tradition FROM households WHERE tradition IS NOT NULL AND tradition != '' ORDER BY id ASC LIMIT 1"
    ).fetchone()

    capsule_items = [
        top_ks["title"] if top_ks else "a hand-carved oak town seal",
        top_radio["title"] if top_radio else "a WMNV 94.3 FM reel-to-reel tape",
        f"a recipe card for {top_trad['tradition']}" if top_trad else "a handwritten town charter",
    ]
    theme = f"Day {day + 1} {season.title()} Founders' Jubilee Capsule"

    honorarium_paid = 0
    treasury = conn.execute(
        "SELECT balance_cents FROM town_account WHERE id = 1"
    ).fetchone()
    if treasury and int(treasury["balance_cents"]) >= TOWN_JUBILEE_FLOOR + HONORARIUM_CENTS:
        conn.execute(
            "UPDATE town_account SET balance_cents = balance_cents - ? WHERE id = 1",
            (HONORARIUM_CENTS,),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ?, fun = MIN(100, fun + 14) WHERE agent_id = ?",
            (HONORARIUM_CENTS, hid),
        )
        honorarium_paid = HONORARIUM_CENTS

    conn.execute(
        "UPDATE agents SET standing = COALESCE(standing, 0) + 3 WHERE id = ?",
        (hid,),
    )
    conn.execute(
        """INSERT INTO time_capsules(
               day, tick, season, honouree_id, theme, items_json, honorarium_cents)
           VALUES(?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, season, hid, theme, json.dumps(capsule_items), honorarium_paid),
    )

    emit(conn, tick, "life_event", place_id=hall_id, a=hid, importance=NOTABLE,
         text=f"received the Key to Miniville and sealed the {theme} at Town Hall",
         tag="key_to_the_town", theme=theme, honorarium_cents=honorarium_paid)
    remember(conn, hid, tick,
             f"I was awarded the Key to Miniville and sealed the {theme} at Town Hall!",
             kind="reflection", importance=5)
    return {
        "day": day,
        "theme": theme,
        "honouree_id": hid,
        "honouree_name": honouree["name"],
        "items": capsule_items,
        "honorarium_cents": honorarium_paid,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    res = celebrate_jubilee(conn, tick, seed)
    return {"jubilees": 1 if res else 0}


def jubilee_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT tc.*, a.name AS honouree_name
           FROM time_capsules tc
           LEFT JOIN agents a ON a.id = tc.honouree_id
           ORDER BY tc.id DESC LIMIT 12"""
    ).fetchall()
    capsules = []
    for r in rows:
        d = dict(r)
        try:
            d["items"] = json.loads(d.get("items_json") or "[]")
        except Exception:
            d["items"] = []
        capsules.append(d)
    return {
        "total_capsules": len(capsules),
        "capsules": capsules,
    }
