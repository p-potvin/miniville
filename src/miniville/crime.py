"""Petty crime: hardship tempts, the town's police decide who is caught.

The town had poverty (arrears, downsizing, the broke) but poverty had no
social consequence except a standing penalty for falling behind on rent. In
a real town the desperate occasionally take what is not theirs, and how the
town answers it is a story about the town.

Once a day a resident in **hardship** — of working age, out of work, with
less than a fortnight's groceries to their name — may steal: from a shop's
till or from someone they know. Whether they are **caught** depends on how
well the Town Hall is staffed. A thief who is caught gives the money back,
pays what they can of a fine into the town purse, spends a day or two in the
cells at Town Hall, loses standing, and the person they stole from does not
forget. A thief who is not caught keeps the money, and the victim remembers
being robbed by someone they will never know.

Money only moves (till/wallet -> thief, thief -> victim and purse); nothing
here mints it. Deterministic: rolls come from `rng_for(seed, "crime", ...)`.
"""
from __future__ import annotations

import json
import sqlite3

from .economy import GROCERY_CENTS, RETIREMENT_AGE, town_credit
from .enterprise import is_commercial
from .events import MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY, day_of

HARDSHIP_CENTS = GROCERY_CENTS * 14     # under a fortnight's groceries
P_OFFEND = 0.015                        # per day, per resident in hardship
P_SHOPLIFT = 0.6                        # ...else a theft from an acquaintance
CATCH_BASE = 0.20
CATCH_PER_OFFICER = 0.04                # each Town Hall post adds this
CATCH_MAX = 0.75
FINE_CENTS = 15_000
GRUDGE = 20.0                           # affinity a victim loses toward the thief
JAIL_DAYS = (1, 2)


def town_hall_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM places WHERE name='Town Hall'").fetchone()
    return row["id"] if row else None


def catch_chance(conn: sqlite3.Connection) -> float:
    hall = town_hall_id(conn)
    staff = conn.execute("SELECT COUNT(*) n FROM jobs WHERE place_id=?",
                         (hall,)).fetchone()["n"] if hall else 0
    return min(CATCH_MAX, CATCH_BASE + CATCH_PER_OFFICER * staff)


def _in_hardship(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT a.id, a.name, s.money_cents FROM agents a
           JOIN agent_state s ON s.agent_id=a.id
           WHERE a.alive=1 AND a.is_child=0 AND a.age BETWEEN 18 AND ?
             AND s.money_cents < ?
             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.agent_id=a.id)
             AND NOT EXISTS (SELECT 1 FROM conditions c
                             WHERE c.agent_id=a.id AND c.kind='jailed')
           ORDER BY a.id""", (RETIREMENT_AGE - 1, HARDSHIP_CENTS)).fetchall()


def _shop_target(conn: sqlite3.Connection, r) -> sqlite3.Row | None:
    shops = [b for b in conn.execute(
        """SELECT b.place_id, b.balance_cents, p.name, p.tags, p.kind
           FROM businesses b JOIN places p ON p.id=b.place_id
           WHERE b.status='open' ORDER BY b.place_id""").fetchall()
        if is_commercial(set(json.loads(b["tags"] or "[]")), b["kind"])]
    return r.choice(shops) if shops else None


def _person_target(conn: sqlite3.Connection, thief: int, amount: int, r):
    known = conn.execute(
        """SELECT CASE WHEN r.a_id=? THEN r.b_id ELSE r.a_id END other
           FROM relationships r WHERE (r.a_id=? OR r.b_id=?)
             AND r.label NOT IN ('spouse','partner')
           ORDER BY other""", (thief, thief, thief)).fetchall()
    ids = [k["other"] for k in known]
    if not ids:
        return None
    rows = conn.execute(
        f"""SELECT a.id, a.name FROM agents a JOIN agent_state s ON s.agent_id=a.id
            WHERE a.alive=1 AND a.is_child=0 AND s.money_cents >= ?
              AND a.id IN ({','.join('?' * len(ids))}) ORDER BY a.id""",
        (amount, *ids)).fetchall()
    return r.choice(rows) if rows else None


def _grudge(conn: sqlite3.Connection, victim: int, thief: int) -> None:
    lo, hi = min(victim, thief), max(victim, thief)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(?,?,10,?,0,'rival')
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             affinity = relationships.affinity - ?,
             label = CASE WHEN relationships.affinity - ? <= -20
                          AND relationships.label NOT IN ('spouse','partner','widowed')
                          THEN 'rival' ELSE relationships.label END""",
        (lo, hi, -GRUDGE, GRUDGE, GRUDGE))


def _pay(conn, cents: int, *, from_agent=None, to_agent=None,
         from_place=None, to_place=None) -> None:
    if from_agent:
        conn.execute("UPDATE agent_state SET money_cents=money_cents-? WHERE agent_id=?",
                     (cents, from_agent))
    if from_place:
        conn.execute("UPDATE businesses SET balance_cents=balance_cents-? WHERE place_id=?",
                     (cents, from_place))
    if to_agent:
        conn.execute("UPDATE agent_state SET money_cents=money_cents+? WHERE agent_id=?",
                     (cents, to_agent))
    if to_place:
        conn.execute("UPDATE businesses SET balance_cents=balance_cents+? WHERE place_id=?",
                     (cents, to_place))


def daily_crime(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Once a day: the desperate may steal; the town may catch them."""
    day = day_of(tick)
    caught = thefts = 0
    p_catch = catch_chance(conn)
    for t in _in_hardship(conn):
        r = rng_for(seed, "crime", t["id"], day)
        if r.random() >= P_OFFEND:
            continue
        amount = r.randint(1_500, 8_000) if r.random() < P_SHOPLIFT else -1
        shop = person = None
        if amount > 0:
            shop = _shop_target(conn, r)
        else:
            amount = r.randint(2_000, 15_000)
            person = _person_target(conn, t["id"], amount, r)
        if shop is None and person is None:
            continue
        thefts += 1
        where = shop["name"] if shop else person["name"]
        if shop:
            _pay(conn, amount, from_place=shop["place_id"], to_agent=t["id"])
        else:
            _pay(conn, amount, from_agent=person["id"], to_agent=t["id"])

        if r.random() < p_catch:
            caught += 1
            # the money goes back, and the fine is whatever they can pay
            if shop:
                _pay(conn, amount, from_agent=t["id"], to_place=shop["place_id"])
            else:
                _pay(conn, amount, from_agent=t["id"], to_agent=person["id"])
            left = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=?",
                                (t["id"],)).fetchone()["money_cents"]
            fine = max(0, min(FINE_CENTS, left))
            if fine:
                _pay(conn, fine, from_agent=t["id"])
                town_credit(conn, fine)
            days = r.randint(*JAIL_DAYS)
            conn.execute(
                "INSERT OR REPLACE INTO conditions(agent_id,kind,until_tick) VALUES(?,?,?)",
                (t["id"], "jailed", tick + days * TICKS_PER_DAY))
            emit(conn, tick, "life_event", a=t["id"], b=person["id"] if person else None,
                 place_id=shop["place_id"] if shop else None, importance=NOTABLE,
                 text=(f"was caught stealing ${amount / 100:,.0f} from {where} "
                       f"and spent {days} day{'s' if days > 1 else ''} in the cells"),
                 tag="arrest", amount=amount, fine=fine)
            if person:
                _grudge(conn, person["id"], t["id"])
                remember(conn, person["id"], tick,
                         f"{t['name']} stole from me. They were caught.",
                         kind="crime", importance=4)
            remember(conn, t["id"], tick, f"I was caught stealing from {where}.",
                     kind="crime", importance=5)
        else:
            emit(conn, tick, "town_event", a=person["id"] if person else None,
                 place_id=shop["place_id"] if shop else None, importance=MINOR,
                 text=(f"someone stole ${amount / 100:,.0f} from {where}; "
                       f"nobody was caught"), tag="theft", amount=amount)
            if person:
                remember(conn, person["id"], tick,
                         f"Someone stole ${amount / 100:,.0f} from me.",
                         kind="crime", importance=3)
    conn.commit()
    return {"thefts": thefts, "caught": caught}


def apply_jail(conn: sqlite3.Connection) -> None:
    """Anyone in the cells is at Town Hall, whatever their plan said."""
    hall = town_hall_id(conn)
    if hall is None:
        return
    conn.execute(
        """UPDATE agent_state SET place_id=?, activity='jailed'
           WHERE agent_id IN (SELECT agent_id FROM conditions WHERE kind='jailed')""",
        (hall,))
