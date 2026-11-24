"""Town Utilities, Power Grid & Renewable Micro-Grid (v0.65).

Simulates daily town electricity demand vs. supply (`Old Mill Hydro` +
`Greenhill Solar Array` + backup) coupled to population, open venues, and
`weather_days`. Emergency grid repairs (`strained`/`brownout`) and weekly
green-energy surplus rebates (`day % 7 == 0`, `renewable_pct >= 65.0`)
transfer 100% conserved funds strictly `town_account -> agent_state.money_cents`
with deficit-safe purse floors.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .economy import town_balance, town_debit
from .events import NOTABLE, emit
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY
from .weather import today as weather_today
from .world import DISTRICTS

MAINT_PURSE_FLOOR = 300_000
REBATE_PURSE_FLOOR = 700_000
REBATE_POST_FLOOR = 600_000
REBATE_PER_ADULT_CENTS = 150
EVENT_COOLDOWN_DAYS = 7


def ensure_utilities_schema(conn: sqlite3.Connection) -> None:
    """Create the `utility_days` table if it does not yet exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS utility_days (
            day INTEGER PRIMARY KEY,
            demand_mw REAL,
            supply_mw REAL,
            renewable_pct REAL,
            grid_status TEXT,
            outage_district TEXT,
            maintenance_cents INTEGER,
            rebate_cents INTEGER,
            note TEXT
        )
        """
    )


def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "day": int(r["day"]),
        "demand_mw": float(r["demand_mw"]),
        "supply_mw": float(r["supply_mw"]),
        "renewable_pct": float(r["renewable_pct"]),
        "grid_status": r["grid_status"],
        "outage_district": r["outage_district"],
        "maintenance_cents": int(r["maintenance_cents"] or 0),
        "rebate_cents": int(r["rebate_cents"] or 0),
        "note": r["note"] or "",
    }


def _get_utility_day(conn: sqlite3.Connection, day: int) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            "SELECT * FROM utility_days WHERE day = ?",
            (int(day),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return _row_to_dict(row) if row is not None else None


def _recent_event(conn: sqlite3.Connection, day: int, tag: str) -> bool:
    cutoff = max(0, day - (EVENT_COOLDOWN_DAYS - 1))
    try:
        row = conn.execute(
            """SELECT 1 FROM events
               WHERE kind = 'town_event'
                 AND day >= ? AND day <= ?
                 AND data LIKE ?
               LIMIT 1""",
            (cutoff, day, f'%"{tag}"%'),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _trades_workers(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """SELECT a.id
           FROM agents a
           JOIN jobs j ON j.agent_id = a.id
           JOIN places p ON p.id = j.place_id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (p.tags LIKE '%"trades"%' OR a.occupation LIKE '%electric%'
                  OR a.occupation LIKE '%technician%' OR a.occupation LIKE '%mechanic%')
           ORDER BY a.id
           LIMIT 6"""
    ).fetchall()
    if rows:
        return [int(r["id"]) for r in rows]
    fallback = conn.execute(
        """SELECT id FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY id
           LIMIT 4"""
    ).fetchall()
    return [int(r["id"]) for r in fallback]


def daily_utilities(
    conn: sqlite3.Connection,
    day: int,
    seed: str = "miniville",
) -> dict[str, Any]:
    """Compute and store daily grid load, supply, and conserved maintenance/rebates (idempotent)."""
    ensure_utilities_schema(conn)
    day_i = int(day)
    existing = _get_utility_day(conn, day_i)
    if existing is not None:
        return existing

    pop = int(conn.execute("SELECT COUNT(*) FROM agents WHERE alive = 1").fetchone()[0])
    try:
        open_venues = int(
            conn.execute("SELECT COUNT(*) FROM businesses WHERE status = 'open'").fetchone()[0]
        )
    except sqlite3.OperationalError:
        open_venues = int(
            conn.execute("SELECT COUNT(*) FROM places WHERE kind != 'home'").fetchone()[0]
        )

    w = weather_today(conn, day_i)
    cond = str(w.get("condition", "clear"))
    r = rng_for(seed, "utilities", day_i)

    # Demand calculation
    base_demand = 1.2 + pop * 0.012 + open_venues * 0.18
    demand_bump = {
        "heatwave": 0.30,
        "blizzard": 0.28,
        "snow": 0.12,
        "storm": 0.10,
        "rain": 0.04,
        "fog": 0.04,
    }.get(cond, 0.0)
    demand_mw = round(max(1.0, base_demand * (1.0 + demand_bump + r.uniform(-0.03, 0.03))), 2)

    # Supply calculation: Old Mill Hydro + Greenhill Solar Array + backup
    base_cap = max(4.5, 1.35 + pop * 0.0125 + open_venues * 0.19)
    hydro_factor = {
        "rain": 1.12,
        "storm": 1.05,
        "heatwave": 0.88,
        "blizzard": 0.82,
        "snow": 0.92,
    }.get(cond, 1.0)
    solar_factor = {
        "clear": 1.18,
        "breezy": 1.12,
        "heatwave": 1.08,
        "overcast": 0.65,
        "fog": 0.60,
        "rain": 0.45,
        "snow": 0.45,
        "storm": 0.25,
        "blizzard": 0.20,
    }.get(cond, 0.85)
    backup_factor = 0.72 if cond in ("storm", "blizzard") else (0.85 if cond == "heatwave" else 1.0)

    hydro_mw = base_cap * 0.42 * hydro_factor
    solar_mw = base_cap * 0.36 * solar_factor
    backup_mw = base_cap * 0.28 * backup_factor

    supply_mw = round(max(1.0, hydro_mw + solar_mw + backup_mw + r.uniform(-0.10, 0.10)), 2)
    renewable_pct = round(min(100.0, max(0.0, 100.0 * (hydro_mw + solar_mw) / supply_mw)), 1)

    ratio = supply_mw / demand_mw
    if ratio >= 1.12:
        grid_status = "surplus"
    elif ratio >= 0.96:
        grid_status = "nominal"
    elif ratio >= 0.84:
        grid_status = "strained"
    else:
        grid_status = "brownout"

    outage_district = DISTRICTS[r.randrange(len(DISTRICTS))] if grid_status == "brownout" else None

    # 100% conserved emergency repair: town_account -> local trades workers
    maintenance_cents = 0
    if grid_status in ("strained", "brownout"):
        bal = town_balance(conn)
        if bal >= MAINT_PURSE_FLOOR:
            workers = _trades_workers(conn)
            if workers:
                target = 15_000 if grid_status == "brownout" else 8_000
                maintenance_cents = min(target, bal - 250_000)
                if maintenance_cents > 0:
                    town_debit(conn, maintenance_cents)
                    share = maintenance_cents // len(workers)
                    rem = maintenance_cents - share * len(workers)
                    for idx, wid in enumerate(workers):
                        pay = share + (rem if idx == 0 else 0)
                        if pay > 0:
                            conn.execute(
                                "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                                (pay, wid),
                            )

    # 100% conserved weekly green-energy surplus rebate: town_account -> living adults
    rebate_cents = 0
    if day_i % 7 == 0 and renewable_pct >= 65.0:
        bal = town_balance(conn)
        if bal >= REBATE_PURSE_FLOOR:
            adults = conn.execute(
                "SELECT id FROM agents WHERE alive = 1 AND is_child = 0 ORDER BY id"
            ).fetchall()
            if adults:
                max_budget = bal - REBATE_POST_FLOOR
                per_adult = min(REBATE_PER_ADULT_CENTS, max_budget // len(adults))
                if per_adult >= 100:
                    rebate_cents = per_adult * len(adults)
                    town_debit(conn, rebate_cents)
                    for a in adults:
                        conn.execute(
                            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                            (per_adult, int(a["id"])),
                        )

    note = f"{cond} ({demand_mw:.1f}/{supply_mw:.1f} MW, {renewable_pct:.0f}% renewables)"
    if outage_district:
        note += f"; brownout in {outage_district}"

    conn.execute(
        """INSERT OR IGNORE INTO utility_days
           (day, demand_mw, supply_mw, renewable_pct, grid_status, outage_district,
            maintenance_cents, rebate_cents, note)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            day_i,
            demand_mw,
            supply_mw,
            renewable_pct,
            grid_status,
            outage_district,
            maintenance_cents,
            rebate_cents,
            note,
        ),
    )

    tick = day_i * TICKS_PER_DAY
    if grid_status == "brownout" and not _recent_event(conn, day_i, "grid_alert"):
        emit(
            conn,
            tick,
            "town_event",
            importance=NOTABLE,
            tag="grid_alert",
            day=day_i,
            outage_district=outage_district,
            demand_mw=demand_mw,
            supply_mw=supply_mw,
            text=f"Rolling brownout hit {outage_district} ({demand_mw:.1f} MW demand vs {supply_mw:.1f} MW supply)",
        )
    elif rebate_cents > 0 and not _recent_event(conn, day_i, "green_rebate"):
        emit(
            conn,
            tick,
            "town_event",
            importance=NOTABLE,
            tag="green_rebate",
            day=day_i,
            renewable_pct=renewable_pct,
            rebate_cents=rebate_cents,
            text=f"Green-grid surplus ({renewable_pct:.0f}% hydro/solar) paid ${rebate_cents / 100:,.2f} in resident rebates",
        )

    return {
        "day": day_i,
        "demand_mw": demand_mw,
        "supply_mw": supply_mw,
        "renewable_pct": renewable_pct,
        "grid_status": grid_status,
        "outage_district": outage_district,
        "maintenance_cents": maintenance_cents,
        "rebate_cents": rebate_cents,
        "note": note,
    }


def utilities_summary(conn: sqlite3.Connection, days: int = 14) -> dict[str, Any]:
    """Return recent utility_days history and aggregate grid metrics."""
    try:
        rows = conn.execute(
            "SELECT * FROM utility_days ORDER BY day DESC LIMIT ?",
            (int(days),),
        ).fetchall()
        agg = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(AVG(renewable_pct), 0.0) AS avg_ren,
                      COALESCE(SUM(CASE WHEN grid_status = 'brownout' THEN 1 ELSE 0 END), 0) AS brownouts,
                      COALESCE(SUM(maintenance_cents), 0) AS total_maint,
                      COALESCE(SUM(rebate_cents), 0) AS total_rebate
               FROM utility_days"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "today": None,
            "avg_renewable_pct": 0.0,
            "brownout_days": 0,
            "total_maintenance_cents": 0,
            "total_rebate_cents": 0,
            "history": [],
        }
    history = [_row_to_dict(r) for r in rows]
    return {
        "today": history[0] if history else None,
        "avg_renewable_pct": round(float(agg["avg_ren"] or 0.0), 1),
        "brownout_days": int(agg["brownouts"] or 0),
        "total_maintenance_cents": int(agg["total_maint"] or 0),
        "total_rebate_cents": int(agg["total_rebate"] or 0),
        "history": history,
    }
