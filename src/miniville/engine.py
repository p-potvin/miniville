"""The world tick loop."""
from __future__ import annotations

import sqlite3

from . import (
    ambitions,
    chronicle,
    conflict,
    crime,
    economy,
    enterprise,
    events,
    groups,
    growth,
    book_club,
    commons_library,
    fire_brigade,
    plaques,
    sabbaticals,
    theater,
    flea_market,
    guilds,
    jubilee,
    renovations,
    harvest,
    health,
    history,
    pets,
    radio,
    sports,
    youth,
    civic_projects,
    competitions,
    correspondence,
    culture,
    individual,
    keepsakes,
    mentorship,
    milestones,
    neighborhood,
    jobs,
    memory,
    politics,
    press,
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
from .needs import apply_all_needs, apply_needs
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
        individual.grounded_town_event(conn, tick, seed, r)


def _pay_and_spend(conn: sqlite3.Connection, tick: int) -> dict:
    """Wages out of the businesses, household spending back into them."""
    paid = economy.pay_wages(conn, tick)
    spent = economy.charge_spending(conn, tick)
    return {"wages": paid, **spent}


def _day_start(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Settle yesterday's books, age the town, then build today."""
    stats: dict = {"birthdays": seasons.birthdays(conn, tick, seed)}
    individual.ensure_profiles(conn, seed)

    economy.ensure_businesses(conn)
    economy.record_day(conn, tick)
    stats["businesses"] = economy.settle_businesses(conn, tick, seed)
    stats["pensions"] = economy.pay_pensions(conn, tick)
    stats["rent"] = economy.collect_rent(conn, tick, seed)
    stats["levy"] = economy.weekly_levy(conn, tick, seed)
    stats["labour"] = economy.wage_dynamics(conn, tick, seed)
    stats["enterprise"] = enterprise.due(conn, tick, seed)
    stats["shocks"] = shocks.apply_due(conn, tick, seed)
    stats["retirements"] = len(jobs.retirements(conn, tick, seed))
    if day_of(tick) % 7 == 0:
        stats["turnover"] = jobs.turnover(conn, tick, seed)
        stats["hiring"] = jobs.hiring_pass(conn, tick, seed)["hired"]
        stats["careers"] = jobs.careers(conn, tick, seed)
        stats["groups"] = groups.refresh_standing(conn)
        # public opinion moves weekly, before any motion reads it: the town's
        # own conditions and the Gazette's reach pull it in different directions
        stats["opinion"] = press.update(conn, tick)

    stats["plans"] = rebuild_day_plans(conn, day_of(tick), seed)
    seasons.announce_day(conn, tick)
    stats["life_events"] = daily_life_lottery(conn, tick, seed)
    stats["individual"] = individual.daily_happenings(conn, tick, seed)
    stats["mentorship"] = mentorship.due(conn, tick, seed)
    stats["keepsakes"] = keepsakes.due(conn, tick, seed)
    stats["culture"] = culture.due(conn, tick, seed)
    stats["neighborhood"] = neighborhood.due(conn, tick, seed)
    stats["health"] = health.due(conn, tick, seed)
    stats["pets"] = pets.due(conn, tick, seed)
    stats["flea_market"] = flea_market.due(conn, tick, seed)
    stats["youth"] = youth.due(conn, tick, seed)
    stats["radio"] = radio.due(conn, tick, seed)
    stats["guilds"] = guilds.due(conn, tick, seed)
    stats["harvest"] = harvest.due(conn, tick, seed)
    stats["sports"] = sports.due(conn, tick, seed)
    stats["fire_brigade"] = fire_brigade.due(conn, tick, seed)
    stats["book_club"] = book_club.due(conn, tick, seed)
    stats["renovations"] = renovations.due(conn, tick, seed)
    stats["jubilee"] = jubilee.due(conn, tick, seed)
    stats["theater"] = theater.due(conn, tick, seed)
    stats["commons_library"] = commons_library.due(conn, tick, seed)
    stats["plaques"] = plaques.due(conn, tick, seed)
    stats["sabbaticals"] = sabbaticals.due(conn, tick, seed)
    stats["history"] = history.due(conn, tick, seed)
    stats["civic_projects"] = civic_projects.due(conn, tick, seed)
    stats["milestones"] = milestones.due(conn, tick, seed)
    stats["competitions"] = competitions.due(conn, tick, seed)
    stats["ambitions"] = ambitions.due(conn, tick, seed)
    stats["correspondence"] = correspondence.due(conn, tick, seed)
    stats["crime"] = crime.daily_crime(conn, tick, seed)
    stats["betrayals"] = spouse_discovery(conn, tick, seed)
    stats["deaths"] = mortality.daily_mortality(conn, tick, seed)
    stats["births"] = growth.births(conn, tick, seed)
    stats["standing"] = reputation.accrue(conn, tick, seed)
    decided = politics.due(conn, tick)
    if decided:
        stats["politics"] = decided
    acted = conflict.due(conn, tick, seed)
    if acted:
        stats["conflict"] = acted
    return stats


def step(conn: sqlite3.Connection, seed: str) -> dict:
    """Advance one tick. Returns stats."""
    tick = int(get_meta(conn, "tick", "0") or 0)
    stats = {"tick": tick}
    if tick_of_day(tick) == 0:
        stats.update(_day_start(conn, tick, seed))
    _move_agents(conn, tick)
    apply_conditions(conn, tick)              # sick agents stay home resting
    crime.apply_jail(conn)                    # ...and the jailed sit at Town Hall
    apply_deviations(conn, tick, seed)        # mood can push agents off-plan

    apply_all_needs(conn)
    stats["interactions"] = run_encounters(conn, tick, seed)
    stats["personal_events"] = individual.tick_happenings(conn, tick, seed)
    _pay_and_spend(conn, tick)
    _ambient_town_event(conn, tick, seed)

    set_meta(conn, "tick", str(tick + 1))
    conn.commit()

    if tick_of_day(tick) == TICKS_PER_DAY - 1:
        individual.update_favorite_places(conn, day_of(tick))
        text = chronicle.write_day(conn, day_of(tick), seed)
        stats["chronicle"] = len(text)
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
