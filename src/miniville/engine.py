"""The world tick loop."""
from __future__ import annotations

import sqlite3

from . import chronicle, events, growth, memory, mortality, newspaper, seasons
from .db import get_meta, set_meta
from .deviations import apply_deviations
from .encounters import run_encounters
from .events import MINOR, emit
from .favors import spouse_discovery
from .life import apply_conditions, daily_life_lottery
from .needs import apply_needs
from .rng import rng_for
from .schedules import rebuild_day_plans
from .timekeeper import TICKS_PER_DAY, day_of, tick_of_day

# occasional town-level happenings
TOWN_EVENTS = [
    ("town", "The Miniville Gazette publishes its weekly edition."),
    ("town", "A farmers' market sets up in the Community Center lot."),
    ("town", "The high school team wins a home game; Greenhill celebrates."),
    ("town", "A pipe bursts downtown; Town Hall crews scramble."),
]


def _move_agents(conn: sqlite3.Connection, tick: int) -> int:
    tod = tick_of_day(tick)
    cur = conn.execute(
        """UPDATE agent_state SET place_id = (
               SELECT place_id FROM plans
               WHERE plans.agent_id=agent_state.agent_id AND plans.tick=?),
               activity = (
               SELECT activity FROM plans
               WHERE plans.agent_id=agent_state.agent_id AND plans.tick=?)
           WHERE EXISTS (SELECT 1 FROM plans
               WHERE plans.agent_id=agent_state.agent_id AND plans.tick=?)""",
        (tod, tod, tod))
    return cur.rowcount


def _ambient_town_event(conn: sqlite3.Connection, tick: int, seed: str) -> None:
    r = rng_for(seed, "town", tick)
    if r.random() < 0.012:
        weather = [("weather", text) for text in seasons.SEASON_WEATHER[
            seasons.season_of(day_of(tick))]]
        kind, text = r.choice(TOWN_EVENTS + weather)
        events.emit(conn, tick, "town_event", importance=2, text=text, tag=kind)


def _wages_and_spending(conn: sqlite3.Connection, tick: int) -> None:
    # pay at end of shift tick
    tod = tick_of_day(tick)
    rows = conn.execute(
        "SELECT agent_id, wage_cents FROM jobs WHERE shift_end=?", (tod,)).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE agent_state SET money_cents=money_cents+? WHERE agent_id=?",
            (row["wage_cents"], row["agent_id"]))
    # dining out cost
    conn.execute(
        """UPDATE agent_state SET money_cents=money_cents-1400
           WHERE activity='eat_out'""")


def step(conn: sqlite3.Connection, seed: str) -> dict:
    """Advance one tick. Returns stats."""
    tick = int(get_meta(conn, "tick", "0") or 0)
    stats = {"tick": tick}
    if tick_of_day(tick) == 0:
        n = rebuild_day_plans(conn, day_of(tick), seed)
        stats["plans"] = n
        stats["birthdays"] = seasons.birthdays(conn, tick, seed)
        seasons.announce_day(conn, tick)
        stats["life_events"] = daily_life_lottery(conn, tick, seed)
        stats["betrayals"] = spouse_discovery(conn, tick, seed)
        # deaths settle before births: a widow is no longer a spouse, so the
        # couple cannot also welcome a child on the same day
        stats["deaths"] = mortality.daily_mortality(conn, tick, seed)
        stats["births"] = growth.births(conn, tick, seed)
    _move_agents(conn, tick)
    apply_conditions(conn, tick)              # sick agents stay home resting
    apply_deviations(conn, tick, seed)        # mood can push agents off-plan

    st = conn.execute(
        "SELECT agent_id, activity FROM agent_state s JOIN agents a ON a.id=s.agent_id WHERE a.alive=1"
    ).fetchall()
    for row in st:
        apply_needs(conn, row["agent_id"], row["activity"])
    stats["interactions"] = run_encounters(conn, tick, seed)
    _wages_and_spending(conn, tick)
    _ambient_town_event(conn, tick, seed)

    set_meta(conn, "tick", str(tick + 1))
    conn.commit()

    if tick_of_day(tick) == TICKS_PER_DAY - 1:
        text = chronicle.write_day(conn, day_of(tick), seed)
        stats["chronicle"] = len(text)
        # fold the day's events into each participant's memory stream, then
        # every third day distill the strongest memories into a reflection
        stats["memories"] = memory.record_event_memories(conn, tick)
        if day_of(tick) % 3 == 2:
            stats["reflections"] = memory.reflect_all(conn, day_of(tick))
        if day_of(tick) % newspaper.DAYS_PER_WEEK == newspaper.DAYS_PER_WEEK - 1:
            newspaper.publish_week(conn, newspaper.week_of(day_of(tick)), seed)
    return stats


def run(conn: sqlite3.Connection, ticks: int, seed: str, verbose=False) -> None:
    day_interactions = 0
    for _ in range(ticks):
        stats = step(conn, seed)
        day_interactions += stats.get("interactions", 0)
        if verbose and stats.get("tick", 0) % 48 == 47:
            d = day_of(stats["tick"])
            print(f"  day {d+1}: {day_interactions} interactions")
            day_interactions = 0
