"""Mood-driven plan deviations. After agents move per schedule, low needs can
push them off-plan: lonely people seek company, bored people go out, the
miserable sometimes cancel plans and wallow.
"""
from __future__ import annotations

import sqlite3

from .rng import rng_for

P_LONELY_OUT, P_BORED_OUT, P_MISERABLE_WALLOW = 0.35, 0.25, 0.30


def _open_public_venues(conn: sqlite3.Connection, tick_of_day: int):
    return conn.execute(
        """SELECT p.id FROM places p
           LEFT JOIN businesses b ON b.place_id=p.id
           WHERE p.kind='public'
             AND p.open_tick <= ? AND p.close_tick > ?
             AND COALESCE(b.status,'open')='open'""",
        (tick_of_day, tick_of_day)).fetchall()


def apply_deviations(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Override agent_state for agents whose mood drives them off-plan."""
    tick_of_day = tick % 48
    if tick_of_day < 12 or tick_of_day > 41:  # sleep hours: no deviations
        return 0
    venues = _open_public_venues(conn, tick_of_day)
    if not venues:
        return 0
    rows = conn.execute(
        """SELECT s.agent_id, s.place_id, s.activity, s.mood, a.home_place_id
           FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE s.activity IN ('home','leisure','idle')
             AND s.mood IN ('lonely','bored','miserable')""").fetchall()
    n = 0
    for row in rows:
        r = rng_for(seed, "deviate", row["agent_id"], tick)
        mood, act = row["mood"], row["activity"]
        if mood == "lonely" and act in ("home", "idle") and r.random() < P_LONELY_OUT:
            conn.execute(
                "UPDATE agent_state SET place_id=?, activity='social_call' WHERE agent_id=?",
                (r.choice(venues)["id"], row["agent_id"]))
            n += 1
        elif mood == "bored" and act == "home" and r.random() < P_BORED_OUT:
            conn.execute(
                "UPDATE agent_state SET place_id=?, activity='leisure' WHERE agent_id=?",
                (r.choice(venues)["id"], row["agent_id"]))
            n += 1
        elif mood == "miserable" and act == "leisure" and r.random() < P_MISERABLE_WALLOW:
            conn.execute(
                "UPDATE agent_state SET place_id=?, activity='wallow' WHERE agent_id=?",
                (row["home_place_id"], row["agent_id"]))
            n += 1
    conn.commit()
    return n
