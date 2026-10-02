"""Calendar, seasons, holidays, and birthdays for Miniville."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .events import NOTABLE, emit
from .rng import seed_int
from .timekeeper import day_of, tick_of_day

DAYS_PER_YEAR = 365
MONTHS = (
    ("Jan", 31), ("Feb", 28), ("Mar", 31), ("Apr", 30),
    ("May", 31), ("Jun", 30), ("Jul", 31), ("Aug", 31),
    ("Sep", 30), ("Oct", 31), ("Nov", 30), ("Dec", 31),
)
MONTH_NAMES = tuple(name for name, _ in MONTHS)


@dataclass(frozen=True)
class Holiday:
    name: str
    month: int
    dom: int
    venue: str | None
    start_tick: int
    end_tick: int
    day_off: bool
    p_attend: float
    adults_only: bool
    text: str
    tag: str = "holiday"   # "festival" for injected festival shocks


HOLIDAYS = (
    Holiday("New Year's Day", 1, 1, "Lakeshore Marina", 22, 26, True, 0.25,
            False, "A few brave residents gather at Lakeshore Marina for a polar plunge."),
    Holiday("Founders' Day", 4, 18, "Miniville Community Center", 20, 30, True, 0.5,
            False, "Miniville gathers at the Community Center to celebrate its founding."),
    Holiday("Summer Fair", 7, 4, "Lush Meadow Park", 30, 44, True, 0.65,
            False, "Miniville turns out at Lush Meadow Park for the Summer Fair."),
    Holiday("Harvest Festival", 10, 10, "Old Mill Shops", 20, 34, False, 0.45,
            False, "Neighbors browse the Old Mill Shops at the Harvest Festival."),
    Holiday("Halloween Parade", 10, 31, "Lush Meadow Park", 34, 40, False, 0.5,
            False, "Miniville gathers at Lush Meadow Park for the Halloween Parade."),
    Holiday("Thanksgiving", 11, 26, None, 34, 40, True, 0.9, False,
            "Families across Miniville gather at home for Thanksgiving."),
    Holiday("Christmas Eve Service", 12, 24, "First Congregational Church", 36, 40,
            False, 0.4, False, "Residents gather at First Congregational Church for Christmas Eve."),
    Holiday("Christmas Day", 12, 25, None, 24, 36, True, 0.9, False,
            "Families across Miniville celebrate Christmas Day at home."),
    Holiday("New Year's Eve", 12, 31, "The Bijou Theater", 40, 47, False, 0.45,
            True, "Adults ring in the new year together at The Bijou Theater."),
)

OUTDOOR_TAGS = {"outdoors", "water"}
OUTDOOR_APPEAL = {"winter": 0.35, "spring": 0.85, "summer": 1.0, "autumn": 0.7}
SEASON_WEATHER = {
    "winter": [
        "Snow drifts through Miniville.",
        "A cold snap settles over the town.",
        "Frost silvers the rooftops before dawn.",
    ],
    "spring": [
        "Spring rain patters across Miniville.",
        "Blossoms brighten the town's streets.",
        "Showers pass over the Old Mill Quarter.",
    ],
    "summer": [
        "Summer heat shimmers over Miniville.",
        "Sunshine draws crowds to Lakeshore.",
        "A bright, warm afternoon settles over town.",
    ],
    "autumn": [
        "Leaves scatter across Miniville's sidewalks.",
        "A soft fog settles over the town.",
        "Autumn colors brighten Lush Meadow Park.",
    ],
}

def date_of(day: int) -> tuple[int, int, int]:
    """Return the 1-based (year, month, day-of-month) for a day index."""
    year, remaining = divmod(day, DAYS_PER_YEAR)
    for month, (_, length) in enumerate(MONTHS, start=1):
        if remaining < length:
            return year + 1, month, remaining + 1
        remaining -= length
    raise AssertionError("unreachable calendar date")


def fmt_date(day: int) -> str:
    year, month, dom = date_of(day)
    return f"{MONTH_NAMES[month - 1]} {dom}, Year {year}"


def day_of_year(day: int) -> int:
    return day % DAYS_PER_YEAR


def season_of(day: int) -> str:
    month = date_of(day)[1]
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def holiday_on(day: int) -> Holiday | None:
    doy = day_of_year(day)
    for holiday in HOLIDAYS:
        holiday_doy = sum(
            length for _, length in MONTHS[:holiday.month - 1]
        ) + holiday.dom - 1
        if holiday_doy == doy:
            return holiday
    return None


def holiday_for(conn: sqlite3.Connection, day: int) -> Holiday | None:
    """The holiday in effect on `day`: a calendar holiday, or a festival shock.

    Festival shocks are stored in the `shocks` table at injection time; the
    operator needs no schema access to call one off. Calendar holidays always
    win — `shocks.inject` refuses to schedule a festival on a holiday anyway.
    """
    holiday = holiday_on(day)
    if holiday is not None:
        return holiday
    row = conn.execute(
        "SELECT place_id, detail FROM shocks WHERE kind='festival' AND day=?",
        (day,)).fetchone()
    if row is None:
        return None
    d = json.loads(row["detail"] or "{}")
    venue = None
    if row["place_id"] is not None:
        p = conn.execute(
            "SELECT name FROM places WHERE id=?", (row["place_id"],)).fetchone()
        venue = p["name"] if p else None
    return Holiday(
        name=d.get("name", "Town Festival"), month=0, dom=0, venue=venue,
        start_tick=int(d.get("start", 30)), end_tick=int(d.get("end", 40)),
        day_off=False, p_attend=float(d.get("p_attend", 0.55)),
        adults_only=False,
        text=d.get("text", "Miniville holds a festival."), tag="festival")


def school_in_session(day: int) -> bool:
    _, month, dom = date_of(day)
    if (month == 6 and dom >= 15) or month in (7, 8):
        return False
    if (month == 12 and dom >= 22) or (month == 1 and dom <= 2):
        return False
    holiday = holiday_on(day)
    return not (holiday and holiday.day_off)


def birthday_doy(seed: str, agent_id: int, birth_day: int | None = None) -> int:
    if birth_day is not None:
        return birth_day % DAYS_PER_YEAR
    return seed_int(seed, "birthday", agent_id) % DAYS_PER_YEAR


def birthdays(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Age residents whose birthdays fall today; day zero is a fresh start."""
    if tick_of_day(tick) != 0 or day_of(tick) == 0:
        return 0
    doy = day_of_year(day_of(tick))
    residents = conn.execute(
        "SELECT id, age, is_child, birth_day FROM agents WHERE alive=1").fetchall()
    aged = 0
    for resident in residents:
        if birthday_doy(seed, resident["id"], resident["birth_day"]) != doy:
            continue
        new_age = resident["age"] + 1
        is_adult = resident["is_child"] and new_age >= 18
        conn.execute(
            "UPDATE agents SET age=?, is_child=? WHERE id=?",
            (new_age, 0 if is_adult else resident["is_child"], resident["id"]))
        if is_adult:
            emit(conn, tick, "life_event", a=resident["id"], importance=NOTABLE,
                 text="came of age at 18", tag="coming_of_age")
        aged += 1
    conn.commit()
    return aged


def announce_day(conn: sqlite3.Connection, tick: int) -> None:
    """Record any holiday or seasonal arrival at the start of this day."""
    day = day_of(tick)
    holiday = holiday_for(conn, day)
    if holiday:
        place_id = None
        if holiday.venue:
            place = conn.execute(
                "SELECT id FROM places WHERE name=?", (holiday.venue,)).fetchone()
            place_id = place["id"] if place else None
        emit(conn, tick, "town_event", place_id=place_id, importance=3,
             text=holiday.text, tag=holiday.tag)

    _, month, dom = date_of(day)
    if dom == 1 and month in (3, 6, 9, 12):
        season = season_of(day).capitalize()
        emit(conn, tick, "town_event", importance=2,
             text=f"{season} arrives in Miniville.", tag="season")
