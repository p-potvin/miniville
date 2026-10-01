"""Daily plan generation: each agent gets a place+activity for every tick.

Templates are deterministic per (agent, day). Needs may nudge leisure picks at
execution time, but the plan is the backbone.
"""
from __future__ import annotations

import json
import sqlite3

from .seasons import (
    OUTDOOR_APPEAL, OUTDOOR_TAGS, holiday_on, school_in_session, season_of,
)
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY, is_weekend, weekday

WAKE_TICK = 13        # 06:30
SLEEP_TICK = 45       # 22:30
SCHOOL_START, SCHOOL_END = 14, 32

LEISURE_TAGS = ["outdoors", "food", "drink", "nightlife", "sport", "quiet",
                "arts", "community", "coffee"]


def _venue_by_tags(conn: sqlite3.Connection, tags: list[str], kinds=("public", "civic")):
    rows = conn.execute(
        f"SELECT id, tags, open_tick, close_tick FROM places WHERE kind IN ({','.join('?'*len(kinds))})",
        kinds).fetchall()
    out = []
    for row in rows:
        ptags = set(json.loads(row["tags"]))
        if ptags & set(tags):
            out.append(dict(row))
    return out


def _plan_ctx(conn: sqlite3.Connection) -> dict:
    """Per-day cached venue data — one table scan instead of ~100."""
    venues = []
    for row in conn.execute(
            "SELECT id, name, kind, tags, open_tick, close_tick FROM places").fetchall():
        v = dict(row)
        v["tags"] = set(json.loads(v["tags"]))
        venues.append(v)
    school = conn.execute(
        "SELECT id FROM places WHERE name='Miniville School'").fetchone()
    return {
        "venues": venues,
        "ids_by_name": {v["name"]: v["id"] for v in venues},
        "school_id": school["id"] if school else None,
    }


def _by_tags(venues: list[dict], tags: list[str], kinds=("public", "civic")):
    tset = set(tags)
    return [v for v in venues if v["kind"] in kinds and v["tags"] & tset]


def _hobby_tags(hobbies: list[str]) -> list[str]:
    h = " ".join(hobbies).lower()
    tags = []
    for kw, tag in [("run", "sport"), ("gym", "sport"), ("hik", "outdoors"),
                    ("read", "quiet"), ("book", "quiet"), ("cook", "food"),
                    ("bak", "food"), ("beer", "drink"), ("brew", "drink"),
                    ("art", "arts"), ("music", "arts"), ("paint", "arts"),
                    ("garden", "outdoors"), ("fish", "water"), ("boat", "water"),
                    ("volunteer", "community"), ("church", "worship")]:
        if kw in h:
            tags.append(tag)
    return tags or LEISURE_TAGS


def build_plan(conn: sqlite3.Connection, agent: sqlite3.Row, day: int, seed: str,
               ctx: dict | None = None) -> list[tuple[int, int, str]]:
    """Return [(tick, place_id, activity)] for ticks 0..47 of `day`."""
    if ctx is None:
        ctx = _plan_ctx(conn)
    r = rng_for(seed, "plan", agent["id"], day)
    plan: dict[int, tuple[int, str]] = {}
    home = agent["home_place_id"]
    wknd = is_weekend(day * TICKS_PER_DAY)
    hobbies = json.loads(agent["hobbies_json"] or "[]")
    season = season_of(day)
    holiday = holiday_on(day)

    job = conn.execute("SELECT * FROM jobs WHERE agent_id=?", (agent["id"],)).fetchone()
    job_venue = next((v for v in ctx["venues"]
                      if job and v["id"] == job["place_id"]), None)
    works_today = bool(job) and not wknd and not agent["is_child"]
    if holiday and holiday.day_off and not (job_venue and "health" in job_venue["tags"]):
        works_today = False
    attends_holiday = bool(holiday) and not (
        holiday.adults_only and agent["is_child"]
    ) and rng_for(seed, "holiday", agent["id"], day).random() < holiday.p_attend

    leisure_venues = _by_tags(ctx["venues"], _hobby_tags(hobbies))
    if not leisure_venues:
        leisure_venues = _by_tags(ctx["venues"], LEISURE_TAGS)
    food_venues = _by_tags(ctx["venues"], ["food"])
    school_id = ctx["school_id"]

    for t in range(TICKS_PER_DAY):
        place, act = home, "sleep"
        in_work_shift = works_today and job["shift_start"] <= t < job["shift_end"]
        if in_work_shift:
            # lunch break mid-shift so workers don't starve
            mid = (job["shift_start"] + job["shift_end"]) // 2
            plan[t] = (job["place_id"], "break" if t == mid else "work")
            continue

        in_school = (
            agent["is_child"] and agent["age"] >= 5 and not wknd
            and school_in_session(day) and SCHOOL_START <= t < SCHOOL_END
            and school_id
        )
        if in_school:
            plan[t] = (school_id, "school")
            continue

        in_holiday = (
            holiday and attends_holiday
            and holiday.start_tick <= t < holiday.end_tick
        )
        if in_holiday:
            venue_id = ctx["ids_by_name"].get(holiday.venue) if holiday.venue else None
            plan[t] = (venue_id or home, "celebrate")
            continue

        if t < WAKE_TICK or t >= SLEEP_TICK:
            plan[t] = (place, act)
            continue

        # post-shift dinner for workers whose shift ends at/past dinner time
        if works_today and t == job["shift_end"]:
            plan[t] = (home, "eat")
            continue

        if t in (14, 26, 38):  # meal windows
            if food_venues and r.random() < (0.15 if not wknd else 0.3):
                v = r.choice(food_venues)
                plan[t] = (v["id"], "eat_out")
                continue
            plan[t] = (home, "eat")
            continue

        # leisure: prob of going out depends on weekend & hour
        p_out = 0.55 if wknd else (0.35 if 18 <= t <= 30 else 0.2)
        if t >= 40 and r.random() < 0.3:
            p_out = 0.4  # nightlife window
        if r.random() < p_out and leisure_venues:
            v = r.choice(leisure_venues)
            if v["tags"] & OUTDOOR_TAGS and r.random() >= OUTDOOR_APPEAL[season]:
                plan[t] = (home, "home")
                continue
            if v["open_tick"] <= t <= v["close_tick"]:
                plan[t] = (v["id"], "leisure")
                continue
        plan[t] = (home, "home")

    return [(t, p, a) for t, (p, a) in sorted(plan.items())]


def rebuild_day_plans(conn: sqlite3.Connection, day: int, seed: str) -> int:
    conn.execute("DELETE FROM plans")
    agents = conn.execute("SELECT * FROM agents WHERE alive=1").fetchall()
    ctx = _plan_ctx(conn)
    rows = []
    for a in agents:
        rows.extend(
            (a["id"], t, p, act) for t, p, act in build_plan(conn, a, day, seed, ctx))
    conn.executemany(
        "INSERT INTO plans(agent_id,tick,place_id,activity) VALUES(?,?,?,?)", rows)
    conn.commit()
    return len(rows)
