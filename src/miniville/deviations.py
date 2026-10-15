"""Mood-driven plan deviations. After agents move per schedule, low needs can
push them off-plan: lonely people seek out friends or favorite third places,
bored people pursue hobbies, and the miserable sometimes cancel plans and
wallow. Deviations persist across the next hour (2 ticks) so residents do not
immediately snap back on the next half-hour tick.
"""
from __future__ import annotations

import sqlite3

from .rng import rng_for

P_LONELY_OUT, P_BORED_OUT, P_MISERABLE_WALLOW = 0.35, 0.25, 0.30


def _open_public_venues(conn: sqlite3.Connection, tick_of_day: int):
    return conn.execute(
        """SELECT p.id, p.tags FROM places p
           LEFT JOIN businesses b ON b.place_id=p.id
           WHERE p.kind='public'
             AND p.open_tick <= ? AND p.close_tick > ?
             AND COALESCE(b.status,'open')='open'""",
        (tick_of_day, tick_of_day)).fetchall()


def _friend_venue(conn: sqlite3.Connection, agent_id: int,
                  open_ids: set[int]) -> int | None:
    """Find an open public venue where one of `agent_id`'s friends or partners
    is currently spending time."""
    if not open_ids:
        return None
    rows = conn.execute(
        """SELECT s.place_id FROM relationships r
           JOIN agent_state s
             ON s.agent_id = (CASE WHEN r.a_id=? THEN r.b_id ELSE r.a_id END)
           WHERE (r.a_id=? OR r.b_id=?)
             AND (r.affinity >= 15 OR r.familiarity >= 15)
             AND s.activity != 'sleep'
           ORDER BY r.affinity DESC, r.familiarity DESC LIMIT 5""",
        (agent_id, agent_id, agent_id),
    ).fetchall()
    for r in rows:
        if r["place_id"] in open_ids:
            return int(r["place_id"])
    return None


def _commit_next_tick(conn: sqlite3.Connection, agent_id: int,
                      next_tod: int, place_id: int, activity: str) -> None:
    if next_tod >= 42:
        return
    conn.execute(
        """UPDATE plans SET place_id=?, activity=?
           WHERE agent_id=? AND tick=? AND activity IN ('home','idle')""",
        (place_id, activity, agent_id, next_tod),
    )


def apply_deviations(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Override agent_state for agents whose mood drives them off-plan."""
    tick_of_day = tick % 48
    if tick_of_day < 12 or tick_of_day > 41:  # sleep hours: no deviations
        return 0
    venues = _open_public_venues(conn, tick_of_day)
    if not venues:
        return 0
    open_ids = {int(v["id"]) for v in venues}
    rows = conn.execute(
        """SELECT s.agent_id, s.place_id, s.activity, s.mood,
                  a.home_place_id, a.favorite_place_id
           FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1
             AND s.activity IN ('home','leisure','idle')
             AND s.mood IN ('lonely','bored','miserable')""").fetchall()
    n = 0
    for row in rows:
        aid = int(row["agent_id"])
        r = rng_for(seed, "deviate", aid, tick)
        mood, act = row["mood"], row["activity"]
        if mood == "lonely" and act in ("home", "idle") and r.random() < P_LONELY_OUT:
            dest = _friend_venue(conn, aid, open_ids)
            if dest is None and row["favorite_place_id"] in open_ids:
                dest = int(row["favorite_place_id"])
            if dest is None:
                dest = int(r.choice(venues)["id"])
            conn.execute(
                "UPDATE agent_state SET place_id=?, activity='social_call' WHERE agent_id=?",
                (dest, aid))
            _commit_next_tick(conn, aid, tick_of_day + 1, dest, "social_call")
            n += 1
        elif mood == "bored" and act == "home" and r.random() < P_BORED_OUT:
            dest = (int(row["favorite_place_id"])
                    if row["favorite_place_id"] in open_ids and r.random() < 0.5
                    else int(r.choice(venues)["id"]))
            conn.execute(
                "UPDATE agent_state SET place_id=?, activity='leisure' WHERE agent_id=?",
                (dest, aid))
            _commit_next_tick(conn, aid, tick_of_day + 1, dest, "leisure")
            n += 1
        elif mood == "miserable" and act == "leisure" and r.random() < P_MISERABLE_WALLOW:
            conn.execute(
                "UPDATE agent_state SET place_id=?, activity='wallow' WHERE agent_id=?",
                (row["home_place_id"], aid))
            n += 1
    conn.commit()
    return n
