"""Dynamic Town Expansion, Lot Zoning & New Venue Construction (v0.60).

On a bounded cadence (every 30-45 days when ``town_account >= 600_000`` cents),
Miniville charters and completes a district expansion project:
* Constructing one of up to 5 new public/civic landmarks in ``places``
  (``kind='public'`` with public amenity tags so commercial staffing invariants
  are never diluted), OR
* Adding capacity (``+4`` to ``+8``) to an existing venue as a ``wing_addition``.

All construction costs (45,000-90,000 cents) are 100% conserved transfers from
``town_account`` to local builders (``agent_state.money_cents``) and supply
businesses (``businesses.balance_cents``).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import economy
from .events import MAJOR, emit
from .rng import rng_for
from .seasons import date_of
from .timekeeper import TICKS_PER_DAY

MIN_CADENCE_DAYS = 30
MAX_CADENCE_DAYS = 45
P_EXPAND_IN_WINDOW = 0.25
PURSE_FLOOR_CENTS = 600_000
COST_MIN_CENTS = 45_000
COST_MAX_CENTS = 90_000
MAX_NEW_LANDMARKS = 5

LANDMARK_CATALOG: tuple[tuple[str, str, int, int, int, list[str]], ...] = (
    ("Lakeshore Boardwalk Pavilion", "Lakeshore", 45, 8, 44, ["outdoors", "community"]),
    ("Eastside Makerspace", "Downtown", 35, 14, 42, ["community", "study"]),
    ("Greenhill Conservatory", "Greenhill", 40, 12, 42, ["quiet", "community"]),
    ("Old Mill Amphitheater", "Old Mill Quarter", 60, 16, 46, ["community", "outdoors"]),
    ("Flats Union Hall", "The Flats", 50, 14, 44, ["community", "civic"]),
)


def ensure_expansion_schema(conn: sqlite3.Connection) -> None:
    """Create the `town_expansions` table and index if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS town_expansions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            day                 INTEGER NOT NULL,
            year                INTEGER NOT NULL,
            district            TEXT NOT NULL,
            kind                TEXT NOT NULL,
            place_id            INTEGER REFERENCES places(id),
            place_name          TEXT NOT NULL,
            cost_cents          INTEGER NOT NULL DEFAULT 0,
            sponsor_name        TEXT NOT NULL DEFAULT '',
            builders_paid_cents INTEGER NOT NULL DEFAULT 0,
            status              TEXT NOT NULL DEFAULT 'completed',
            summary             TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_town_expansions_day ON town_expansions(day DESC)"
    )


def _select_builders(conn: sqlite3.Connection, r) -> list[int]:
    adults = conn.execute(
        """SELECT a.id, a.occupation
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY a.id ASC"""
    ).fetchall()
    if not adults:
        return []
    trades_kws = ("carpenter", "mechanic", "builder", "construction", "electrician", "plumber", "repair", "welder", "laborer")
    skilled = [
        int(a["id"])
        for a in adults
        if a["occupation"] and any(k in str(a["occupation"]).lower() for k in trades_kws)
    ]
    pool = skilled if skilled else [int(a["id"]) for a in adults]
    count = min(4, len(pool))
    return sorted(r.sample(pool, count))


def _select_supply_businesses(conn: sqlite3.Connection, r) -> list[int]:
    economy.ensure_businesses(conn)
    rows = conn.execute(
        """SELECT b.place_id, p.tags
           FROM businesses b
           JOIN places p ON p.id = b.place_id
           WHERE b.status = 'open' AND p.kind != 'home'
           ORDER BY b.place_id ASC"""
    ).fetchall()
    preferred: list[int] = []
    fallback: list[int] = []
    for row in rows:
        tags = set(json.loads(row["tags"] or "[]"))
        if tags & economy.PUBLIC_TAGS:
            continue
        pid = int(row["place_id"])
        if tags & {"trades", "retail"}:
            preferred.append(pid)
        else:
            fallback.append(pid)
    pool = preferred if preferred else fallback
    if not pool:
        return []
    count = min(2, len(pool))
    return sorted(r.sample(pool, count))


def _pick_sponsor(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """SELECT name FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY COALESCE(standing, 0) DESC, id ASC
           LIMIT 1"""
    ).fetchone()
    return str(row["name"]) if row else "Miniville Town Council"


def daily_expansion(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Evaluate and execute a conserved town expansion project when due."""
    ensure_expansion_schema(conn)
    day_i = int(day)

    existing = conn.execute(
        "SELECT * FROM town_expansions WHERE day = ? LIMIT 1", (day_i,)
    ).fetchone()
    if existing:
        out = dict(existing)
        out["expanded"] = False
        out["already_ran"] = True
        return out

    if day_i < MIN_CADENCE_DAYS:
        return {"day": day_i, "expanded": False, "reason": "early"}

    last_row = conn.execute(
        "SELECT MAX(day) AS last_day FROM town_expansions"
    ).fetchone()
    last_day = int(last_row["last_day"]) if last_row and last_row["last_day"] is not None else 0
    gap = day_i - last_day
    if gap < MIN_CADENCE_DAYS:
        return {"day": day_i, "expanded": False, "reason": "cadence"}

    r = rng_for(seed, "expansion", day_i)
    if gap < MAX_CADENCE_DAYS and r.random() >= P_EXPAND_IN_WINDOW:
        return {"day": day_i, "expanded": False, "reason": "window_skip"}

    purse = economy.town_balance(conn)
    if purse < PURSE_FLOOR_CENTS:
        return {"day": day_i, "expanded": False, "reason": "insufficient_purse"}

    builders = _select_builders(conn, r)
    suppliers = _select_supply_businesses(conn, r)
    if not builders and not suppliers:
        return {"day": day_i, "expanded": False, "reason": "no_contractors"}

    cost_cents = min(purse, r.randint(COST_MIN_CENTS, COST_MAX_CENTS))
    if builders and suppliers:
        builders_paid = (cost_cents * 65) // 100
        supply_paid = cost_cents - builders_paid
    elif builders:
        builders_paid = cost_cents
        supply_paid = 0
    else:
        builders_paid = 0
        supply_paid = cost_cents

    # Decide between new public landmark (capped at MAX_NEW_LANDMARKS) or wing_addition
    existing_places = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM places").fetchall()
    }
    landmarks_built = conn.execute(
        "SELECT COUNT(*) AS c FROM town_expansions WHERE kind = 'new_landmark'"
    ).fetchone()["c"]
    available_landmarks = [
        lm for lm in LANDMARK_CATALOG if lm[0] not in existing_places
    ]

    expand_candidates = conn.execute(
        """SELECT p.id, p.name, p.district, p.capacity
           FROM places p
           LEFT JOIN businesses b ON b.place_id = p.id
           WHERE p.kind != 'home' AND COALESCE(b.status, 'open') = 'open'
           ORDER BY COALESCE(b.ema_traffic, 0) DESC, p.capacity DESC, p.id ASC
           LIMIT 8"""
    ).fetchall()

    do_landmark = bool(
        available_landmarks
        and int(landmarks_built) < MAX_NEW_LANDMARKS
        and (not expand_candidates or r.random() < 0.55)
    )

    if do_landmark:
        lm_name, lm_district, lm_cap, lm_open, lm_close, lm_tags = available_landmarks[0]
        cur = conn.execute(
            """INSERT INTO places(name, kind, district, capacity, open_tick, close_tick, tags)
               VALUES(?, 'public', ?, ?, ?, ?, ?)""",
            (lm_name, lm_district, lm_cap, lm_open, lm_close, json.dumps(lm_tags)),
        )
        place_id = int(cur.lastrowid)
        economy.ensure_businesses(conn)
        kind = "new_landmark"
        place_name = lm_name
        district = lm_district
        summary = (
            f"Constructed new public landmark {lm_name} in {lm_district} "
            f"(capacity {lm_cap}, ${cost_cents / 100:,.2f})"
        )
    else:
        if not expand_candidates:
            return {"day": day_i, "expanded": False, "reason": "no_venues"}
        target = r.choice(expand_candidates)
        place_id = int(target["id"])
        place_name = str(target["name"])
        district = str(target["district"])
        delta = r.randint(4, 8)
        new_cap = int(target["capacity"]) + delta
        conn.execute(
            "UPDATE places SET capacity = ? WHERE id = ?", (new_cap, place_id)
        )
        kind = "wing_addition"
        summary = (
            f"Expanded {place_name} in {district} (+{delta} capacity to {new_cap}, "
            f"${cost_cents / 100:,.2f})"
        )

    # Execute 100% conserved transfers: town_account -> builders & suppliers
    short = economy.town_debit(conn, cost_cents)
    if short > 0:
        economy.town_credit(conn, short)
        return {"day": day_i, "expanded": False, "reason": "debit_shortfall"}

    if builders_paid > 0 and builders:
        per_b, rem_b = divmod(builders_paid, len(builders))
        for idx, aid in enumerate(builders):
            amt = per_b + (1 if idx < rem_b else 0)
            if amt > 0:
                conn.execute(
                    "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                    (amt, aid),
                )

    if supply_paid > 0 and suppliers:
        per_s, rem_s = divmod(supply_paid, len(suppliers))
        for idx, pid in enumerate(suppliers):
            amt = per_s + (1 if idx < rem_s else 0)
            if amt > 0:
                conn.execute(
                    "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
                    (amt, pid),
                )

    year, _, _ = date_of(day_i)
    sponsor_name = _pick_sponsor(conn)
    cur_exp = conn.execute(
        """INSERT INTO town_expansions(
               day, year, district, kind, place_id, place_name,
               cost_cents, sponsor_name, builders_paid_cents, status, summary)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)""",
        (
            day_i, year, district, kind, place_id, place_name,
            cost_cents, sponsor_name, builders_paid, summary,
        ),
    )
    exp_id = int(cur_exp.lastrowid)

    emit(
        conn,
        day_i * TICKS_PER_DAY,
        "town_expansion",
        place_id=place_id,
        importance=MAJOR,
        expansion_id=exp_id,
        district=district,
        expansion_kind=kind,
        place_name=place_name,
        cost_cents=cost_cents,
        builders_paid_cents=builders_paid,
        sponsor_name=sponsor_name,
        text=summary,
    )

    return {
        "id": exp_id,
        "day": day_i,
        "year": year,
        "district": district,
        "kind": kind,
        "place_id": place_id,
        "place_name": place_name,
        "cost_cents": cost_cents,
        "sponsor_name": sponsor_name,
        "builders_paid_cents": builders_paid,
        "status": "completed",
        "summary": summary,
        "expanded": True,
    }


def list_expansions(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent town expansions ordered newest-first."""
    try:
        rows = conn.execute(
            """SELECT * FROM town_expansions
               ORDER BY day DESC, id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def expansion_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate town expansion metrics and recent projects."""
    try:
        agg = conn.execute(
            """SELECT COUNT(*) AS total_expansions,
                      SUM(CASE WHEN kind = 'new_landmark' THEN 1 ELSE 0 END) AS landmarks_built,
                      SUM(CASE WHEN kind = 'wing_addition' THEN 1 ELSE 0 END) AS wings_added,
                      COALESCE(SUM(cost_cents), 0) AS total_cost_cents,
                      COALESCE(SUM(builders_paid_cents), 0) AS total_builders_paid_cents
               FROM town_expansions"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "total_expansions": 0,
            "landmarks_built": 0,
            "wings_added": 0,
            "total_cost_cents": 0,
            "total_builders_paid_cents": 0,
            "latest": None,
            "expansions": [],
        }

    recent = list_expansions(conn, limit=20)
    return {
        "total_expansions": int(agg["total_expansions"] or 0),
        "landmarks_built": int(agg["landmarks_built"] or 0),
        "wings_added": int(agg["wings_added"] or 0),
        "total_cost_cents": int(agg["total_cost_cents"] or 0),
        "total_builders_paid_cents": int(agg["total_builders_paid_cents"] or 0),
        "latest": recent[0] if recent else None,
        "expansions": recent,
    }
