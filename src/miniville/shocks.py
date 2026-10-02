"""Operator-injected shocks — god mode for Miniville.

The town runs itself, but the operator can poke it: shut a business down, burn
a venue to the ground, or call a festival. Every shock is recorded in the
`shocks` table so the event ledger stays auditable. Disasters reuse the
economy's machinery — layoffs delete jobs the same way a market failure does,
and a `businesses.reopen_day` overrides the usual 21-day reopening so a fire's
repair window is what the operator says it is. Festivals need no mutation at
all: `seasons.holiday_for` surfaces a festival row as a one-day Holiday, so
schedules, needs and crowd-mingling handle them for free.

Shocks are inputs, not rolls — the only randomness is which bystanders a fire
injures, seeded per (seed, place, tick).
"""
from __future__ import annotations

import json
import sqlite3

from . import economy
from .db import get_meta
from .events import HISTORIC, MAJOR, MINOR, emit
from .rng import rng_for
from .seasons import fmt_date, holiday_on
from .timekeeper import TICKS_PER_DAY, day_of, tick_of_day

SHOCK_KINDS = ("closure", "fire", "festival")

FIRE_REPAIR_DAYS = 14      # a burnt venue reopens a fortnight later, by default
FIRE_INJURY_CAP = 3        # god mode is cruel, not genocidal
# festival window, in ticks of day: 15:00–20:00 — after work lets out, before
# bedtime. Not a day off: the crowd is whoever chooses to come.
FESTIVAL_START, FESTIVAL_END = 30, 40
FESTIVAL_P_ATTEND = 0.55


def _find_venue(conn: sqlite3.Connection, venue: str) -> sqlite3.Row | None:
    if str(venue).isdigit():
        row = conn.execute(
            "SELECT * FROM places WHERE id=?", (int(venue),)).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM places WHERE name LIKE ? ORDER BY name",
        (f"%{venue}%",)).fetchone()


def _apply_disaster(conn: sqlite3.Connection, kind: str, place_id: int,
                    name: str, tick: int, reopen_day: int | None,
                    seed: str) -> dict:
    """Shut a venue now: lay off its staff, empty it, cancel today's trips."""
    staff = conn.execute(
        "SELECT agent_id FROM jobs WHERE place_id=?", (place_id,)).fetchall()
    conn.execute("DELETE FROM jobs WHERE place_id=?", (place_id,))
    conn.execute("UPDATE agents SET work_place_id=NULL WHERE work_place_id=?",
                 (place_id,))

    occupants = conn.execute(
        """SELECT s.agent_id, a.home_place_id FROM agent_state s
           JOIN agents a ON a.id=s.agent_id
           WHERE s.place_id=? AND a.alive=1 AND s.activity != 'sleep'""",
        (place_id,)).fetchall()

    hurt = 0
    if kind == "fire" and occupants:
        r = rng_for(seed, "shock_fire", place_id, tick)
        victims = r.sample(list(occupants), min(FIRE_INJURY_CAP, len(occupants)))
        hurt = len(victims)
        for v in victims:
            days = r.randint(1, 3)
            conn.execute(
                """INSERT OR REPLACE INTO conditions(agent_id,kind,until_tick)
                   VALUES(?,?,?)""",
                (v["agent_id"], "sick", tick + days * TICKS_PER_DAY))
            emit(conn, tick, "life_event", a=v["agent_id"], importance=MINOR,
                 text=f"was hurt in the fire at {name}", tag="injured")

    # the venue empties out, and nobody else is going there today
    for v in occupants:
        conn.execute(
            "UPDATE agent_state SET place_id=?, activity='resting' WHERE agent_id=?",
            (v["home_place_id"], v["agent_id"]))
    conn.execute(
        """UPDATE plans SET
               place_id=(SELECT home_place_id FROM agents
                         WHERE agents.id=plans.agent_id),
               activity='home'
           WHERE place_id=? AND tick>=?""",
        (place_id, tick_of_day(tick)))

    if kind == "closure":
        conn.execute(
            """UPDATE businesses SET status='closed', closed_tick=?, reopen_day=?,
                   balance_cents=0, price_index=1.0 WHERE place_id=?""",
            (tick, reopen_day, place_id))
        text = f"{name} closed without warning"
        if staff:
            text += f"; {len(staff)} people lost their jobs"
        emit(conn, tick, "town_event", place_id=place_id, importance=MAJOR,
             text=text, tag="shock_closure")
    else:
        # insurance rebuilds, so the balance carries over — the venue just
        # cannot serve anyone until the doors are back on
        conn.execute(
            """UPDATE businesses SET status='closed', closed_tick=?, reopen_day=?
               WHERE place_id=?""",
            (tick, reopen_day, place_id))
        bits = []
        if staff:
            bits.append(f"{len(staff)} out of work")
        if hurt:
            bits.append(f"{hurt} resident(s) hurt")
        detail_txt = f" — {', '.join(bits)};" if bits else ";"
        emit(conn, tick, "town_event", place_id=place_id, importance=HISTORIC,
             text=f"a fire gutted {name}{detail_txt} it will be weeks "
                  f"before the doors reopen", tag="shock_fire")
    return {"laid_off": len(staff), "hurt": hurt}


def inject(conn: sqlite3.Connection, kind: str, venue: str, seed: str,
           day: int | None = None, days: int | None = None) -> dict:
    """Inject a shock. `day` is the 0-based sim day it lands on (None = today
    for disasters, tomorrow for festivals). `days` sets how long a closure or
    fire keeps the venue shut. Returns {'ok': bool, 'message': str}."""
    if kind not in SHOCK_KINDS:
        return {"ok": False, "message": f"unknown shock {kind!r} "
                                        f"(pick from {', '.join(SHOCK_KINDS)})"}
    tick = int(get_meta(conn, "tick", "0") or 0)
    today = day_of(tick)
    place = _find_venue(conn, venue)
    if place is None:
        return {"ok": False, "message": f"no venue matches {venue!r}"}
    if place["kind"] == "home":
        return {"ok": False,
                "message": f"{place['name']} is a home — shocks hit venues"}
    economy.ensure_businesses(conn)
    biz = conn.execute(
        "SELECT * FROM businesses WHERE place_id=?", (place["id"],)).fetchone()

    if kind == "festival":
        target = today + 1 if day is None else day
        if target <= today:
            return {"ok": False, "message": "festivals must be scheduled for a "
                                            "future day — today's plans are made"}
        if holiday_on(target):
            return {"ok": False,
                    "message": f"{fmt_date(target)} already has "
                               f"{holiday_on(target).name}"}
        if conn.execute(
                "SELECT 1 FROM shocks WHERE kind='festival' AND day=?",
                (target,)).fetchone():
            return {"ok": False,
                    "message": f"{fmt_date(target)} already has a festival"}
        name = f"{place['name']} Festival"
        detail = {
            "name": name, "start": FESTIVAL_START, "end": FESTIVAL_END,
            "p_attend": FESTIVAL_P_ATTEND,
            "text": f"Miniville turns out for a festival at {place['name']}.",
        }
        conn.execute(
            "INSERT INTO shocks(kind,place_id,day,tick,detail) VALUES(?,?,?,?,?)",
            (kind, place["id"], target, tick, json.dumps(detail)))
        emit(conn, tick, "town_event", place_id=place["id"], importance=MINOR,
             text=f"a festival is announced at {place['name']} for "
                  f"{fmt_date(target)}", tag="festival_announced")
        conn.commit()
        return {"ok": True,
                "message": f"festival scheduled at {place['name']} for day "
                           f"{target + 1} ({fmt_date(target)})"}

    # closure | fire
    target = today if day is None else day
    if target < today:
        return {"ok": False, "message": "that day has already passed"}
    if kind == "closure" and biz and biz["status"] == "closed":
        return {"ok": False,
                "message": f"{place['name']} is already closed"}

    reopen_day = None
    if days is not None:
        reopen_day = target + days
    elif kind == "fire":
        reopen_day = target + FIRE_REPAIR_DAYS

    detail = {"reopen_day": reopen_day}
    if target > today:
        conn.execute(
            "INSERT INTO shocks(kind,place_id,day,tick,detail) VALUES(?,?,?,?,?)",
            (kind, place["id"], target, tick, json.dumps(detail)))
        conn.commit()
        return {"ok": True,
                "message": f"{kind} scheduled at {place['name']} for day "
                           f"{target + 1} ({fmt_date(target)})"}

    out = _apply_disaster(conn, kind, place["id"], place["name"], tick,
                          reopen_day, seed)
    conn.execute(
        """INSERT INTO shocks(kind,place_id,day,tick,applied,detail)
           VALUES(?,?,?,?,1,?)""",
        (kind, place["id"], today, tick, json.dumps(detail)))
    conn.commit()
    reopen_txt = f"; reopens day {reopen_day + 1}" if reopen_day else ""
    return {"ok": True,
            "message": f"{kind} at {place['name']}: {out['laid_off']} laid off, "
                       f"{out['hurt']} hurt{reopen_txt}"}


def apply_due(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Land scheduled disasters whose day has come. Runs at each day start,
    after settlement and before the day's plans are rebuilt. Festivals need no
    mutation — holiday_for surfaces them on their day — so they're only marked
    applied once the day has passed."""
    day = day_of(tick)
    rows = conn.execute(
        """SELECT s.id, s.kind, s.place_id, s.detail, p.name
           FROM shocks s JOIN places p ON p.id=s.place_id
           WHERE s.applied=0 AND s.day<=? AND s.kind != 'festival'""",
        (day,)).fetchall()
    for s in rows:
        detail = json.loads(s["detail"] or "{}")
        _apply_disaster(conn, s["kind"], s["place_id"], s["name"], tick,
                        detail.get("reopen_day"), seed)
        conn.execute("UPDATE shocks SET applied=1 WHERE id=?", (s["id"],))
    conn.execute(
        "UPDATE shocks SET applied=1 WHERE applied=0 AND kind='festival' AND day<?",
        (day,))
    if rows:
        conn.commit()
    return len(rows)


def list_shocks(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT s.id, s.kind, s.day, s.tick, s.applied, s.detail,
                  p.name AS venue
           FROM shocks s LEFT JOIN places p ON p.id=s.place_id
           ORDER BY s.id DESC""").fetchall()]
