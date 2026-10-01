"""Needs decay/replenish + mood. Values 0..100 unless noted."""
from __future__ import annotations

import sqlite3

# per-tick decay
DECAY = {"energy": 0.35, "hunger": 0.9, "social": 0.35, "fun": 0.6}

ACTIVITY_EFFECTS = {
    "sleep":   {"energy": +4.2, "hunger": -0.4},
    "work":    {"energy": -0.6, "fun": -0.4, "social": +0.8, "stress": +0.5},
    "school":  {"energy": -0.4, "social": +1.0, "fun": -0.2, "stress": +0.3},
    "break":   {"hunger": +18, "social": +1.5, "energy": +0.6, "stress": -0.4},
    "eat":     {"hunger": +22, "energy": +0.5},
    "eat_out": {"hunger": +24, "fun": +3, "social": +1.5},
    "leisure": {"fun": +4.5, "social": +2.0, "stress": -1.2, "energy": -0.3},
    "celebrate": {"fun": +5.0, "social": +3.0, "stress": -1.5, "energy": -0.4, "hunger": +4},
    "social_call": {"social": +3.5, "fun": +2.0, "energy": -0.4},
    "wallow":  {"stress": +1.0, "fun": -1.0, "social": -1.0, "energy": +0.2},
    "resting": {"energy": +1.5, "stress": -0.5, "social": -0.5},
    "home":    {"fun": +1.0, "stress": -0.6, "energy": +0.4, "social": +0.15},
    "idle":    {},
}

MOOD_RULES = [
    ("miserable", lambda s: s["energy"] < 15 or s["hunger"] < 15 or s["stress"] > 85),
    ("stressed",  lambda s: s["stress"] > 65),
    ("lonely",    lambda s: s["social"] < 20),
    ("bored",     lambda s: s["fun"] < 20),
    ("tired",     lambda s: s["energy"] < 35),
    ("hungry",    lambda s: s["hunger"] < 30),
    ("content",   lambda s: True),
]


def apply_needs(conn: sqlite3.Connection, agent_id: int, activity: str) -> dict:
    st = conn.execute("SELECT * FROM agent_state WHERE agent_id=?", (agent_id,)).fetchone()
    new = dict(st)
    for k, d in DECAY.items():
        new[k] = max(0.0, min(100.0, new[k] - d))
    for k, d in ACTIVITY_EFFECTS.get(activity, {}).items():
        new[k] = max(0.0, min(100.0, new[k] + d))
    for label, rule in MOOD_RULES:
        if rule(new):
            new["mood"] = label
            break
    conn.execute(
        """UPDATE agent_state SET energy=?,hunger=?,social=?,fun=?,stress=?,mood=?
           WHERE agent_id=?""",
        (new["energy"], new["hunger"], new["social"], new["fun"],
         new["stress"], new["mood"], agent_id))
    return new
