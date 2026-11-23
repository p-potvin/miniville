"""Town Market Prices, Supply Waves & Cost-of-Living Index (CPI) (v0.56).

Computes deterministic daily sector price multipliers (`food`, `dining`,
`goods`, `leisure`) clamped to [0.85, 1.20] and a composite CPI index
(baseline 100.0) driven by weather, open business supply, seasonal cycles,
and mild deterministic drift. Notable CPI swings emit rate-limited
`market_bulletin` town events.
"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .rng import rng_for
from .seasons import season_of
from .timekeeper import TICKS_PER_DAY
from .weather import today as weather_today

MIN_MULT = 0.85
MAX_MULT = 1.20
BASELINE_CPI = 100.0

CPI_HIGH_THRESHOLD = 108.0
CPI_LOW_THRESHOLD = 94.0
BULLETIN_COOLDOWN_DAYS = 7

SECTORS = ("food", "dining", "goods", "leisure")

CPI_WEIGHTS: dict[str, float] = {
    "food": 0.35,
    "dining": 0.25,
    "goods": 0.25,
    "leisure": 0.15,
}

_SEASON_DELTAS: dict[str, dict[str, float]] = {
    "spring": {"food": 0.01, "dining": 0.00, "goods": 0.01, "leisure": 0.02},
    "summer": {"food": -0.02, "dining": 0.01, "goods": 0.00, "leisure": 0.04},
    "autumn": {"food": -0.05, "dining": -0.03, "goods": -0.01, "leisure": 0.00},
    "winter": {"food": 0.05, "dining": 0.04, "goods": 0.03, "leisure": -0.02},
}

_WEATHER_DELTAS: dict[str, dict[str, float]] = {
    "clear": {"food": -0.01, "dining": 0.00, "goods": -0.01, "leisure": 0.02},
    "breezy": {"food": -0.01, "dining": 0.00, "goods": 0.00, "leisure": 0.01},
    "overcast": {"food": 0.00, "dining": 0.00, "goods": 0.00, "leisure": 0.00},
    "fog": {"food": 0.01, "dining": 0.01, "goods": 0.02, "leisure": -0.01},
    "rain": {"food": 0.02, "dining": 0.01, "goods": 0.02, "leisure": -0.02},
    "snow": {"food": 0.03, "dining": 0.02, "goods": 0.03, "leisure": -0.03},
    "storm": {"food": 0.06, "dining": 0.04, "goods": 0.05, "leisure": -0.05},
    "blizzard": {"food": 0.07, "dining": 0.05, "goods": 0.06, "leisure": -0.05},
    "heatwave": {"food": 0.04, "dining": 0.03, "goods": 0.02, "leisure": 0.03},
}

_LEISURE_TAGS = {"coffee", "drink", "arts", "nightlife", "fitness"}


def ensure_market_schema(conn: sqlite3.Connection) -> None:
    """Create the `market_days` table if it does not yet exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_days (
            day INTEGER PRIMARY KEY,
            cpi_index REAL,
            food_mult REAL,
            dining_mult REAL,
            goods_mult REAL,
            leisure_mult REAL,
            driver_note TEXT
        )
        """
    )


def _clamp(value: float, lo: float = MIN_MULT, hi: float = MAX_MULT) -> float:
    return max(lo, min(hi, value))


def compute_cpi(
    food_mult: float,
    dining_mult: float,
    goods_mult: float,
    leisure_mult: float,
) -> float:
    """Weighted Cost-of-Living Index (baseline 100.0)."""
    weighted = (
        CPI_WEIGHTS["food"] * food_mult
        + CPI_WEIGHTS["dining"] * dining_mult
        + CPI_WEIGHTS["goods"] * goods_mult
        + CPI_WEIGHTS["leisure"] * leisure_mult
    )
    return round(BASELINE_CPI * weighted, 2)


def _supply_deltas(conn: sqlite3.Connection) -> tuple[dict[str, float], list[str]]:
    """Compute price pressure from closed vs open commercial venues per sector."""
    deltas = {s: 0.0 for s in SECTORS}
    notes: list[str] = []
    try:
        rows = conn.execute(
            """SELECT p.tags, COALESCE(b.status, 'open') AS status
               FROM places p
               JOIN businesses b ON b.place_id = p.id
               WHERE p.kind != 'home'"""
        ).fetchall()
    except sqlite3.OperationalError:
        return deltas, notes

    if not rows:
        return deltas, notes

    totals = {s: 0 for s in SECTORS}
    opens = {s: 0 for s in SECTORS}
    for r in rows:
        tags = set(json.loads(r["tags"] or "[]"))
        is_open = r["status"] == "open"
        if "food" in tags:
            totals["food"] += 1
            if is_open:
                opens["food"] += 1
            if "retail" not in tags:
                totals["dining"] += 1
                if is_open:
                    opens["dining"] += 1
        if "retail" in tags or "trades" in tags:
            totals["goods"] += 1
            if is_open:
                opens["goods"] += 1
        if tags & _LEISURE_TAGS:
            totals["leisure"] += 1
            if is_open:
                opens["leisure"] += 1

    tight_sectors: list[str] = []
    for s in SECTORS:
        t = totals[s]
        if t <= 0:
            continue
        closed_ratio = (t - opens[s]) / t
        if closed_ratio > 0:
            deltas[s] = round(0.14 * closed_ratio, 4)
            if closed_ratio >= 0.25:
                tight_sectors.append(s)
        elif t >= 2:
            deltas[s] = -0.01

    if tight_sectors:
        notes.append(f"tight {'/'.join(tight_sectors)} supply")
    return deltas, notes


def _build_driver_note(
    season: str,
    condition: str,
    severity: int,
    supply_notes: list[str],
) -> str:
    parts: list[str] = []
    if season == "autumn":
        parts.append("autumn harvest abundance")
    elif season == "winter":
        parts.append("winter shipping & heating costs")
    elif season == "summer":
        parts.append("summer demand")
    else:
        parts.append("spring market turnover")

    if severity >= 2:
        parts.append(f"{condition} disruption")
    elif condition in ("rain", "snow", "fog"):
        parts.append(f"{condition} transit friction")

    parts.extend(supply_notes)
    return "; ".join(parts)


def _bulletin_recently_emitted(conn: sqlite3.Connection, day: int) -> bool:
    cutoff = max(0, int(day) - (BULLETIN_COOLDOWN_DAYS - 1))
    try:
        row = conn.execute(
            """SELECT 1 FROM events
               WHERE kind = 'town_event'
                 AND day >= ?
                 AND day <= ?
                 AND data LIKE '%"market_bulletin"%'
               LIMIT 1""",
            (cutoff, int(day)),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def daily_market(
    conn: sqlite3.Connection,
    day: int,
    seed: str = "miniville",
) -> dict[str, Any]:
    """Compute and store daily sector multipliers and CPI for `day` (idempotent)."""
    ensure_market_schema(conn)
    day_i = int(day)
    existing = get_market_day(conn, day_i)
    if existing is not None:
        return existing

    season = season_of(day_i)
    w = weather_today(conn, day_i)
    cond = w["condition"]
    severity = int(w.get("severity", 0))

    s_deltas = _SEASON_DELTAS.get(season, _SEASON_DELTAS["spring"])
    w_deltas = _WEATHER_DELTAS.get(cond, _WEATHER_DELTAS["overcast"])
    sup_deltas, sup_notes = _supply_deltas(conn)

    r = rng_for(seed, "market", day_i)
    mults: dict[str, float] = {}
    for idx, sector in enumerate(SECTORS):
        wave = math.sin((day_i / 14.0) + idx * 1.1) * 0.015
        jitter = r.uniform(-0.015, 0.015)
        raw = 1.0 + s_deltas[sector] + w_deltas[sector] + sup_deltas[sector] + wave + jitter
        mults[sector] = round(_clamp(raw), 4)

    cpi = compute_cpi(
        mults["food"],
        mults["dining"],
        mults["goods"],
        mults["leisure"],
    )
    note = _build_driver_note(season, cond, severity, sup_notes)

    conn.execute(
        """INSERT OR IGNORE INTO market_days
           (day, cpi_index, food_mult, dining_mult, goods_mult, leisure_mult, driver_note)
           VALUES (?,?,?,?,?,?,?)""",
        (
            day_i,
            cpi,
            mults["food"],
            mults["dining"],
            mults["goods"],
            mults["leisure"],
            note,
        ),
    )

    if (cpi >= CPI_HIGH_THRESHOLD or cpi <= CPI_LOW_THRESHOLD) and not _bulletin_recently_emitted(conn, day_i):
        direction = "surged" if cpi >= CPI_HIGH_THRESHOLD else "dipped"
        txt = f"Miniville cost-of-living {direction} to CPI {cpi:.1f} ({note})"
        emit(
            conn,
            day_i * TICKS_PER_DAY,
            "town_event",
            importance=NOTABLE,
            text=txt,
            tag="market_bulletin",
            day=day_i,
            cpi_index=cpi,
            driver_note=note,
        )

    return {
        "day": day_i,
        "cpi_index": cpi,
        "food_mult": mults["food"],
        "dining_mult": mults["dining"],
        "goods_mult": mults["goods"],
        "leisure_mult": mults["leisure"],
        "driver_note": note,
    }


def get_market_day(conn: sqlite3.Connection, day: int) -> dict[str, Any] | None:
    """Return the stored market row for `day`, or None if absent."""
    try:
        row = conn.execute(
            """SELECT day, cpi_index, food_mult, dining_mult, goods_mult, leisure_mult, driver_note
               FROM market_days WHERE day = ?""",
            (int(day),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {
        "day": int(row["day"]),
        "cpi_index": float(row["cpi_index"]),
        "food_mult": float(row["food_mult"]),
        "dining_mult": float(row["dining_mult"]),
        "goods_mult": float(row["goods_mult"]),
        "leisure_mult": float(row["leisure_mult"]),
        "driver_note": row["driver_note"] or "",
    }


def sector_multiplier(conn: sqlite3.Connection, day: int, sector: str) -> float:
    """Return the price multiplier for `sector` on `day` (defaults to 1.0)."""
    if sector not in SECTORS:
        return 1.0
    row = get_market_day(conn, day)
    if row is None:
        return 1.0
    return float(row[f"{sector}_mult"])


def market_history(conn: sqlite3.Connection, days: int = 14) -> list[dict[str, Any]]:
    """Return up to `days` most recent market_days rows, newest first."""
    try:
        rows = conn.execute(
            """SELECT day, cpi_index, food_mult, dining_mult, goods_mult, leisure_mult, driver_note
               FROM market_days ORDER BY day DESC LIMIT ?""",
            (int(days),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "day": int(r["day"]),
            "cpi_index": float(r["cpi_index"]),
            "food_mult": float(r["food_mult"]),
            "dining_mult": float(r["dining_mult"]),
            "goods_mult": float(r["goods_mult"]),
            "leisure_mult": float(r["leisure_mult"]),
            "driver_note": r["driver_note"] or "",
        }
        for r in rows
    ]
