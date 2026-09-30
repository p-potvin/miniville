"""Daily plan generation: each agent gets a place+activity for every tick.

Templates are deterministic per (agent, day). Needs may nudge leisure picks at
execution time, but the plan is the backbone.
"""
from __future__ import annotations

import json
import sqlite3

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


def build_plan(conn: sqlite3.Connection, agent: sqlite3.Row, day: int, seed: str) -> list[tuple[int, int, str]]:
    """Return [(tick, place_id, activity)] for ticks 0..47 of `day`."""
    r = rng_for(seed, "plan", agent["id"], day)
    plan: dict[int, tuple[int, str]] = {}
    home = agent["home_place_id"]
    wknd = is_weekend(day * TICKS_PER_DAY)
    hobbies = json.loads(agent["hobbies_json"] or "[]")

    job = conn.execute("SELECT * FROM jobs WHERE agent_id=?", (agent["id"],)).fetchone()
    works_today = bool(job) and not wknd and not agent["is_child"]

    leisure_venues = _venue_by_tags(conn, _hobby_tags(hobbies))
    if not leisure_venues:
        leisure_venues = _venue_by_tags(conn, LEISURE_TAGS)

    for t in range(TICKS_PER_DAY):
        place, act = home, "sleep"
        if t < WAKE_TICK or t >= SLEEP_TICK:
            plan[t] = (place, act)
            continue

        if agent["is_child"] and agent["age"] >= 5 and not wknd:
            if SCHOOL_START <= t < SCHOOL_END:
                school = conn.execute(
                    "SELECT id FROM places WHERE name='Miniville School'").fetchone()
                plan[t] = (school["id"], "school")
                continue
        elif works_today and job["shift_start"] <= t < job["shift_end"]:
            # lunch break mid-shift so workers don't starve
            mid = (job["shift_start"] + job["shift_end"]) // 2
            if t == mid:
                plan[t] = (job["place_id"], "break")
            else:
                plan[t] = (job["place_id"], "work")
            continue

        # post-shift dinner for workers whose shift ends at/past dinner time
        if works_today and t == job["shift_end"]:
            plan[t] = (home, "eat")
            continue

        if t in (14, 26, 38):  # meal windows
            if r.random() < (0.15 if not wknd else 0.3):
                v = r.choice(_venue_by_tags(conn, ["food"]))
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
            if v["open_tick"] <= t <= v["close_tick"]:
                plan[t] = (v["id"], "leisure")
                continue
        plan[t] = (home, "home")

    return [(t, p, a) for t, (p, a) in sorted(plan.items())]


def rebuild_day_plans(conn: sqlite3.Connection, day: int, seed: str) -> int:
    conn.execute("DELETE FROM plans")
    agents = conn.execute("SELECT * FROM agents WHERE alive=1").fetchall()
    n = 0
    for a in agents:
        for t, p, act in build_plan(conn, a, day, seed):
            conn.execute(
                "INSERT INTO plans(agent_id,tick,place_id,activity) VALUES(?,?,?,?)",
                (a["id"], t, p, act))
            n += 1
    conn.commit()
    return n
