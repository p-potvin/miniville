"""Personal life milestones: coming-of-age gifts, decade birthdays, wedding
anniversaries, and workplace retirement send-offs.

In a deep-time town simulation, residents age (`seasons.birthdays`), marry
(`life.dating_arc_check`), and retire (`jobs.retirements`), yet those pivotal
biographical moments used to happen as silent row updates:

* **Coming-of-Age Starter Gift (`on_coming_of_age`)** — children are born with
  `$0` in `agent_state.money_cents`, so turning 18 dropped them straight into
  destitution before the weekly hiring pass could employ them. Now, when a
  young resident comes of age, household adults with savings (`>= $1,000`) chip
  in a strictly conserved `$150–$350` starter gift (`SUM(wallets)` unchanged to
  the cent) so they begin adult life on their feet.
* **Decade Milestone Birthdays (`decade_birthdays`)** — turning 30, 40, 50, 60,
  70, 80, or 90 gathers a resident's closest friends and kin for a milestone
  toast, warming bonds and lifting morale.
* **Wedding Anniversaries (`wedding_anniversaries`)** — married couples celebrate
  their annual anniversary (on a deterministic day of the year for their pair),
  renewing romance (`+8`), warming affinity (`+5`), and exchanging a keepsake
  when available.
* **Workplace Retirement Send-Offs (`retirement_sendoffs`)** — when a veteran
  retires (`tag='retired'`), their former workplace throws a send-off:
  coworkers warm their ties, a profitable employer pays a strictly conserved
  `$25–$60` parting bonus from its till, and the retiree receives a
  commemorative engraved timepiece keepsake.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, describe, emit
from .keepsakes import craft_keepsake, gift_keepsake
from .memory import remember
from .rng import rng_for, seed_int
from .seasons import DAYS_PER_YEAR, birthday_doy, day_of_year
from .timekeeper import day_of

COMING_OF_AGE_GIFT_MIN = 15_000    # $150 conserved family starter gift
COMING_OF_AGE_GIFT_MAX = 35_000    # $350 max starter gift
PARENT_CUSHION_CENTS = 100_000     # household adult needs >= $1,000 to gift
RETIRE_BONUS_MIN = 2_500           # $25 conserved retirement parting bonus
RETIRE_BONUS_MAX = 6_000           # $60 max retirement parting bonus
EMPLOYER_RETIRE_CUSHION = 60_000   # business till needs >= $600 to pay bonus
DECADE_AGES = {30, 40, 50, 60, 70, 80, 90}


def _coming_of_age_gifts(conn: sqlite3.Connection, tick: int,
                         seed: str, r) -> int:
    """When a resident turns 18 today, their household adults pass them a
    strictly conserved starter nest-egg."""
    day = day_of(tick)
    rows = conn.execute(
        """SELECT e.a_id, a.name, a.household_id, a.home_place_id
           FROM events e
           JOIN agents a ON a.id = e.a_id
           WHERE e.day = ? AND e.kind = 'life_event'
             AND e.data LIKE '%"coming_of_age"%'
             AND a.alive = 1
           ORDER BY e.id""",
        (day,),
    ).fetchall()
    gifted = 0
    for row in rows:
        kid_id = int(row["a_id"])
        hid = row["household_id"]
        if hid is None:
            continue
        parents = conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a JOIN agent_state s ON s.agent_id = a.id
               WHERE a.household_id = ? AND a.alive = 1 AND a.is_child = 0
                 AND a.id != ? AND s.money_cents >= ?
               ORDER BY s.money_cents DESC, a.id ASC""",
            (hid, kid_id, PARENT_CUSHION_CENTS),
        ).fetchall()
        if not parents:
            continue
        donor = parents[0]
        did = int(donor["id"])
        amount = r.randint(COMING_OF_AGE_GIFT_MIN, COMING_OF_AGE_GIFT_MAX)
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
            (amount, did),
        )
        conn.execute(
            """UPDATE agent_state
               SET money_cents = money_cents + ?, fun = MIN(100, fun + 12),
                   stress = MAX(0, stress - 8)
               WHERE agent_id = ?""",
            (amount, kid_id),
        )
        lo, hi = min(did, kid_id), max(did, kid_id)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,40.0,45.0,0.0,'kin',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 5.0),
                 affinity = MIN(100.0, relationships.affinity + 6.0),
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        emit(conn, tick, "life_event", place_id=row["home_place_id"],
             a=kid_id, b=did, importance=MINOR,
             text=f"received a ${amount / 100:,.0f} coming-of-age starter gift from {donor['name']}",
             tag="coming_of_age_gift", amount_cents=amount)
        remember(conn, kid_id, tick,
                 f"{donor['name']} gave me ${amount / 100:,.0f} to start my adult life on my 18th birthday.",
                 kind="personal", importance=4)
        gifted += 1
    return gifted


def _decade_birthdays(conn: sqlite3.Connection, tick: int,
                      seed: str) -> int:
    """Celebrate 30th, 40th, 50th, 60th, 70th, 80th, and 90th birthdays."""
    day = day_of(tick)
    if day == 0:
        return 0
    doy = day_of_year(day)
    adults = conn.execute(
        """SELECT id, name, age, birth_day, home_place_id
           FROM agents WHERE alive = 1 AND is_child = 0
           ORDER BY id"""
    ).fetchall()
    celebrated = 0
    for a in adults:
        age = int(a["age"] or 0)
        if age not in DECADE_AGES:
            continue
        if birthday_doy(seed, int(a["id"]), a["birth_day"]) != doy:
            continue
        aid = int(a["id"])
        friends = conn.execute(
            """SELECT CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END AS fid,
                      x.name AS fname
               FROM relationships r
               JOIN agents x ON x.id = (CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END)
               WHERE (r.a_id = ? OR r.b_id = ?) AND x.alive = 1
                 AND r.affinity >= 10
               ORDER BY r.familiarity DESC, r.affinity DESC LIMIT 4""",
            (aid, aid, aid, aid),
        ).fetchall()
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100, fun + 14), social = MIN(100, social + 12),
                   stress = MAX(0, stress - 8)
               WHERE agent_id = ?""",
            (aid,),
        )
        for f in friends:
            fid = int(f["fid"])
            lo, hi = min(aid, fid), max(aid, fid)
            conn.execute(
                """UPDATE relationships
                   SET familiarity = MIN(100.0, familiarity + 2.5),
                       affinity = MIN(100.0, affinity + 3.5),
                       last_met_tick = ?
                   WHERE a_id = ? AND b_id = ?""",
                (tick, lo, hi),
            )
        guest_Note = (
            f" with {', '.join(f['fname'] for f in friends[:2])}"
            if friends else " at home"
        )
        emit(conn, tick, "life_event", place_id=a["home_place_id"],
             a=aid, b=int(friends[0]["fid"]) if friends else None,
             importance=NOTABLE,
             text=f"celebrated their {age}th milestone birthday{guest_Note}",
             tag="milestone_birthday", age=age)
        remember(conn, aid, tick,
                 f"I celebrated my {age}th birthday{guest_Note}.",
                 kind="personal", importance=4)
        celebrated += 1
    return celebrated


def _wedding_anniversaries(conn: sqlite3.Connection, tick: int,
                           seed: str) -> int:
    """Married couples celebrate their annual wedding anniversary."""
    day = day_of(tick)
    if day == 0:
        return 0
    doy = day_of_year(day)
    spouses = conn.execute(
        """SELECT r.a_id, r.b_id, a.name AS aname, b.name AS bname,
                  a.home_place_id
           FROM relationships r
           JOIN agents a ON a.id = r.a_id
           JOIN agents b ON b.id = r.b_id
           WHERE r.label = 'spouse' AND a.alive = 1 AND b.alive = 1
           ORDER BY r.a_id, r.b_id"""
    ).fetchall()
    celebrated = 0
    for sp in spouses:
        aid, bid = int(sp["a_id"]), int(sp["b_id"])
        anniv_doy = seed_int(seed, "anniversary", aid, bid) % DAYS_PER_YEAR
        if anniv_doy != doy:
            continue
        conn.execute(
            """UPDATE relationships
               SET romance = MIN(100.0, romance + 8.0),
                   affinity = MIN(100.0, affinity + 5.0),
                   familiarity = MIN(100.0, familiarity + 2.5),
                   last_met_tick = ?
               WHERE a_id = ? AND b_id = ?""",
            (tick, aid, bid),
        )
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100, fun + 12), social = MIN(100, social + 10),
                   stress = MAX(0, stress - 8)
               WHERE agent_id IN (?, ?)""",
            (aid, bid),
        )
        gift_keepsake(conn, aid, bid, tick, reason="for their wedding anniversary")
        emit(conn, tick, "life_event", place_id=sp["home_place_id"],
             a=aid, b=bid, importance=MINOR,
             text=f"celebrated their wedding anniversary with {sp['bname']}",
             tag="wedding_anniversary")
        remember(conn, aid, tick,
                 f"Celebrated our wedding anniversary with {sp['bname']}.",
                 kind="personal", importance=3)
        remember(conn, bid, tick,
                 f"Celebrated our wedding anniversary with {sp['aname']}.",
                 kind="personal", importance=3)
        celebrated += 1
    return celebrated


def _retirement_sendoffs(conn: sqlite3.Connection, tick: int,
                         seed: str, r) -> int:
    """When a resident retires today, their workplace hosts a send-off."""
    day = day_of(tick)
    rows = conn.execute(
        """SELECT e.a_id, e.place_id, a.name AS aname, p.name AS pname,
                  COALESCE(b.balance_cents, 0) AS balance_cents
           FROM events e
           JOIN agents a ON a.id = e.a_id
           LEFT JOIN places p ON p.id = e.place_id
           LEFT JOIN businesses b ON b.place_id = e.place_id
           WHERE e.day = ? AND e.kind = 'life_event'
             AND e.data LIKE '%"retired"%'
             AND a.alive = 1
           ORDER BY e.id""",
        (day,),
    ).fetchall()
    sendoffs = 0
    for row in rows:
        aid = int(row["a_id"])
        pid = row["place_id"]
        pname = row["pname"] or "their workplace"
        bonus = 0
        if pid is not None and int(row["balance_cents"]) >= EMPLOYER_RETIRE_CUSHION:
            bonus = r.randint(RETIRE_BONUS_MIN, RETIRE_BONUS_MAX)
            conn.execute(
                "UPDATE businesses SET balance_cents = balance_cents - ? WHERE place_id = ?",
                (bonus, pid),
            )
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                (bonus, aid),
            )
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100, fun + 16), social = MIN(100, social + 14),
                   stress = MAX(0, stress - 12)
               WHERE agent_id = ?""",
            (aid,),
        )
        craft_keepsake(conn, aid, tick, seed, craft_override="timepiece")
        bonus_txt = f" and a ${bonus / 100:,.0f} parting bonus" if bonus else ""
        emit(conn, tick, "life_event", place_id=pid, a=aid, importance=NOTABLE,
             text=f"was honoured with a retirement send-off{bonus_txt} at {pname}",
             tag="retirement_sendoff", bonus_cents=bonus)
        remember(conn, aid, tick,
                 f"My coworkers at {pname} threw me a warm retirement send-off{bonus_txt}.",
                 kind="reflection", importance=4)
        sendoffs += 1
    return sendoffs


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    """Run daily milestone checks (coming-of-age gifts, decade birthdays,
    wedding anniversaries, and retirement send-offs)."""
    day = day_of(tick)
    r = rng_for(seed, "milestones", day)
    coa = _coming_of_age_gifts(conn, tick, seed, r)
    decades = _decade_birthdays(conn, tick, seed)
    annivs = _wedding_anniversaries(conn, tick, seed)
    sendoffs = _retirement_sendoffs(conn, tick, seed, r)
    return {
        "coming_of_age_gifts": coa,
        "milestone_birthdays": decades,
        "anniversaries": annivs,
        "retirement_sendoffs": sendoffs,
    }


def list_milestones(conn: sqlite3.Connection, limit: int = 40) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT e.* FROM events e
           WHERE e.kind = 'life_event'
             AND (e.data LIKE '%"coming_of_age_gift"%'
                  OR e.data LIKE '%"milestone_birthday"%'
                  OR e.data LIKE '%"wedding_anniversary"%'
                  OR e.data LIKE '%"retirement_sendoff"%')
           ORDER BY e.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for e in rows:
        try:
            d = json.loads(e["data"] or "{}")
        except Exception:
            d = {}
        out.append({
            "id": e["id"],
            "day": int(e["day"]) + 1,
            "tick": e["tick"],
            "tag": d.get("tag", "milestone"),
            "a_id": e["a_id"],
            "b_id": e["b_id"],
            "text": describe(conn, e),
        })
    return out
