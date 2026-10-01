"""Life events: dating arc, employment changes, illness, moving house.

Daily lottery runs once per simulated day (tick_of_day==0) with deterministic
per-(agent, day) probabilities. Dating arc progresses inside encounters.
"""
from __future__ import annotations

import json
import sqlite3

from . import economy
from .events import HISTORIC, MAJOR, MINOR, NOTABLE, emit
from .rng import chance, rng_for
from .world import workplace_tags_for

P_FIRE, P_ILL, P_MOVE = 0.005, 0.01, 0.004
P_HIRE_MAX = 0.15        # cap on the derived hiring rate


def _hire(conn: sqlite3.Connection, agent: sqlite3.Row, tick: int, r) -> None:
    wid = workplace_tags_for(agent["occupation"] or "")
    rows = economy.open_workplaces(conn)
    if not rows:                       # every business in town has closed
        return
    scored = sorted(rows, key=lambda x: -len(set(json.loads(x["tags"])) & set(wid)))
    place = scored[0]
    conn.execute(
        "INSERT OR REPLACE INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days)"
        " VALUES(?,?,?,?,?,?,62)",
        (agent["id"], place["id"], agent["occupation"],
         r.randint(economy.WAGE_MIN_CENTS, economy.WAGE_MAX_CENTS),
         r.choice([12, 14, 16, 18]), 0))
    conn.execute(
        "UPDATE jobs SET shift_end=shift_start+? WHERE agent_id=?",
        (r.randint(14, 18), agent["id"]))
    conn.execute("UPDATE agents SET work_place_id=? WHERE id=?", (place["id"], agent["id"]))
    venue = conn.execute("SELECT name FROM places WHERE id=?", (place["id"],)).fetchone()
    emit(conn, tick, "life_event", place_id=place["id"], a=agent["id"],
         importance=NOTABLE, text=f"was hired at {venue['name']}", tag="hire")


def _fire(conn: sqlite3.Connection, agent: sqlite3.Row, tick: int) -> None:
    job = conn.execute("SELECT place_id FROM jobs WHERE agent_id=?",
                       (agent["id"],)).fetchone()
    conn.execute("DELETE FROM jobs WHERE agent_id=?", (agent["id"],))
    conn.execute("UPDATE agents SET work_place_id=NULL WHERE id=?", (agent["id"],))
    venue = conn.execute("SELECT name FROM places WHERE id=?",
                         (job["place_id"],)).fetchone()
    emit(conn, tick, "life_event", place_id=job["place_id"], a=agent["id"],
         importance=MAJOR, text=f"lost their job at {venue['name']}", tag="fired")


def _fall_ill(conn: sqlite3.Connection, agent: sqlite3.Row, tick: int, r) -> None:
    days = r.randint(1, 3)
    conn.execute(
        "INSERT OR REPLACE INTO conditions(agent_id,kind,until_tick) VALUES(?,?,?)",
        (agent["id"], "sick", tick + days * 48))
    emit(conn, tick, "life_event", a=agent["id"], importance=MINOR,
         text=f"fell ill ({days} day{'s' if days>1 else ''})", tag="sick")


def _move_house(conn: sqlite3.Connection, agent: sqlite3.Row, tick: int, r) -> None:
    hid = agent["household_id"]
    homes = conn.execute("SELECT id FROM places WHERE kind='home'").fetchall()
    new_home = r.choice(homes)["id"]
    members = conn.execute(
        "SELECT id FROM agents WHERE household_id=?", (hid,)).fetchall()
    for m in members:
        conn.execute("UPDATE agents SET home_place_id=? WHERE id=?", (new_home, m["id"]))
    conn.execute("UPDATE households SET home_place_id=? WHERE id=?", (new_home, hid))
    emit(conn, tick, "life_event", a=agent["id"], importance=MINOR,
         text=f"moved house with {len(members)-1} other(s)", tag="move")


def daily_life_lottery(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Once-per-day random life events. Returns event count."""
    n = 0
    employed = {r["agent_id"] for r in conn.execute("SELECT agent_id FROM jobs")}
    adults = conn.execute(
        "SELECT * FROM agents WHERE alive=1 AND is_child=0").fetchall()
    # A flat hiring rate is applied to the unemployed and a flat firing rate to
    # the employed, so the town shed jobs whenever most people had one. Derive
    # the hiring rate from the firing rate instead: hiring tracks firing and the
    # employment level holds steady.
    n_emp = sum(1 for a in adults if a["id"] in employed)
    n_unemp = max(1, len(adults) - n_emp)
    p_hire = min(P_HIRE_MAX, P_FIRE * n_emp / n_unemp)

    for a in adults:
        r = rng_for(seed, "life", a["id"], tick)
        if a["id"] in employed:
            if r.random() < P_FIRE:
                _fire(conn, a, tick); n += 1
        elif r.random() < p_hire:
            _hire(conn, a, tick, r); n += 1
        if r.random() < P_ILL:
            _fall_ill(conn, a, tick, r); n += 1
        if r.random() < P_MOVE:
            _move_house(conn, a, tick, r); n += 1
    conn.commit()
    return n


def apply_conditions(conn: sqlite3.Connection, tick: int) -> None:
    """Sick agents stay home resting; expired conditions are cleared."""
    conn.execute("DELETE FROM conditions WHERE until_tick < ?", (tick,))
    sick = conn.execute(
        """SELECT c.agent_id, a.home_place_id FROM conditions c
           JOIN agents a ON a.id=c.agent_id WHERE c.kind='sick'""").fetchall()
    for row in sick:
        conn.execute(
            "UPDATE agent_state SET place_id=?, activity='resting' WHERE agent_id=?",
            (row["home_place_id"], row["agent_id"]))
    conn.commit()


def dating_arc_check(conn: sqlite3.Connection, a_id: int, b_id: int,
                     rel: sqlite3.Row, tick: int, seed: str) -> str | None:
    """Progress sweetheart -> partner (move in) -> spouse (marry).
    Returns emitted event tag or None."""
    lo, hi = min(a_id, b_id), max(a_id, b_id)
    r = rng_for(seed, "dating", lo, hi, tick)
    rom, fam = rel["romance"], rel["familiarity"]
    if rel["label"] == "sweetheart" and rom >= 70 and r.random() < 0.15:
        mover, keeper = (b_id, a_id) if r.random() < 0.5 else (a_id, b_id)
        hid = conn.execute(
            "SELECT household_id, home_place_id FROM agents WHERE id=?",
            (keeper,)).fetchone()
        conn.execute(
            "UPDATE agents SET household_id=?, home_place_id=? WHERE id=?",
            (hid["household_id"], hid["home_place_id"], mover))
        conn.execute(
            "UPDATE relationships SET label='partner' WHERE a_id=? AND b_id=?",
            (lo, hi))
        emit(conn, tick, "life_event", a=mover, b=keeper, importance=MAJOR,
             text="moved in together", tag="cohabitation")
        return "cohabitation"
    if rel["label"] == "partner" and rom >= 85 and fam >= 40 and r.random() < 0.08:
        for aid in (a_id, b_id):
            conn.execute(
                "UPDATE agents SET marital_status='married_present' WHERE id=?", (aid,))
        conn.execute(
            "UPDATE relationships SET label='spouse' WHERE a_id=? AND b_id=?",
            (lo, hi))
        emit(conn, tick, "life_event", a=a_id, b=b_id, importance=HISTORIC,
             text="got married!", tag="marriage")
        return "marriage"
    return None


def romance_allowed(conn: sqlite3.Connection, a: sqlite3.Row, b: sqlite3.Row,
                    rel_label: str) -> bool:
    """Romance is suppressed when either is married to someone else."""
    if rel_label == "spouse":
        return True
    for agent in (a, b):
        if agent["marital_status"] in ("married_present", "married",
                                      "married_spouse_present"):
            return False
    return True
