"""Town Cultural Commons: library shelf, community gallery, stage & recipes.

In `v0.20`, residents began crafting named personal keepsakes (`manuscript`,
`artwork`, `woodwork`, `recipe`, `music`, `garment`, `timepiece`). This module
lets those creations enter Miniville's public cultural life:

* **Curated Public Display (`curate_exhibits`)** — creators place their works
  on display or in circulation at the town venue matching the medium:
  - `manuscript` -> `Miniville Public Library`
  - `artwork` / `woodwork` / `garment` -> `Miniville Community Center` or `The Scenic Bean`
  - `music` -> `The Quaint Corner Tavern` or `The Bijou Theater`
  - `recipe` -> `Riverside Diner` or `Sunrise Bakery`
* **Audience Appreciation (`experience_culture`)** — when other residents visit
  those venues for leisure, study, or dining, they read, admire, or savour a
  neighbour's exhibited work (`appreciations += 1`), gaining `+6` fun, `-4`
  stress, and forming a warm affinity toward the creator (`+3.0` affinity,
  `+2.0` familiarity). Works that reach 3+ appreciations emit a `NOTABLE`
  `acclaimed_work` life event and earn the creator `+1` standing.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

ACCLAIM_THRESHOLDS = (3, 8, 15)

_VENUE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "manuscript": ("Miniville Public Library", "The Scenic Bean"),
    "artwork": ("Miniville Community Center", "The Scenic Bean"),
    "woodwork": ("Miniville Community Center", "Old Mill Shops"),
    "garment": ("Miniville Community Center", "Old Mill Shops"),
    "music": ("The Quaint Corner Tavern", "The Bijou Theater"),
    "recipe": ("Riverside Diner", "Sunrise Bakery"),
    "timepiece": ("Miniville Public Library", "Miniville Community Center"),
}


def _venue_map(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        r["name"]: int(r["id"])
        for r in conn.execute("SELECT id, name FROM places WHERE kind != 'home'").fetchall()
    }


def curate_exhibits(conn: sqlite3.Connection, tick: int,
                    seed: str = "miniville") -> int:
    """Place unexhibited keepsakes onto the shelves/walls of town venues."""
    vmap = _venue_map(conn)
    if not vmap:
        return 0
    rows = conn.execute(
        """SELECT k.id, k.title, k.kind, k.creator_id, k.owner_id,
                  a.name AS owner_name
           FROM keepsakes k
           JOIN agents a ON a.id = k.owner_id
           WHERE k.venue_id IS NULL AND a.alive = 1
           ORDER BY k.id ASC LIMIT 4"""
    ).fetchall()
    if not rows:
        return 0

    exhibited = 0
    for k in rows:
        prefs = _VENUE_PREFERENCES.get(k["kind"], ("Miniville Community Center",))
        vid = next((vmap[name] for name in prefs if name in vmap), None)
        if vid is None:
            vid = next(iter(vmap.values()))
        vname = next((name for name, pid in vmap.items() if pid == vid), "town")
        conn.execute(
            "UPDATE keepsakes SET venue_id = ? WHERE id = ?", (vid, k["id"])
        )
        verb = {
            "manuscript": "shelved",
            "artwork": "hung",
            "music": "performed",
            "recipe": "shared",
        }.get(k["kind"], "exhibited")
        emit(conn, tick, "life_event", place_id=vid, a=int(k["owner_id"]),
             importance=MINOR,
             text=f"{verb} {k['title']} at {vname} for the town to enjoy",
             tag="keepsake_exhibited", keepsake_id=k["id"], venue=vname)
        exhibited += 1
    return exhibited


def experience_culture(conn: sqlite3.Connection, tick: int,
                       seed: str = "miniville") -> dict[str, int]:
    """Visitors at cultural venues experience and appreciate exhibited works."""
    day = day_of(tick)
    r = rng_for(seed, "culture_exp", day)
    exhibits = conn.execute(
        """SELECT k.id, k.title, k.kind, k.creator_id, k.venue_id,
                  k.appreciations, c.name AS creator_name,
                  c.alive AS creator_alive, p.name AS venue_name
           FROM keepsakes k
           JOIN agents c ON c.id = k.creator_id
           JOIN places p ON p.id = k.venue_id
           WHERE k.venue_id IS NOT NULL
           ORDER BY k.id ASC"""
    ).fetchall()
    if not exhibits:
        return {"appreciated": 0, "acclaimed": 0}

    # Find living adults who are not the creator to experience up to 3 works/day
    adults = conn.execute(
        "SELECT id, name FROM agents WHERE alive = 1 AND is_child = 0 ORDER BY id"
    ).fetchall()
    if len(adults) < 2:
        return {"appreciated": 0, "acclaimed": 0}

    sample_works = r.sample(list(exhibits), min(3, len(exhibits)))
    appreciated = acclaimed = 0

    for w in sample_works:
        cid = int(w["creator_id"])
        pool = [a for a in adults if int(a["id"]) != cid]
        if not pool:
            continue
        visitor = r.choice(pool)
        vid = int(visitor["id"])
        new_app = int(w["appreciations"] or 0) + 1
        conn.execute(
            "UPDATE keepsakes SET appreciations = ? WHERE id = ?",
            (new_app, w["id"]),
        )
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100, fun + 6), stress = MAX(0, stress - 4)
               WHERE agent_id = ?""",
            (vid,),
        )
        if w["creator_alive"]:
            lo, hi = min(cid, vid), max(cid, vid)
            conn.execute(
                """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                       label,interactions,last_met_tick)
                   VALUES(?,?,4.0,6.0,0.0,'acquaintance',1,?)
                   ON CONFLICT(a_id,b_id) DO UPDATE SET
                     familiarity = MIN(100.0, relationships.familiarity + 2.0),
                     affinity = MIN(100.0, relationships.affinity + 3.0),
                     last_met_tick = excluded.last_met_tick""",
                (lo, hi, tick),
            )
        appreciated += 1

        if new_app in ACCLAIM_THRESHOLDS and w["creator_alive"]:
            emit(conn, tick, "life_event", place_id=w["venue_id"],
                 a=cid, b=vid, importance=NOTABLE,
                 text=f"saw their work ({w['title']}) become a local favourite at {w['venue_name']} ({new_app} admirers)",
                 tag="acclaimed_work", keepsake_id=w["id"], appreciations=new_app)
            remember(conn, cid, tick,
                     f"My work ({w['title']}) has {new_app} admirers at {w['venue_name']}!",
                     kind="reflection", importance=4)
            acclaimed += 1

    return {"appreciated": appreciated, "acclaimed": acclaimed}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    exhibited = curate_exhibits(conn, tick, seed)
    exp = experience_culture(conn, tick, seed)
    return {
        "exhibited": exhibited,
        "appreciated": exp["appreciated"],
        "acclaimed": exp["acclaimed"],
    }


def list_cultural_commons(conn: sqlite3.Connection,
                          limit: int = 40) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT k.id, k.title, k.kind, k.craft, k.creator_id, k.owner_id,
                  k.venue_id, k.generations, k.appreciations,
                  c.name AS creator_name, o.name AS owner_name,
                  p.name AS venue_name
           FROM keepsakes k
           JOIN agents c ON c.id = k.creator_id
           JOIN agents o ON o.id = k.owner_id
           LEFT JOIN places p ON p.id = k.venue_id
           WHERE k.venue_id IS NOT NULL
           ORDER BY k.appreciations DESC, k.generations DESC, k.id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
