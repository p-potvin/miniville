"""Daily plan generation: each agent gets a place+activity for every tick.

Templates are deterministic per (agent, day). Needs may nudge leisure picks at
execution time, but the plan is the backbone.
"""
from __future__ import annotations

import json
import sqlite3

from .seasons import (
    OUTDOOR_APPEAL, OUTDOOR_TAGS, holiday_for, holiday_on, school_in_session,
    season_of,
)
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY, is_weekend, weekday

WAKE_TICK = 13        # 06:30
SLEEP_TICK = 45       # 22:30
SCHOOL_START, SCHOOL_END = 14, 32

LEISURE_TAGS = ["outdoors", "food", "drink", "nightlife", "sport", "quiet",
                "arts", "community", "coffee"]

SHOP_TAGS = ["retail", "trades"]
SHOP_KINDS = ("workplace", "public")
DINING_KINDS = ("workplace", "public")
SHOPPING_WINDOWS = (20, 32, 42)   # 10:00, 16:00, 21:00
GATHERING_TICKS = 3               # a club night runs two hours, not thirty minutes
MEAL_TICKS = (14, 26, 38)         # breakfast, lunch, dinner — see build_plan


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
            """SELECT p.id, p.name, p.kind, p.tags, p.open_tick, p.close_tick,
                      COALESCE(b.status, 'open') AS status
               FROM places p LEFT JOIN businesses b ON b.place_id = p.id""").fetchall():
        v = dict(row)
        v["tags"] = set(json.loads(v["tags"]))
        if v["status"] == "closed":
            continue              # a shut business is not a destination
        venues.append(v)
    ids_by_name = {v["name"]: v["id"] for v in venues}
    venues_by_id = {v["id"]: v for v in venues}
    rival_places: dict[int, set[int]] = {}
    for r in conn.execute(
            """SELECT r.a_id, r.b_id, ja.place_id AS pa, jb.place_id AS pb
               FROM relationships r
               LEFT JOIN jobs ja ON ja.agent_id = r.a_id
               LEFT JOIN jobs jb ON jb.agent_id = r.b_id
               WHERE r.affinity <= -20""").fetchall():
        if r["pb"] is not None:
            rival_places.setdefault(r["a_id"], set()).add(r["pb"])
        if r["pa"] is not None:
            rival_places.setdefault(r["b_id"], set()).add(r["pa"])
    jobs_by_agent = {
        r["agent_id"]: r
        for r in conn.execute("SELECT * FROM jobs").fetchall()
    }
    savers = {
        r["agent_id"]
        for r in conn.execute(
            "SELECT agent_id FROM ambitions WHERE status='active' AND kind='save_nest_egg'"
        ).fetchall()
    }
    return {
        "venues": venues,
        "venues_by_id": venues_by_id,
        "ids_by_name": ids_by_name,
        "school_id": ids_by_name.get("Miniville School"),
        "rival_places": rival_places,
        "jobs_by_agent": jobs_by_agent,
        "savers": savers,
        "food_venues": _by_tags(venues, ["food"], DINING_KINDS),
        "shop_venues": _by_tags(venues, SHOP_TAGS, SHOP_KINDS),
        "default_leisure": _by_tags(venues, LEISURE_TAGS),
        "leisure_cache": {},
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


def _apply_personal_habits(agent: sqlite3.Row, day: int, seed: str,
                           plan: dict[int, tuple[int, str]], ctx: dict,
                           leisure_venues: list[dict], season: str) -> None:
    """Apply individual chronotype offsets, favorite third-place pull, and
    rival workplace avoidance on a separate RNG stream so the base `plan`
    stream stays invariant."""
    keys = agent.keys() if hasattr(agent, "keys") else ()
    raw_traits = agent["traits_json"] if "traits_json" in keys else None
    if not raw_traits:
        return
    try:
        traits = json.loads(raw_traits)
    except Exception:
        return
    if not isinstance(traits, dict) or "chronotype" not in traits:
        return

    aid = int(agent["id"])
    home = agent["home_place_id"]
    hr = rng_for(seed, "habit", aid, day)
    wake_off = int(traits.get("wake_offset", 0))
    sleep_off = int(traits.get("sleep_offset", 0))

    if not agent["is_child"]:
        if wake_off < 0:
            for t in range(max(0, WAKE_TICK + wake_off), WAKE_TICK):
                if plan.get(t) == (home, "sleep"):
                    plan[t] = (home, "home")
        elif wake_off > 0:
            for t in range(WAKE_TICK, min(TICKS_PER_DAY, WAKE_TICK + wake_off)):
                if plan.get(t) == (home, "home"):
                    plan[t] = (home, "sleep")

        if sleep_off < 0:
            for t in range(max(0, SLEEP_TICK + sleep_off), SLEEP_TICK):
                if plan.get(t) == (home, "home"):
                    plan[t] = (home, "sleep")
        elif sleep_off > 0:
            for t in range(SLEEP_TICK, min(TICKS_PER_DAY, SLEEP_TICK + sleep_off)):
                if plan.get(t) == (home, "sleep"):
                    plan[t] = (home, "home")

    fav_id = agent["favorite_place_id"] if "favorite_place_id" in keys else None
    venues_by_id = ctx.get("venues_by_id") or {v["id"]: v for v in ctx["venues"]}
    fav_venue = venues_by_id.get(fav_id) if fav_id else None
    avoid_ids = (ctx.get("rival_places") or {}).get(aid, set())

    is_saver = aid in (ctx.get("savers") or set())
    if is_saver:
        for t in MEAL_TICKS:
            if plan.get(t, (None, None))[1] == "eat_out" and hr.random() < 0.5:
                plan[t] = (home, "eat")
        for t in SHOPPING_WINDOWS:
            if plan.get(t, (None, None))[1] == "shopping" and hr.random() < 0.5:
                plan[t] = (home, "home")

    if not fav_venue and not avoid_ids:
        return

    for t, (pid, act) in list(plan.items()):
        if act != "leisure":
            continue
        if pid in avoid_ids and leisure_venues:
            safe = [v for v in leisure_venues
                    if v["id"] not in avoid_ids and v["open_tick"] <= t <= v["close_tick"]]
            if safe:
                v = hr.choice(safe)
                if not (v["tags"] & OUTDOOR_TAGS and season == "winter"):
                    plan[t] = (v["id"], "leisure")
                    pid = v["id"]
        if fav_venue and pid != fav_venue["id"]:
            if fav_venue["open_tick"] <= t <= fav_venue["close_tick"]:
                if fav_venue["tags"] & OUTDOOR_TAGS and hr.random() >= OUTDOOR_APPEAL[season]:
                    continue
                curiosity = float(traits.get("curiosity", 0.0))
                p_fav = max(0.15, min(0.55, 0.35 - 0.15 * curiosity))
                if hr.random() < p_fav:
                    plan[t] = (fav_venue["id"], "leisure")


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
    holiday = ctx["holiday"] if "holiday" in ctx else holiday_for(conn, day)

    jobs_map = ctx.get("jobs_by_agent")
    job = jobs_map.get(agent["id"]) if jobs_map is not None else conn.execute("SELECT * FROM jobs WHERE agent_id=?", (agent["id"],)).fetchone()
    venues_by_id = ctx.get("venues_by_id") or {v["id"]: v for v in ctx["venues"]}
    job_venue = venues_by_id.get(job["place_id"]) if job else None
    works_today = bool(job) and not wknd and not agent["is_child"]
    if holiday and holiday.day_off and not (job_venue and "health" in job_venue["tags"]):
        works_today = False
    attends_holiday = bool(holiday) and not (
        holiday.adults_only and agent["is_child"]
    ) and rng_for(seed, "holiday", agent["id"], day).random() < holiday.p_attend

    htags = tuple(_hobby_tags(hobbies))
    lcache = ctx.get("leisure_cache")
    if lcache is not None:
        leisure_venues = lcache.get(htags)
        if leisure_venues is None:
            leisure_venues = _by_tags(ctx["venues"], list(htags)) or ctx.get("default_leisure") or _by_tags(ctx["venues"], LEISURE_TAGS)
            lcache[htags] = leisure_venues
    else:
        leisure_venues = _by_tags(ctx["venues"], list(htags)) or _by_tags(ctx["venues"], LEISURE_TAGS)
    food_venues = ctx.get("food_venues") if "food_venues" in ctx else _by_tags(ctx["venues"], ["food"], DINING_KINDS)
    shop_venues = ctx.get("shop_venues") if "shop_venues" in ctx else _by_tags(ctx["venues"], SHOP_TAGS, SHOP_KINDS)
    school_id = ctx["school_id"]

    for t in range(TICKS_PER_DAY):
        place, act = home, "sleep"
        if works_today and job["shift_start"] < job["shift_end"]:
            in_work_shift = job["shift_start"] <= t < job["shift_end"]
        elif works_today:
            in_work_shift = t >= job["shift_start"] or t < job["shift_end"]
        else:
            in_work_shift = False
        if in_work_shift:
            length = (job["shift_end"] - job["shift_start"]) % TICKS_PER_DAY
            mid = (job["shift_start"] + length // 2) % TICKS_PER_DAY
            plan[t] = (job["place_id"], "break" if t == mid else "work")
            continue

        in_school = (
            agent["is_child"] and agent["age"] >= 5 and not wknd
            and school_in_session(day) and SCHOOL_START <= t < SCHOOL_END
            and school_id
        )
        if in_school:
            plan[t] = (school_id, "eat" if t in MEAL_TICKS else "school")
            continue

        in_holiday = (
            holiday and attends_holiday
            and holiday.start_tick <= t < holiday.end_tick
        )
        if in_holiday:
            venue_id = ctx["ids_by_name"].get(holiday.venue) if holiday.venue else None
            plan[t] = (venue_id or home, "celebrate")
            continue

        if works_today and t == job["shift_end"]:
            plan[t] = (home, "eat")
            continue

        if t < WAKE_TICK or t >= SLEEP_TICK:
            plan[t] = (place, act)
            continue

        if t in (14, 26, 38):  # meal windows
            if food_venues and r.random() < (0.15 if not wknd else 0.3):
                v = r.choice(food_venues)
                plan[t] = (v["id"], "eat_out")
                continue
            plan[t] = (home, "eat")
            continue

        if t in SHOPPING_WINDOWS and shop_venues and not agent["is_child"]:
            if r.random() < 0.30:
                v = r.choice(shop_venues)
                if v["open_tick"] <= t <= v["close_tick"]:
                    plan[t] = (v["id"], "shopping")
                    continue

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

    _apply_personal_habits(agent, day, seed, plan, ctx, leisure_venues, season)

    meeting = ctx.get("meetings", {}).get(agent["id"])
    if meeting:
        venue_id, meet_tick, _name = meeting
        if in_work_shift_for(works_today, job, meet_tick) is False:
            for t in range(meet_tick, min(meet_tick + GATHERING_TICKS, TICKS_PER_DAY)):
                if t in MEAL_TICKS:
                    continue      # nobody misses dinner for the club
                plan[t] = (venue_id, "gathering")

    return [(t, p, a) for t, (p, a) in sorted(plan.items())]


def in_work_shift_for(works_today: bool, job, tick: int) -> bool:
    """True when the tick falls inside a shift — a gathering never pulls
    someone off their post."""
    if not works_today or job is None:
        return False
    start, end = job["shift_start"], job["shift_end"]
    if start < end:
        return start <= tick < end
    return tick >= start or tick < end


def rebuild_day_plans(conn: sqlite3.Connection, day: int, seed: str) -> int:
    conn.execute("DELETE FROM plans")
    agents = conn.execute("SELECT * FROM agents WHERE alive=1").fetchall()
    ctx = _plan_ctx(conn)
    ctx["holiday"] = holiday_for(conn, day)
    from .groups import meetings_today
    ctx["meetings"] = meetings_today(conn, day)
    rows = []
    for a in agents:
        rows.extend(
            (a["id"], t, p, act) for t, p, act in build_plan(conn, a, day, seed, ctx))
    conn.executemany(
        "INSERT INTO plans(agent_id,tick,place_id,activity) VALUES(?,?,?,?)", rows)
    conn.commit()
    return len(rows)
