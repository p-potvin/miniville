"""Town Transit & Bus Loop (v0.57).

Simulates daily ridership across Miniville's two municipal bus routes:
* ``Line 1: Crosstown Express`` (Downtown / Greenhill / The Flats)
* ``Line 2: Lakeshore & Mill Shuttle`` (Lakeshore / Old Mill Quarter)

Ridership responds to cross-district commuting, rainy/stormy weather, and
active seasonal festivals. Adult fares (150 cents) are transferred strictly
from ``agent_state.money_cents`` to ``town_account.balance_cents`` (100%
conserved, zero net mint/burn); youth (``age < 18``) and elders (``age >= 65``)
ride free, and adults with ``money_cents < 150`` are skipped.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import economy, festivals, weather
from .events import MINOR, NOTABLE, emit
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY

FARE_CENTS = 150
ROUTE_1 = "Line 1: Crosstown Express"
ROUTE_2 = "Line 2: Lakeshore & Mill Shuttle"

DELAY_THRESHOLD_PCT = 80.0
DELAY_COOLDOWN_DAYS = 7
MILESTONE_COOLDOWN_DAYS = 14
MILESTONE_STEP = 500


def ensure_transit_schema(conn: sqlite3.Connection) -> None:
    """Create the `transit_days` table if it does not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transit_days (
            day                INTEGER PRIMARY KEY,
            total_rides        INTEGER NOT NULL DEFAULT 0,
            storm_rides        INTEGER NOT NULL DEFAULT 0,
            fare_revenue_cents INTEGER NOT NULL DEFAULT 0,
            on_time_pct        REAL NOT NULL DEFAULT 100.0,
            top_route          TEXT NOT NULL DEFAULT '',
            driver_agent_id    INTEGER REFERENCES agents(id),
            note               TEXT NOT NULL DEFAULT ''
        )
        """
    )


def _pick_driver(conn: sqlite3.Connection, day: int, seed: str) -> int | None:
    """Deterministically select an adult bus operator for `day`."""
    adults = conn.execute(
        """SELECT id, occupation FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY id ASC"""
    ).fetchall()
    if not adults:
        return None
    preferred = [
        int(a["id"])
        for a in adults
        if a["occupation"]
        and any(k in str(a["occupation"]).lower() for k in ("driver", "transit", "mechanic", "clerk"))
    ]
    pool = preferred if preferred else [int(a["id"]) for a in adults]
    r = rng_for(seed, "transit_driver", day)
    return int(r.choice(pool))


def _recently_emitted(conn: sqlite3.Connection, kind: str, day: int, cooldown: int) -> bool:
    row = conn.execute(
        "SELECT MAX(day) AS last_day FROM events WHERE kind = ?",
        (kind,),
    ).fetchone()
    if row is None or row["last_day"] is None:
        return False
    return (int(day) - int(row["last_day"])) < cooldown


def daily_transit(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Simulate one day of municipal bus loop ridership, fares, and punctuality."""
    ensure_transit_schema(conn)
    day_i = int(day)

    existing = conn.execute(
        "SELECT * FROM transit_days WHERE day = ?", (day_i,)
    ).fetchone()
    if existing:
        out = dict(existing)
        out["already_ran"] = True
        return out

    w = weather.today(conn, day_i)
    cond = str(w.get("condition", "clear"))
    severity = int(w.get("severity", 0))
    is_storm_or_rain = cond in ("rain", "storm", "snow", "blizzard") or severity >= 2

    af = festivals.active_festival(conn, day_i)
    has_festival = af is not None

    driver_id = _pick_driver(conn, day_i, seed)

    residents = conn.execute(
        """SELECT a.id, a.age, a.is_child,
                  s.money_cents,
                  hp.district AS home_district,
                  wp.district AS work_district
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN places hp ON hp.id = a.home_place_id
           LEFT JOIN places wp ON wp.id = a.work_place_id
           WHERE a.alive = 1
           ORDER BY a.id ASC"""
    ).fetchall()

    r = rng_for(seed, "transit", day_i)
    route_counts = {ROUTE_1: 0, ROUTE_2: 0}
    total_rides = 0
    storm_rides = 0
    fare_revenue = 0

    for res in residents:
        aid = int(res["id"])
        if driver_id is not None and aid == driver_id:
            continue

        age = int(res["age"]) if res["age"] is not None else 30
        is_free = bool(res["is_child"]) or age < 18 or age >= 65
        wallet = int(res["money_cents"])

        if not is_free and wallet < FARE_CENTS:
            continue

        home_d = res["home_district"]
        work_d = res["work_district"]
        cross_district = bool(home_d and work_d and home_d != work_d)

        p_ride = 0.12
        if cross_district:
            p_ride += 0.45
        if is_storm_or_rain:
            p_ride += 0.22
        if has_festival:
            p_ride += 0.15
        p_ride = min(0.90, p_ride)

        if r.random() >= p_ride:
            continue

        if not is_free:
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (FARE_CENTS, aid),
            )
            economy.town_credit(conn, FARE_CENTS)
            fare_revenue += FARE_CENTS

        total_rides += 1
        if is_storm_or_rain:
            storm_rides += 1

        if home_d in ("Lakeshore", "Old Mill Quarter") or work_d in ("Lakeshore", "Old Mill Quarter"):
            route = ROUTE_2 if r.random() < 0.70 else ROUTE_1
        else:
            route = ROUTE_1 if r.random() < 0.70 else ROUTE_2
        route_counts[route] += 1

    # Punctuality calculation
    base_pct = 96.0 + r.uniform(-2.0, 3.0)
    if cond in ("storm", "blizzard") or severity >= 2 and cond != "heatwave":
        base_pct -= 22.0
    elif cond == "heatwave":
        base_pct -= 10.0
    elif cond in ("rain", "snow", "fog"):
        base_pct -= 7.0
    on_time_pct = round(max(40.0, min(100.0, base_pct)), 1)

    top_route = ROUTE_2 if route_counts[ROUTE_2] > route_counts[ROUTE_1] else ROUTE_1

    note_parts = [f"{total_rides} rides"]
    if is_storm_or_rain:
        note_parts.append(f"heavy {cond} ridership")
    if has_festival:
        note_parts.append(f"festival shuttle ({af['name']})")
    note = ", ".join(note_parts)

    conn.execute(
        """INSERT INTO transit_days(
               day, total_rides, storm_rides, fare_revenue_cents,
               on_time_pct, top_route, driver_agent_id, note)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day_i,
            total_rides,
            storm_rides,
            fare_revenue,
            on_time_pct,
            top_route,
            driver_id,
            note,
        ),
    )

    tick = day_i * TICKS_PER_DAY
    if on_time_pct < DELAY_THRESHOLD_PCT and not _recently_emitted(conn, "transit_delay", day_i, DELAY_COOLDOWN_DAYS):
        emit(
            conn,
            tick,
            "transit_delay",
            a=driver_id,
            importance=MINOR,
            day=day_i,
            on_time_pct=on_time_pct,
            condition=cond,
            text=f"Bus loop delayed by {cond} ({on_time_pct:.1f}% on-time)",
        )

    cum_row = conn.execute(
        "SELECT COALESCE(SUM(total_rides), 0) AS s FROM transit_days"
    ).fetchone()
    cum_rides = int(cum_row["s"]) if cum_row else total_rides
    prev_cum = cum_rides - total_rides
    if (
        total_rides > 0
        and (cum_rides // MILESTONE_STEP) > (prev_cum // MILESTONE_STEP)
        and not _recently_emitted(conn, "transit_milestone", day_i, MILESTONE_COOLDOWN_DAYS)
    ):
        milestone_num = (cum_rides // MILESTONE_STEP) * MILESTONE_STEP
        emit(
            conn,
            tick,
            "transit_milestone",
            a=driver_id,
            importance=NOTABLE,
            day=day_i,
            cumulative_rides=cum_rides,
            milestone=milestone_num,
            text=f"Miniville Bus Loop surpassed {milestone_num:,} cumulative rides",
        )

    return {
        "day": day_i,
        "total_rides": total_rides,
        "storm_rides": storm_rides,
        "fare_revenue_cents": fare_revenue,
        "on_time_pct": on_time_pct,
        "top_route": top_route,
        "driver_agent_id": driver_id,
        "note": note,
        "already_ran": False,
    }


def transit_summary(conn: sqlite3.Connection, days: int = 14) -> dict[str, Any]:
    """Return aggregate transit stats and recent `transit_days` history."""
    try:
        agg = conn.execute(
            """SELECT COALESCE(SUM(total_rides), 0) AS total_rides,
                      COALESCE(SUM(storm_rides), 0) AS total_storm_rides,
                      COALESCE(SUM(fare_revenue_cents), 0) AS total_revenue_cents,
                      COALESCE(AVG(on_time_pct), 100.0) AS avg_on_time_pct
               FROM transit_days"""
        ).fetchone()
        rows = conn.execute(
            """SELECT t.*, a.name AS driver_name
               FROM transit_days t
               LEFT JOIN agents a ON a.id = t.driver_agent_id
               ORDER BY t.day DESC
               LIMIT ?""",
            (int(days),),
        ).fetchall()
    except sqlite3.OperationalError:
        return {
            "today": None,
            "total_rides": 0,
            "total_storm_rides": 0,
            "total_revenue_cents": 0,
            "avg_on_time_pct": 100.0,
            "history": [],
        }

    history = [dict(r) for r in rows]
    return {
        "today": history[0] if history else None,
        "total_rides": int(agg["total_rides"]),
        "total_storm_rides": int(agg["total_storm_rides"]),
        "total_revenue_cents": int(agg["total_revenue_cents"]),
        "avg_on_time_pct": round(float(agg["avg_on_time_pct"]), 1),
        "history": history,
    }
