"""Mortality: residents die of old age, and the town absorbs it.

Until now Miniville only grew — births and immigration in, nothing out — so
there was no generational turnover and the town could not really change over
years. This adds an age-dependent hazard (Gompertz) evaluated once per
simulated day, plus the consequences that make a death a story rather than a
row update: the spouse is widowed, the job is freed for someone else, children
left with no adult are taken in, and the people close to the deceased remember
them.

Deterministic like everything else: the hazard is a pure function of age and
health, and the roll comes from ``rng_for(seed, "death", agent_id, tick)``.
"""
from __future__ import annotations

import math
import os
import sqlite3

from .events import HISTORIC, emit
from .growth import _free_home, _new_household
from .memory import remember
from .rng import rng_for

# Gompertz hazard h(age) = A * exp(B * age) per year. Calibrated against a US
# life table: ~1.5/1000 at 40, ~19/1000 at 70, and a crude rate near 10/1000/yr
# for a town with this age profile (the real US figure is ~8.2/1000).
GOMPERTZ_A = 5e-5
GOMPERTZ_B = 0.085
SICK_MULTIPLIER = 4.0        # being ill multiplies the hazard
MIN_DEATH_AGE = 18           # children are not subject to mortality here
CLOSE_FAMILIARITY = 30       # who counts as close enough to mourn

# 1.0 is realistic. Raise it to watch generations turn over in a short run.
SCALE = float(os.environ.get("MINIVILLE_MORTALITY_SCALE", "1.0"))


def daily_hazard(age: int, sick: bool = False) -> float:
    """Probability that a resident dies on a given day."""
    h = GOMPERTZ_A * math.exp(GOMPERTZ_B * max(0, age)) / 365.0
    if sick:
        h *= SICK_MULTIPLIER
    return h * SCALE


def _widow(conn: sqlite3.Connection, deceased_id: int) -> int | None:
    """Mark the surviving spouse widowed. Returns their id, or None."""
    rel = conn.execute(
        """SELECT a_id, b_id FROM relationships
           WHERE label='spouse' AND (a_id=? OR b_id=?)""",
        (deceased_id, deceased_id)).fetchone()
    if not rel:
        return None
    survivor = rel["b_id"] if rel["a_id"] == deceased_id else rel["a_id"]
    conn.execute("UPDATE agents SET marital_status='widowed' WHERE id=?", (survivor,))
    conn.execute("UPDATE relationships SET label='widowed' WHERE a_id=? AND b_id=?",
                 (rel["a_id"], rel["b_id"]))
    return survivor


def _rehome_children(conn: sqlite3.Connection, deceased: sqlite3.Row, r) -> int:
    """Place orphaned children with a living adult household when possible."""
    hid = deceased["household_id"]
    if hid is None:
        return 0
    adults = conn.execute(
        """SELECT COUNT(*) c FROM agents
           WHERE household_id=? AND alive=1 AND is_child=0 AND id!=?""",
        (hid, deceased["id"])).fetchone()["c"]
    if adults:
        return 0
    kids = conn.execute(
        "SELECT id, name FROM agents WHERE household_id=? AND alive=1 AND is_child=1",
        (hid,)).fetchall()
    if not kids:
        return 0

    related = conn.execute(
        """SELECT adult.household_id
           FROM relationships r
           JOIN agents adult
             ON adult.id=CASE WHEN r.a_id=? THEN r.b_id ELSE r.a_id END
           WHERE (r.a_id=? OR r.b_id=?)
             AND adult.alive=1 AND adult.is_child=0
             AND adult.household_id IS NOT NULL AND adult.household_id!=?
           ORDER BY r.familiarity DESC, adult.id
           LIMIT 1""",
        (deceased["id"], deceased["id"], deceased["id"], hid)).fetchone()
    if related:
        destination = conn.execute(
            "SELECT id, home_place_id FROM households WHERE id=?",
            (related["household_id"],)).fetchone()
    else:
        households = conn.execute(
            """SELECT h.id, h.home_place_id FROM households h
               WHERE EXISTS (
                   SELECT 1 FROM agents a
                   WHERE a.household_id=h.id AND a.alive=1 AND a.is_child=0
               )
               ORDER BY h.id""").fetchall()
        destination = r.choice(households) if households else None

    if destination:
        for kid in kids:
            conn.execute(
                "UPDATE agents SET household_id=?, home_place_id=? WHERE id=?",
                (destination["id"], destination["home_place_id"], kid["id"]))
    else:
        home = _free_home(conn, r)
        surname = (kids[0]["name"] or "Miniville").split()[-1]
        _new_household(conn, [k["id"] for k in kids], home, surname)
    return len(kids)


def _mourn(conn: sqlite3.Connection, deceased_id: int, name: str, tick: int) -> int:
    """Everyone close to the deceased carries the memory."""
    rows = conn.execute(
        """SELECT CASE WHEN a_id=? THEN b_id ELSE a_id END AS other
           FROM relationships
           WHERE (a_id=? OR b_id=?) AND familiarity >= ?""",
        (deceased_id, deceased_id, deceased_id, CLOSE_FAMILIARITY)).fetchall()
    for row in rows:
        remember(conn, row["other"], tick, f"{name} died.", kind="death",
                 importance=5)
    return len(rows)


def _die(conn: sqlite3.Connection, agent: sqlite3.Row, tick: int, r) -> int | None:
    """Retire one resident and settle their affairs. Returns the widow's id."""
    aid = agent["id"]
    conn.execute("UPDATE agents SET alive=0 WHERE id=?", (aid,))
    conn.execute("DELETE FROM jobs WHERE agent_id=?", (aid,))
    conn.execute("UPDATE agents SET work_place_id=NULL WHERE id=?", (aid,))
    conn.execute("DELETE FROM conditions WHERE agent_id=?", (aid,))
    conn.execute("DELETE FROM plans WHERE agent_id=?", (aid,))
    conn.execute("UPDATE agent_state SET activity='deceased', place_id=NULL "
                 "WHERE agent_id=?", (aid,))

    survivor = _widow(conn, aid)
    orphans = _rehome_children(conn, agent, r)

    text = f"died at {agent['age']}"
    if survivor:
        sname = conn.execute("SELECT name FROM agents WHERE id=?",
                             (survivor,)).fetchone()["name"]
        text += f", leaving {sname} widowed"
    if orphans:
        text += (f" and {orphans} child"
                 f"{'ren' if orphans > 1 else ''} to be taken in")
    emit(conn, tick, "life_event", a=aid, b=survivor, importance=HISTORIC,
         text=text, tag="death")
    _mourn(conn, aid, agent["name"], tick)
    return survivor


def daily_mortality(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Once-per-day mortality roll. Returns the number of deaths."""
    sick = {r["agent_id"] for r in conn.execute(
        "SELECT agent_id FROM conditions WHERE kind='sick'")}
    adults = conn.execute(
        "SELECT * FROM agents WHERE alive=1 AND is_child=0 AND age >= ?",
        (MIN_DEATH_AGE,)).fetchall()
    deaths = 0
    for a in adults:
        r = rng_for(seed, "death", a["id"], tick)
        if r.random() < daily_hazard(a["age"] or 0, a["id"] in sick):
            _die(conn, a, tick, r)
            deaths += 1
    conn.commit()
    return deaths
