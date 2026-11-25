"""Town Ensembles, Choir & Street Buskers (v0.69).

Every 8-14 days (preferring weekends or active festivals), a local Miniville
ensemble performs at a public venue or landmark, sheltering indoors during
rain, storms, or extreme weather. Listeners with comfortable wallets toss
conserved tips (`50`-`150` cents, `listener agent_state.money_cents -> leader
agent_state.money_cents`) while gaining fun/social/stress relief.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .rng import rng_for
from .seasons import date_of, season_of
from .timekeeper import TICKS_PER_DAY, is_weekend
from .weather import today as weather_today

MIN_CADENCE_DAYS = 8
MAX_CADENCE_DAYS = 14
COMFORTABLE_WALLET_CENTS = 5_000
MAX_AUDIENCE = 15

ENSEMBLES: tuple[tuple[str, str], ...] = (
    ("Lakeshore Brass", "brass"),
    ("Old Mill Folk Trio", "folk"),
    ("Greenhill Chamber Choir", "choral"),
    ("Flats Blues Quartet", "blues"),
)

_OUTDOOR_VENUES = ("Lush Meadow Park", "Lakeshore Marina")
_INDOOR_VENUES = (
    "Miniville Community Center",
    "The Bijou Theater",
    "First Congregational Church",
    "Miniville Public Library",
)


def ensure_music_schema(conn: sqlite3.Connection) -> None:
    """Create the `music_performances` table if it does not yet exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS music_performances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day INTEGER,
            year INTEGER,
            ensemble_name TEXT,
            genre TEXT,
            leader_agent_id INTEGER,
            place_id INTEGER,
            occasion TEXT,
            audience_count INTEGER,
            tips_cents INTEGER,
            weather_mode TEXT,
            summary TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_music_day ON music_performances(day)")


def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "day": int(r["day"]),
        "year": int(r["year"]),
        "ensemble_name": r["ensemble_name"],
        "genre": r["genre"],
        "leader_agent_id": int(r["leader_agent_id"]) if r["leader_agent_id"] is not None else None,
        "leader_name": r["leader_name"] if "leader_name" in r.keys() else None,
        "place_id": int(r["place_id"]) if r["place_id"] is not None else None,
        "place_name": r["place_name"] if "place_name" in r.keys() else None,
        "occasion": r["occasion"],
        "audience_count": int(r["audience_count"] or 0),
        "tips_cents": int(r["tips_cents"] or 0),
        "weather_mode": r["weather_mode"],
        "summary": r["summary"] or "",
    }


def _get_performance_for_day(conn: sqlite3.Connection, day: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT m.*, a.name AS leader_name, p.name AS place_name
           FROM music_performances m
           LEFT JOIN agents a ON a.id = m.leader_agent_id
           LEFT JOIN places p ON p.id = m.place_id
           WHERE m.day = ?
           LIMIT 1""",
        (int(day),),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def _active_festival_name(conn: sqlite3.Connection, day: int) -> str | None:
    try:
        row = conn.execute(
            """SELECT name FROM festivals
               WHERE start_day <= ? AND end_day >= ?
               ORDER BY id LIMIT 1""",
            (day, day),
        ).fetchone()
        return str(row["name"]) if row else None
    except sqlite3.OperationalError:
        return None


def _is_music_due(conn: sqlite3.Connection, day: int, seed: str, has_festival: bool) -> bool:
    row = conn.execute(
        "SELECT MAX(day) AS last_day FROM music_performances WHERE day < ?",
        (day,),
    ).fetchone()
    last_day = row["last_day"] if row and row["last_day"] is not None else None
    wknd = is_weekend(day * TICKS_PER_DAY)

    if last_day is None:
        return wknd or has_festival or day >= 5

    gap = day - int(last_day)
    if gap < MIN_CADENCE_DAYS:
        return False
    if gap >= MAX_CADENCE_DAYS:
        return True
    if has_festival:
        return True
    r = rng_for(seed, "music_due", day)
    if wknd:
        return r.random() < 0.70
    return r.random() < 0.20


def _pick_venue(conn: sqlite3.Connection, is_outdoor: bool, r) -> tuple[int | None, str]:
    preferred = _OUTDOOR_VENUES if is_outdoor else _INDOOR_VENUES
    placeholders = ",".join("?" for _ in preferred)
    rows = conn.execute(
        f"SELECT id, name FROM places WHERE name IN ({placeholders}) ORDER BY id",
        preferred,
    ).fetchall()
    if rows:
        chosen = rows[r.randrange(len(rows))]
        return int(chosen["id"]), str(chosen["name"])
    fallback = conn.execute(
        "SELECT id, name FROM places WHERE kind != 'home' ORDER BY id LIMIT 1"
    ).fetchone()
    if fallback:
        return int(fallback["id"]), str(fallback["name"])
    return None, "Town Square"


def daily_music(
    conn: sqlite3.Connection,
    day: int,
    seed: str = "miniville",
) -> dict[str, Any] | None:
    """Maybe stage a live music performance on `day` (idempotent)."""
    ensure_music_schema(conn)
    day_i = int(day)
    existing = _get_performance_for_day(conn, day_i)
    if existing is not None:
        return existing

    fest_name = _active_festival_name(conn, day_i)
    if not _is_music_due(conn, day_i, seed, fest_name is not None):
        return None

    adults = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY a.id"""
    ).fetchall()
    if not adults:
        return None

    r = rng_for(seed, "music", day_i)
    pool = list(adults)
    r.shuffle(pool)
    leader = pool[0]
    leader_id = int(leader["id"])
    leader_name = str(leader["name"])
    audience = pool[1 : 1 + MAX_AUDIENCE]
    audience_count = len(audience)

    ensemble_name, genre = ENSEMBLES[r.randrange(len(ENSEMBLES))]

    season = season_of(day_i)
    w = weather_today(conn, day_i)
    is_outdoor = (
        int(w.get("severity", 0)) == 0
        and w.get("condition") in ("clear", "breezy", "overcast")
        and (season != "winter" or int(w.get("temp_f", 30)) >= 45)
    )
    weather_mode = "outdoor_stage" if is_outdoor else "indoor_shelter"
    place_id, place_name = _pick_venue(conn, is_outdoor, r)

    if fest_name:
        occasion = f"{fest_name} Stage"
    elif is_weekend(day_i * TICKS_PER_DAY):
        occasion = "Weekend Matinee"
    else:
        occasion = "Evening Street Busking"

    # Boost audience mood/stress and collect 100% conserved tips (listener -> leader)
    tips_cents = 0
    for listener in audience:
        lid = int(listener["id"])
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100.0, fun + 6.0),
                   social = MIN(100.0, social + 4.0),
                   stress = MAX(0.0, stress - 4.0)
               WHERE agent_id = ?""",
            (lid,),
        )
        if int(listener["money_cents"]) >= COMFORTABLE_WALLET_CENTS:
            tip = r.choice((50, 100, 150))
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (tip, lid),
            )
            tips_cents += tip

    if tips_cents > 0:
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (tips_cents, leader_id),
        )

    year, _, _ = date_of(day_i)
    mode_text = "outdoors" if is_outdoor else "sheltered indoors"
    summary = (
        f"{ensemble_name} ({leader_name}) played a {occasion} set {mode_text} "
        f"at {place_name} for {audience_count} listeners (${tips_cents / 100:.2f} tips)"
    )

    cur = conn.execute(
        """INSERT INTO music_performances
           (day, year, ensemble_name, genre, leader_agent_id, place_id, occasion,
            audience_count, tips_cents, weather_mode, summary)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            day_i,
            year,
            ensemble_name,
            genre,
            leader_id,
            place_id,
            occasion,
            audience_count,
            tips_cents,
            weather_mode,
            summary,
        ),
    )

    emit(
        conn,
        day_i * TICKS_PER_DAY,
        "town_event",
        place_id=place_id,
        a=leader_id,
        importance=NOTABLE,
        tag="live_music",
        day=day_i,
        ensemble_name=ensemble_name,
        genre=genre,
        weather_mode=weather_mode,
        audience_count=audience_count,
        tips_cents=tips_cents,
        text=summary,
    )

    return {
        "id": int(cur.lastrowid),
        "day": day_i,
        "year": year,
        "ensemble_name": ensemble_name,
        "genre": genre,
        "leader_agent_id": leader_id,
        "leader_name": leader_name,
        "place_id": place_id,
        "place_name": place_name,
        "occasion": occasion,
        "audience_count": audience_count,
        "tips_cents": tips_cents,
        "weather_mode": weather_mode,
        "summary": summary,
    }


def list_performances(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent live music performances, newest first."""
    try:
        rows = conn.execute(
            """SELECT m.*, a.name AS leader_name, p.name AS place_name
               FROM music_performances m
               LEFT JOIN agents a ON a.id = m.leader_agent_id
               LEFT JOIN places p ON p.id = m.place_id
               ORDER BY m.day DESC, m.id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_dict(r) for r in rows]


def music_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate music stats and recent performances."""
    try:
        agg = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(audience_count), 0) AS total_aud,
                      COALESCE(SUM(tips_cents), 0) AS total_tips
               FROM music_performances"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "total_performances": 0,
            "total_audience": 0,
            "total_tips_cents": 0,
            "latest": None,
            "performances": [],
        }
    recent = list_performances(conn, limit=20)
    return {
        "total_performances": int(agg["cnt"]),
        "total_audience": int(agg["total_aud"]),
        "total_tips_cents": int(agg["total_tips"]),
        "latest": recent[0] if recent else None,
        "performances": recent,
    }
