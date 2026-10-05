"""The world tick loop."""
from __future__ import annotations

import sqlite3

from . import (
    chronicle,
    economy,
    events,
    groups,
    growth,
    jobs,
    memory,
    reputation,
    mortality,
    newspaper,
    seasons,
    shocks,
)
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


def _pay_and_spend(conn: sqlite3.Connection, tick: int) -> dict:
    """Wages out of the businesses, household spending back into them."""
    paid = economy.pay_wages(conn, tick)
    spent = economy.charge_spending(conn, tick)
    return {"wages": paid, **spent}


def _day_start(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Settle yesterday's books, age the town, then build today."""
    # birthdays run before the plan rebuild so a child who comes of age today
    # is planned as an adult
    stats: dict = {"birthdays": seasons.birthdays(conn, tick, seed)}

    # record_day must run before settle_businesses zeroes the daily counters,
    # and settlement must run before plans are rebuilt so a business that
    # closed overnight is not on anybody's schedule
    economy.ensure_businesses(conn)
    economy.record_day(conn, tick)
    stats["businesses"] = economy.settle_businesses(conn, tick, seed)
    stats["rent"] = economy.collect_rent(conn, tick, seed)
    stats["levy"] = economy.weekly_levy(conn, tick, seed)
    stats["labour"] = economy.wage_dynamics(conn, tick, seed)
    # operator shocks land after the books settle but before the town plans
    # its day, so a venue that burnt overnight is nobody's destination
    stats["shocks"] = shocks.apply_due(conn, tick, seed)
    # the labour market turns over before plans are built: a retiree is no
    # longer planned at work, and a new hire is planned at their new venue
    stats["retirements"] = len(jobs.retirements(conn, tick, seed))
    if day_of(tick) % 7 == 0:
        stats["turnover"] = jobs.turnover(conn, tick, seed)
        stats["hiring"] = jobs.hiring_pass(conn, tick, seed)["hired"]
        stats["careers"] = jobs.careers(conn, tick, seed)
        stats["groups"] = groups.refresh_standing(conn)

    stats["plans"] = rebuild_day_plans(conn, day_of(tick), seed)
    seasons.announce_day(conn, tick)
    stats["life_events"] = daily_life_lottery(conn, tick, seed)
    stats["betrayals"] = spouse_discovery(conn, tick, seed)
    # deaths settle before births: a widow is no longer a spouse, so the
    # couple cannot also welcome a child on the same day
    stats["deaths"] = mortality.daily_mortality(conn, tick, seed)
    stats["births"] = growth.births(conn, tick, seed)
    # what the town made of yesterday, after everyone who acted on it is gone
    stats["standing"] = reputation.accrue(conn, tick, seed)
    return stats


def step(conn: sqlite3.Connection, seed: str) -> dict:
    """Advance one tick. Returns stats."""
    tick = int(get_meta(conn, "tick", "0") or 0)
    stats = {"tick": tick}
    if tick_of_day(tick) == 0:
        stats.update(_day_start(conn, tick, seed))
    _move_agents(conn, tick)
    apply_conditions(conn, tick)              # sick agents stay home resting
    apply_deviations(conn, tick, seed)        # mood can push agents off-plan

    st = conn.execute(
        "SELECT agent_id, activity FROM agent_state s JOIN agents a ON a.id=s.agent_id WHERE a.alive=1"
    ).fetchall()
    for row in st:
        apply_needs(conn, row["agent_id"], row["activity"])
    stats["interactions"] = run_encounters(conn, tick, seed)
    _pay_and_spend(conn, tick)
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
