"""Greenhill Skywatch & Town Almanac (v0.67).

Every 18-28 days, Miniville schedules an astronomical or almanac skywatch
event (meteor shower, supermoon, planetary conjunction, lunar eclipse, comet
sighting, or rare aurora). Clear/breezy skies yield a ``crystal_clear``
outdoor turnout with morale relief and a standing boost for the curious
discoverer; overcast/rain/storm/snow/fog shifts the gathering indoors to a
``clouded_out`` lantern talk. Optional 100-cent star-party donations transfer
strictly ``agent_state.money_cents -> town_account`` (100% conserved).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import economy, weather
from .events import MAJOR, NOTABLE, emit
from .rng import rng_for
from .seasons import date_of, season_of
from .timekeeper import TICKS_PER_DAY

MIN_CADENCE_DAYS = 18
MAX_CADENCE_DAYS = 28
P_SCHEDULE_IN_WINDOW = 0.25
DONATION_CENTS = 100
DONATION_MIN_WALLET_CENTS = 5_000

CLEAR_CONDITIONS = frozenset({"clear", "breezy"})
CURIOUS_KEYWORDS = frozenset({
    "astronomy", "stargazing", "science", "curious", "inquisitive",
    "photography", "reading", "history", "physics", "nature",
})

SKYWATCH_CATALOG: tuple[tuple[str, str, int], ...] = (
    ("meteor_shower", "Perseid & Ridge Meteor Shower", NOTABLE),
    ("harvest_supermoon", "Greenhill Harvest Supermoon", NOTABLE),
    ("solstice_conjunction", "Solstice Planetary Conjunction", NOTABLE),
    ("lunar_eclipse", "Copper Lunar Eclipse", MAJOR),
    ("comet_sighting", "Lakeshore Comet Sighting", MAJOR),
    ("rare_aurora", "Northern Ribbon Aurora", MAJOR),
)


def ensure_observatory_schema(conn: sqlite3.Connection) -> None:
    """Create the `skywatch_events` table and index if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skywatch_events (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            day                 INTEGER NOT NULL,
            year                INTEGER NOT NULL,
            season              TEXT NOT NULL,
            kind                TEXT NOT NULL,
            title               TEXT NOT NULL,
            visibility          TEXT NOT NULL,
            weather_condition   TEXT NOT NULL,
            attendees_count     INTEGER NOT NULL DEFAULT 0,
            discoverer_agent_id INTEGER REFERENCES agents(id),
            host_place_id       INTEGER REFERENCES places(id),
            donation_cents      INTEGER NOT NULL DEFAULT 0,
            summary             TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skywatch_events_day ON skywatch_events(day DESC)"
    )


def _pick_host_place(conn: sqlite3.Connection, is_clear: bool) -> tuple[int | None, str]:
    names = (
        ("Greenhill Conservatory", "Lush Meadow Park", "Greenhill Gym")
        if is_clear
        else ("Miniville Public Library", "Miniville Community Center", "Town Hall")
    )
    for nm in names:
        row = conn.execute(
            "SELECT id, name FROM places WHERE name = ? LIMIT 1", (nm,)
        ).fetchone()
        if row:
            return int(row["id"]), str(row["name"])
    row = conn.execute(
        "SELECT id, name FROM places WHERE kind IN ('public', 'civic') ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if row:
        return int(row["id"]), str(row["name"])
    return None, "Greenhill Ridge"


def _pick_discoverer(conn: sqlite3.Connection, r) -> tuple[int | None, str]:
    adults = conn.execute(
        """SELECT id, name, hobbies_json, skills_json, traits_json
           FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY id ASC"""
    ).fetchall()
    if not adults:
        return None, "Town Almanac"
    curious: list[tuple[int, str]] = []
    for a in adults:
        tokens: set[str] = set()
        for raw in (a["hobbies_json"], a["skills_json"], a["traits_json"]):
            try:
                items = json.loads(raw or "[]")
            except (TypeError, ValueError):
                items = []
            if isinstance(items, dict):
                items = list(items.keys()) + list(items.values())
            for it in items:
                s = str(it).lower()
                tokens.add(s)
                tokens.update(w.strip(",.") for w in s.split())
        if tokens & CURIOUS_KEYWORDS:
            curious.append((int(a["id"]), str(a["name"])))
    pool = curious if curious else [(int(a["id"]), str(a["name"])) for a in adults]
    aid, name = r.choice(pool)
    return aid, name


def daily_observatory(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Schedule and resolve a Greenhill Skywatch or indoor almanac talk when due."""
    ensure_observatory_schema(conn)
    day_i = int(day)

    existing = conn.execute(
        "SELECT * FROM skywatch_events WHERE day = ? LIMIT 1", (day_i,)
    ).fetchone()
    if existing:
        out = dict(existing)
        out["scheduled"] = False
        out["already_ran"] = True
        return out

    if day_i < MIN_CADENCE_DAYS:
        return {"day": day_i, "scheduled": False, "reason": "early"}

    last_row = conn.execute("SELECT MAX(day) AS last_day FROM skywatch_events").fetchone()
    last_day = int(last_row["last_day"]) if last_row and last_row["last_day"] is not None else 0
    gap = day_i - last_day
    if gap < MIN_CADENCE_DAYS:
        return {"day": day_i, "scheduled": False, "reason": "cadence"}

    r = rng_for(seed, "observatory", day_i)
    if gap < MAX_CADENCE_DAYS and r.random() >= P_SCHEDULE_IN_WINDOW:
        return {"day": day_i, "scheduled": False, "reason": "window_skip"}

    kind, title, base_imp = r.choice(SKYWATCH_CATALOG)
    w = weather.today(conn, day_i)
    cond = str(w.get("condition", "clear"))
    is_clear = cond in CLEAR_CONDITIONS
    visibility = "crystal_clear" if is_clear else "clouded_out"

    host_place_id, host_place_name = _pick_host_place(conn, is_clear)
    discoverer_id, discoverer_name = _pick_discoverer(conn, r)

    residents = conn.execute(
        """SELECT a.id, a.is_child, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1
           ORDER BY a.id ASC"""
    ).fetchall()

    if residents:
        target_n = (
            min(24, max(2, len(residents) // 3))
            if is_clear
            else min(12, max(1, len(residents) // 6))
        )
        attendees = (
            list(residents)
            if len(residents) <= target_n
            else r.sample(list(residents), target_n)
        )
    else:
        attendees = []

    fun_delta = 8 if is_clear else 4
    stress_delta = 6 if is_clear else 3
    donation_cents = 0
    donors = 0

    for att in attendees:
        aid = int(att["id"])
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100, fun + ?),
                   stress = MAX(0, stress - ?)
               WHERE agent_id = ?""",
            (fun_delta, stress_delta, aid),
        )
        if (
            not att["is_child"]
            and donors < 8
            and int(att["money_cents"]) >= DONATION_MIN_WALLET_CENTS
            and r.random() < 0.50
        ):
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (DONATION_CENTS, aid),
            )
            economy.town_credit(conn, DONATION_CENTS)
            donation_cents += DONATION_CENTS
            donors += 1

    if is_clear and discoverer_id is not None:
        standing_boost = 2 if base_imp >= MAJOR else 1
        conn.execute(
            "UPDATE agents SET standing = COALESCE(standing, 0) + ? WHERE id = ?",
            (standing_boost, discoverer_id),
        )

    year, _, _ = date_of(day_i)
    season = season_of(day_i)
    attendees_count = len(attendees)
    if is_clear:
        summary = (
            f"Crystal-clear skies for the {title} at {host_place_name}: "
            f"{attendees_count} residents gathered with {discoverer_name}."
        )
    else:
        summary = (
            f"Clouded out by {cond}: {attendees_count} residents moved indoors to "
            f"{host_place_name} for a lantern almanac talk led by {discoverer_name}."
        )

    cur = conn.execute(
        """INSERT INTO skywatch_events(
               day, year, season, kind, title, visibility, weather_condition,
               attendees_count, discoverer_agent_id, host_place_id,
               donation_cents, summary)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day_i, year, season, kind, title, visibility, cond,
            attendees_count, discoverer_id, host_place_id,
            donation_cents, summary,
        ),
    )
    ev_id = int(cur.lastrowid)

    emit(
        conn,
        day_i * TICKS_PER_DAY,
        "skywatch_event",
        place_id=host_place_id,
        a=discoverer_id,
        importance=base_imp if is_clear else NOTABLE,
        skywatch_id=ev_id,
        skywatch_kind=kind,
        visibility=visibility,
        weather_condition=cond,
        attendees_count=attendees_count,
        donation_cents=donation_cents,
        text=summary,
    )

    return {
        "id": ev_id,
        "day": day_i,
        "year": year,
        "season": season,
        "kind": kind,
        "title": title,
        "visibility": visibility,
        "weather_condition": cond,
        "attendees_count": attendees_count,
        "discoverer_agent_id": discoverer_id,
        "host_place_id": host_place_id,
        "donation_cents": donation_cents,
        "summary": summary,
        "scheduled": True,
    }


def list_skywatch(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent skywatch events enriched with discoverer and venue names."""
    try:
        rows = conn.execute(
            """SELECT s.*,
                      a.name AS discoverer_name,
                      p.name AS place_name
               FROM skywatch_events s
               LEFT JOIN agents a ON a.id = s.discoverer_agent_id
               LEFT JOIN places p ON p.id = s.host_place_id
               ORDER BY s.day DESC, s.id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def observatory_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate observatory metrics and recent skywatch events."""
    try:
        agg = conn.execute(
            """SELECT COUNT(*) AS total_events,
                      SUM(CASE WHEN visibility = 'crystal_clear' THEN 1 ELSE 0 END) AS clear_nights,
                      SUM(CASE WHEN visibility = 'clouded_out' THEN 1 ELSE 0 END) AS clouded_nights,
                      COALESCE(SUM(attendees_count), 0) AS total_attendees,
                      COALESCE(SUM(donation_cents), 0) AS total_donations_cents
               FROM skywatch_events"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "total_events": 0,
            "clear_nights": 0,
            "clouded_nights": 0,
            "total_attendees": 0,
            "total_donations_cents": 0,
            "latest": None,
            "events": [],
        }

    recent = list_skywatch(conn, limit=20)
    return {
        "total_events": int(agg["total_events"] or 0),
        "clear_nights": int(agg["clear_nights"] or 0),
        "clouded_nights": int(agg["clouded_nights"] or 0),
        "total_attendees": int(agg["total_attendees"] or 0),
        "total_donations_cents": int(agg["total_donations_cents"] or 0),
        "latest": recent[0] if recent else None,
        "events": recent,
    }
