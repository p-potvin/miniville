"""Community Potlucks & Block Parties (v0.59).

Every 14-21 days on a weekend, a Miniville district hosts a weather-aware
community potluck or outdoor block party. Neighbors gather, share a signature
dish, gain modest social/fun/stress relief and neighbor affinity warmth, and
chip in a small 100% conserved supply kitty to a local food/grocery/bakery
business (`agent_state.money_cents -> businesses.balance_cents`).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .rng import rng_for
from .seasons import date_of, season_of
from .timekeeper import TICKS_PER_DAY, is_weekend
from .weather import today as weather_today
from .world import DISTRICTS

MIN_CADENCE_DAYS = 14
MAX_CADENCE_DAYS = 21
KITTY_PER_ATTENDEE_CENTS = 250
MAX_ATTENDEES = 12

_THEMES: dict[str, list[str]] = {
    "spring": ["Spring Sprout Potluck", "Garden Herb Supper", "Blossom Street Table"],
    "summer": ["Lakeshore Cookout", "Midsummer Block Party", "Porch & Patio Feast"],
    "autumn": ["Harvest Casserole Night", "Apple Cider Social", "Amber Leaf Potluck"],
    "winter": ["Fireside Soup Swap", "Midwinter Comfort Table", "Lantern Supper Club"],
}

_DISHES: dict[str, list[str]] = {
    "spring": ["lemon herb pasta salad", "strawberry spinach tart", "spring pea risotto"],
    "summer": ["charred sweet corn & ribs", "peach cobbler", "smoked brisket sliders"],
    "autumn": ["butternut squash bisque", "warm apple crisp", "roast root medley"],
    "winter": ["three-bean chili", "baked potato chowder", "cinnamon bread pudding"],
}


def ensure_potlucks_schema(conn: sqlite3.Connection) -> None:
    """Create the `potlucks` table if it does not yet exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS potlucks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day INTEGER,
            year INTEGER,
            district TEXT,
            host_agent_id INTEGER,
            venue_name TEXT,
            theme TEXT,
            signature_dish TEXT,
            attendees_count INTEGER,
            supply_cents INTEGER,
            weather_mode TEXT,
            summary TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_potlucks_day ON potlucks(day)")


def _row_to_dict(r: sqlite3.Row, host_name: str | None = None) -> dict[str, Any]:
    d = {
        "id": int(r["id"]),
        "day": int(r["day"]),
        "year": int(r["year"]),
        "district": r["district"],
        "host_agent_id": int(r["host_agent_id"]) if r["host_agent_id"] is not None else None,
        "venue_name": r["venue_name"],
        "theme": r["theme"],
        "signature_dish": r["signature_dish"],
        "attendees_count": int(r["attendees_count"] or 0),
        "supply_cents": int(r["supply_cents"] or 0),
        "weather_mode": r["weather_mode"],
        "summary": r["summary"] or "",
    }
    if host_name is not None:
        d["host_name"] = host_name
    return d


def _get_potluck_for_day(conn: sqlite3.Connection, day: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT p.*, a.name AS host_name
           FROM potlucks p
           LEFT JOIN agents a ON a.id = p.host_agent_id
           WHERE p.day = ?
           LIMIT 1""",
        (int(day),),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row, row["host_name"])


def _is_potluck_due(conn: sqlite3.Connection, day: int, seed: str) -> bool:
    if not is_weekend(day * TICKS_PER_DAY):
        return False
    row = conn.execute(
        "SELECT MAX(day) AS last_day FROM potlucks WHERE day < ?",
        (int(day),),
    ).fetchone()
    last_day = row["last_day"] if row and row["last_day"] is not None else None
    if last_day is None:
        return True
    gap = int(day) - int(last_day)
    if gap < MIN_CADENCE_DAYS:
        return False
    if gap >= MAX_CADENCE_DAYS:
        return True
    return rng_for(seed, "potluck_due", int(day)).random() < 0.55


def _pick_district(conn: sqlite3.Connection, day: int, r) -> str:
    rows = conn.execute(
        """SELECT DISTINCT p.district
           FROM agents a
           JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1 AND a.is_child = 0 AND p.district IS NOT NULL
           ORDER BY p.district"""
    ).fetchall()
    active_districts = [row["district"] for row in rows if row["district"]]
    pool = active_districts or list(DISTRICTS)

    recent_rows = conn.execute(
        "SELECT district FROM potlucks ORDER BY day DESC, id DESC LIMIT 3"
    ).fetchall()
    recent = {row["district"] for row in recent_rows}
    fresh = [d for d in pool if d not in recent]
    choices = fresh or pool
    return choices[r.randrange(len(choices))]


def _pick_local_food_business(conn: sqlite3.Connection, district: str) -> int | None:
    """Return place_id of an open food/grocery/retail business (preferring `district`)."""
    try:
        rows = conn.execute(
            """SELECT p.id, p.district, p.tags
               FROM businesses b
               JOIN places p ON p.id = b.place_id
               WHERE b.status = 'open'
                 AND p.kind != 'home'
                 AND (p.tags LIKE '%"food"%' OR p.tags LIKE '%"retail"%')
               ORDER BY CASE WHEN p.district = ? THEN 0 ELSE 1 END,
                        CASE WHEN p.tags LIKE '%"food"%' THEN 0 ELSE 1 END,
                        p.id""",
            (district,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    return int(rows[0]["id"]) if rows else None


def _warm_pair(conn: sqlite3.Connection, a_id: int, b_id: int, tick: int) -> None:
    if a_id == b_id:
        return
    u, v = (a_id, b_id) if a_id < b_id else (b_id, a_id)
    row = conn.execute(
        "SELECT familiarity, affinity FROM relationships WHERE a_id = ? AND b_id = ?",
        (u, v),
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO relationships
               (a_id, b_id, familiarity, affinity, romance, label, interactions, last_met_tick)
               VALUES (?, ?, 2.0, 3.0, 0.0, 'acquaintance', 1, ?)""",
            (u, v, tick),
        )
    else:
        conn.execute(
            """UPDATE relationships
               SET familiarity = MIN(100.0, familiarity + 1.5),
                   affinity = MIN(100.0, affinity + 2.5),
                   interactions = interactions + 1,
                   last_met_tick = ?
               WHERE a_id = ? AND b_id = ?""",
            (tick, u, v),
        )


def daily_potlucks(
    conn: sqlite3.Connection,
    day: int,
    seed: str = "miniville",
) -> dict[str, Any] | None:
    """Maybe host a weekend district potluck / block party on `day` (idempotent)."""
    ensure_potlucks_schema(conn)
    day_i = int(day)
    existing = _get_potluck_for_day(conn, day_i)
    if existing is not None:
        return existing

    if not _is_potluck_due(conn, day_i, seed):
        return None

    r = rng_for(seed, "potluck", day_i)
    district = _pick_district(conn, day_i, r)

    candidates = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1 AND a.is_child = 0 AND p.district = ?
           ORDER BY a.id""",
        (district,),
    ).fetchall()
    if not candidates:
        candidates = conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a
               JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0
               ORDER BY a.id"""
        ).fetchall()
    if not candidates:
        return None

    pool = list(candidates)
    r.shuffle(pool)
    attendees = pool[:MAX_ATTENDEES]
    host = attendees[0]
    host_id = int(host["id"])
    host_name = str(host["name"])
    attendees_count = len(attendees)

    season = season_of(day_i)
    w = weather_today(conn, day_i)
    is_outdoor = (
        int(w.get("severity", 0)) == 0
        and w.get("condition") in ("clear", "breezy", "overcast")
        and (season != "winter" or int(w.get("temp_f", 30)) >= 45)
    )
    if is_outdoor:
        weather_mode = "outdoor_block_party"
        venue_name = f"{district} Block Party"
    else:
        weather_mode = "indoor_potluck"
        first_name = host_name.split()[0] if host_name else "Neighbor"
        venue_name = f"{first_name}'s Indoor Potluck ({district})"

    themes = _THEMES.get(season, _THEMES["spring"])
    dishes = _DISHES.get(season, _DISHES["spring"])
    theme = themes[r.randrange(len(themes))]
    signature_dish = dishes[r.randrange(len(dishes))]

    tick = day_i * TICKS_PER_DAY
    attendee_ids = [int(a["id"]) for a in attendees]

    # Modest social/fun/stress boost for attendees
    for aid in attendee_ids:
        conn.execute(
            """UPDATE agent_state
               SET social = MIN(100.0, social + 6.0),
                   fun = MIN(100.0, fun + 5.0),
                   stress = MAX(0.0, stress - 3.0)
               WHERE agent_id = ?""",
            (aid,),
        )

    # Neighbor affinity warmth between host and guests
    for guest_id in attendee_ids[1:]:
        _warm_pair(conn, host_id, guest_id, tick)

    # 100% conserved supply kitty: agent_state.money_cents -> local food/retail business balance_cents
    supply_cents = 0
    biz_id = _pick_local_food_business(conn, district)
    if biz_id is not None:
        for a in attendees:
            if int(a["money_cents"]) >= KITTY_PER_ATTENDEE_CENTS:
                conn.execute(
                    "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                    (KITTY_PER_ATTENDEE_CENTS, int(a["id"])),
                )
                supply_cents += KITTY_PER_ATTENDEE_CENTS
        if supply_cents > 0:
            conn.execute(
                "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
                (supply_cents, biz_id),
            )

    year, _, _ = date_of(day_i)
    mode_label = "outdoor block party" if is_outdoor else "cozy indoor potluck"
    summary = (
        f"{host_name} hosted {theme} ({mode_label}) in {district} "
        f"with {attendees_count} neighbors and {signature_dish}"
    )

    cur = conn.execute(
        """INSERT INTO potlucks
           (day, year, district, host_agent_id, venue_name, theme, signature_dish,
            attendees_count, supply_cents, weather_mode, summary)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            day_i,
            year,
            district,
            host_id,
            venue_name,
            theme,
            signature_dish,
            attendees_count,
            supply_cents,
            weather_mode,
            summary,
        ),
    )

    emit(
        conn,
        tick,
        "town_event",
        a=host_id,
        importance=NOTABLE,
        text=summary,
        tag="potluck_hosted",
        day=day_i,
        district=district,
        weather_mode=weather_mode,
        theme=theme,
        signature_dish=signature_dish,
        attendees_count=attendees_count,
        supply_cents=supply_cents,
    )

    return {
        "id": int(cur.lastrowid),
        "day": day_i,
        "year": year,
        "district": district,
        "host_agent_id": host_id,
        "host_name": host_name,
        "venue_name": venue_name,
        "theme": theme,
        "signature_dish": signature_dish,
        "attendees_count": attendees_count,
        "supply_cents": supply_cents,
        "weather_mode": weather_mode,
        "summary": summary,
    }


def list_potlucks(conn: sqlite3.Connection, limit: int = 16) -> list[dict[str, Any]]:
    """Return recent potlucks, newest first."""
    try:
        rows = conn.execute(
            """SELECT p.*, a.name AS host_name
               FROM potlucks p
               LEFT JOIN agents a ON a.id = p.host_agent_id
               ORDER BY p.day DESC, p.id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_dict(r, r["host_name"]) for r in rows]


def potlucks_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate potluck stats plus recent entries."""
    try:
        agg = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(attendees_count), 0) AS total_att,
                      COALESCE(SUM(supply_cents), 0) AS total_sup
               FROM potlucks"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "total_potlucks": 0,
            "total_attendees": 0,
            "total_supply_cents": 0,
            "latest": None,
            "potlucks": [],
        }
    recent = list_potlucks(conn, limit=16)
    return {
        "total_potlucks": int(agg["cnt"]),
        "total_attendees": int(agg["total_att"]),
        "total_supply_cents": int(agg["total_sup"]),
        "latest": recent[0] if recent else None,
        "potlucks": recent,
    }
