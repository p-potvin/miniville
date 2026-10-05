"""Standing: what the town thinks of a resident.

The ledger already records everything the town knows — who did someone a
favour, who was promoted, who was caught cheating, who fell behind on rent.
This module turns that record into a number and lets the number matter:

* `accrue` reads each day's events and moves the standing of everyone named
  in them, then lets standing decay so a reputation is something you keep
  earning;
* encounters warm or cool with it, so a well-regarded resident is met more
  kindly than a stranger with a grudge against them;
* hiring prefers the better-regarded candidate when two people match a post
  equally well;
* a death is recorded with the standing they held and the number of people
  who actually knew them, which is the closest thing the town has to an
  obituary.

Nothing here invents state: it is a reading of `events`, so a replayed tick
produces the same standing it did the first time.
"""
from __future__ import annotations

import json
import sqlite3

from .events import HISTORIC, NOTABLE, emit
from .rng import rng_for
from .timekeeper import day_of

# what the town thinks of a thing you did
WEIGHTS = {
    "favor": 2,            # did somebody a good turn
    "favor_repaid": 1,
    "marriage": 3,
    "birth": 2,
    "coming_of_age": 1,
    "promoted": 2,
    "hired": 1,
    "retired": 1,
    "injured": 1,          # hurt in a fire: the town's sympathy
    "fired": -1,
    "quit": -1,
    "separation": -4,
    "rent_distress": -2,
    "downsize": -3,
    "betrayal": -10,       # caught cheating
}
STANDING_MIN, STANDING_MAX = -100, 100
# A reputation fades if you stop earning it: once a month, a point back toward
# zero — but only for the residents who have a reputation worth losing. A
# multiplicative decay was tried first and the integer truncation compounded
# (40 fell to 0 in two months instead of drifting).
DECAY_EVERY_DAYS = 30
DECAY_FLOOR = 15


def standing_of(conn: sqlite3.Connection, agent_id: int) -> int:
    row = conn.execute("SELECT standing FROM agents WHERE id=?",
                       (agent_id,)).fetchone()
    return int(row["standing"]) if row else 0


def _bump(conn: sqlite3.Connection, agent_id: int | None, delta: int) -> None:
    if not agent_id or not delta:
        return
    conn.execute(
        """UPDATE agents SET standing =
             MAX(?, MIN(?, COALESCE(standing,0) + ?)) WHERE id=?""",
        (STANDING_MIN, STANDING_MAX, int(delta), agent_id))


def accrue(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Move standing from yesterday's ledger, then let it fade."""
    day = day_of(tick) - 1
    if day < 1:
        return {"moved": 0, "risen": 0, "fallen": 0}
    rows = conn.execute(
        """SELECT kind, a_id, b_id, data FROM events
           WHERE day=? AND (a_id IS NOT NULL OR b_id IS NOT NULL)""",
        (day,)).fetchall()
    moved = risen = fallen = 0
    for r in rows:
        try:
            data = json.loads(r["data"] or "{}")
        except ValueError:
            continue
        tag = data.get("tag") or r["kind"]
        weight = WEIGHTS.get(tag)
        if weight is None:
            continue
        # the subject of the event gets the full weight; the other party to a
        # betrayal or a split gets the sympathy of the wronged
        for aid, delta in ((r["a_id"], weight), (r["b_id"], -weight)):
            if not aid:
                continue
            before = standing_of(conn, aid)
            _bump(conn, aid, delta)
            after = standing_of(conn, aid)
            if after != before:
                moved += 1
                risen += 1 if after > before else 0
                fallen += 1 if after < before else 0
    if day % DECAY_EVERY_DAYS == 0:
        conn.execute(
            """UPDATE agents
               SET standing = standing - (CASE WHEN standing > 0 THEN 1 ELSE -1 END)
               WHERE alive=1 AND ABS(standing) >= ?""", (DECAY_FLOOR,))
    return {"moved": moved, "risen": risen, "fallen": fallen}


def notable_residents(conn: sqlite3.Connection, n: int = 3) -> list[dict]:
    """The town's most admired and most disliked, for the chronicle."""
    rows = conn.execute(
        """SELECT id, name, standing FROM agents
           WHERE alive=1 AND standing != 0
           ORDER BY standing DESC LIMIT ?""", (n,)).fetchall()
    return [dict(r) for r in rows]


def obituary(conn: sqlite3.Connection, deceased: sqlite3.Row, tick: int,
             seed: str) -> None:
    """Record how the town regarded them, and how many people knew them."""
    known = conn.execute(
        """SELECT COUNT(*) n FROM relationships
           WHERE (a_id=? OR b_id=?) AND familiarity >= 20""",
        (deceased["id"], deceased["id"])).fetchone()["n"]
    standing = standing_of(conn, deceased["id"])
    if standing >= 25:
        regard = "well thought of"
    elif standing <= -25:
        regard = "not much missed"
    else:
        regard = "known about town"
    emit(conn, tick, "town_event", a=deceased["id"], importance=HISTORIC,
         text=f"{deceased['name']} was {regard}; {known} resident"
              f"{'s' if known != 1 else ''} had actually known them",
         tag="obituary", standing=standing, known=known)
