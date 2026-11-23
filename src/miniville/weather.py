"""Town weather & micro-climate simulation (v0.50).

Generates deterministic seasonal weather (`clear`, `breezy`, `overcast`,
`fog`, `rain`, `storm`, `heatwave`, `snow`, `blizzard`) via an isolated Markov
chain keyed by `(seed, "weather", day)`. Severe weather (`severity >= 2`)
emits a notable town alert and shifts outdoor leisure plans indoors.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_meta
from .events import NOTABLE, emit
from .rng import rng_for
from .seasons import season_of
from .timekeeper import day_of

CONDITIONS: dict[str, dict[str, Any]] = {
    "clear":    {"icon": "☀️",  "label": "Clear Skies",     "severity": 0, "outdoor_penalty": 0.00},
    "breezy":   {"icon": "🌤️", "label": "Crisp & Breezy",  "severity": 0, "outdoor_penalty": 0.05},
    "overcast": {"icon": "☁️",  "label": "Overcast",        "severity": 0, "outdoor_penalty": 0.15},
    "fog":      {"icon": "🌫️", "label": "Lakeshore Fog",   "severity": 1, "outdoor_penalty": 0.30},
    "rain":     {"icon": "🌧️", "label": "Steady Rain",     "severity": 1, "outdoor_penalty": 0.65},
    "storm":    {"icon": "⛈️", "label": "Thunderstorm",    "severity": 2, "outdoor_penalty": 0.90},
    "heatwave": {"icon": "🌡️", "label": "Heatwave",        "severity": 2, "outdoor_penalty": 0.70},
    "snow":     {"icon": "❄️",  "label": "Snowfall",        "severity": 1, "outdoor_penalty": 0.60},
    "blizzard": {"icon": "🌨️", "label": "Winter Blizzard", "severity": 2, "outdoor_penalty": 0.92},
}

_SEASON_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "winter": [("clear", 0.25), ("overcast", 0.25), ("snow", 0.28), ("fog", 0.12), ("blizzard", 0.10)],
    "spring": [("clear", 0.34), ("breezy", 0.24), ("overcast", 0.16), ("rain", 0.18), ("storm", 0.05), ("fog", 0.03)],
    "summer": [("clear", 0.44), ("breezy", 0.22), ("rain", 0.12), ("storm", 0.12), ("heatwave", 0.10)],
    "autumn": [("clear", 0.30), ("breezy", 0.26), ("overcast", 0.18), ("fog", 0.14), ("rain", 0.10), ("storm", 0.02)],
}

_TEMP_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "winter": {"clear": (22, 36), "overcast": (24, 35), "fog": (26, 36), "snow": (18, 31), "blizzard": (6, 20)},
    "spring": {"clear": (54, 72), "breezy": (50, 68), "overcast": (48, 62), "fog": (44, 56), "rain": (46, 60), "storm": (52, 66)},
    "summer": {"clear": (74, 86), "breezy": (72, 82), "rain": (66, 76), "storm": (68, 80), "heatwave": (93, 103)},
    "autumn": {"clear": (48, 66), "breezy": (44, 60), "overcast": (42, 56), "fog": (38, 52), "rain": (40, 54), "storm": (45, 58)},
}

_SEASON_FALLBACK_TEMP = {"winter": 30, "spring": 62, "summer": 78, "autumn": 55}


def _weighted_pick(items: list[tuple[str, float]], prev: str | None, r) -> str:
    adjusted: list[tuple[str, float]] = []
    for cond, w in items:
        boost = 1.6 if cond == prev else 1.0
        adjusted.append((cond, w * boost))
    total = sum(w for _, w in adjusted)
    roll = r.random() * total
    acc = 0.0
    for cond, w in adjusted:
        acc += w
        if roll <= acc:
            return cond
    return adjusted[-1][0]


def generate_day(day: int, seed: str, prev_condition: str | None = None) -> dict[str, Any]:
    """Return deterministic weather dict for calendar `day`."""
    season = season_of(day)
    r = rng_for(seed, "weather", day)
    cond = _weighted_pick(_SEASON_WEIGHTS[season], prev_condition, r)
    lo, hi = _TEMP_RANGES[season].get(cond, (50, 70))
    temp_f = r.randint(lo, hi)
    meta = CONDITIONS[cond]
    return {
        "day": int(day),
        "condition": cond,
        "temp_f": int(temp_f),
        "severity": int(meta["severity"]),
    }


def _enrich(row: dict[str, Any]) -> dict[str, Any]:
    cond = row["condition"]
    meta = CONDITIONS.get(cond, CONDITIONS["clear"])
    return {
        "day": int(row["day"]),
        "condition": cond,
        "temp_f": int(row["temp_f"]),
        "severity": int(row.get("severity", meta["severity"])),
        "icon": meta["icon"],
        "label": meta["label"],
        "outdoor_penalty": float(meta["outdoor_penalty"]),
    }


def today(conn: sqlite3.Connection, day: int | None = None) -> dict[str, Any]:
    """Return stored weather for `day` (or current tick day), with safe fallback."""
    if day is None:
        tick = int(get_meta(conn, "tick", "0") or 0)
        day = day_of(tick)
    try:
        row = conn.execute(
            "SELECT day, condition, temp_f, severity FROM weather_days WHERE day=?",
            (int(day),),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        return _enrich(dict(row))
    season = season_of(int(day))
    return _enrich({
        "day": int(day),
        "condition": "clear",
        "temp_f": _SEASON_FALLBACK_TEMP.get(season, 64),
        "severity": 0,
    })


def recent(conn: sqlite3.Connection, limit: int = 14) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT day, condition, temp_f, severity FROM weather_days ORDER BY day DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_enrich(dict(r)) for r in rows]


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "today": today(conn),
        "recent": recent(conn, 14),
    }


def due(conn: sqlite3.Connection, tick: int, seed: str = "miniville") -> dict[str, Any]:
    """Generate today's weather row, emit severe alerts, and nudge town morale."""
    day = day_of(tick)
    existing = conn.execute(
        "SELECT day, condition, temp_f, severity FROM weather_days WHERE day=?",
        (day,),
    ).fetchone()
    if existing:
        return _enrich(dict(existing))

    prev_row = conn.execute(
        "SELECT condition FROM weather_days WHERE day < ? ORDER BY day DESC LIMIT 1",
        (day,),
    ).fetchone()
    prev_cond = prev_row["condition"] if prev_row else None
    w = generate_day(day, seed, prev_cond)
    conn.execute(
        "INSERT OR IGNORE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
        (w["day"], w["condition"], w["temp_f"], w["severity"]),
    )
    enriched = _enrich(w)

    if w["severity"] >= 2:
        desc_map = {
            "storm": f"a summer thunderstorm rolled into Miniville ({w['temp_f']}°F)",
            "heatwave": f"a sweltering heatwave settled over town ({w['temp_f']}°F)",
            "blizzard": f"a fierce winter blizzard blanketed the streets ({w['temp_f']}°F)",
        }
        txt = desc_map.get(w["condition"], f"severe weather struck Miniville ({w['label']}, {w['temp_f']}°F)")
        emit(
            conn, tick, "town_event", importance=NOTABLE,
            text=txt, tag="weather_alert",
            condition=w["condition"], temp_f=w["temp_f"], severity=w["severity"],
        )
        conn.execute(
            """UPDATE agent_state
               SET stress = MIN(100, stress + 2),
                   fun = MAX(0, fun - 2)
               WHERE agent_id IN (SELECT id FROM agents WHERE alive = 1)"""
        )
    elif w["condition"] == "clear" and season_of(day) in ("spring", "summer", "autumn"):
        conn.execute(
            """UPDATE agent_state
               SET stress = MAX(0, stress - 1),
                   fun = MIN(100, fun + 1)
               WHERE agent_id IN (SELECT id FROM agents WHERE alive = 1)"""
        )
    return enriched
