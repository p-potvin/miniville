"""Out-of-town roots, hometown correspondence, care packages, and personal diaries.

One of Miniville's founding design principles (`docs/STATE.md`) is that
every adult resident is a transplant whose `origin_city` and `origin_state`
come from the Nemotron-Personas-USA dataset. Until now, those origin fields sat
unused after `ingest.py`. This module brings a resident's roots and inner voice
into the living simulation with zero LLM overhead:

* **Hometown Letters & Care Packages (`letters_and_packages`)** — lonely or
  reflective residents write letters back to family/friends in `origin_city,
  origin_state`, easing loneliness (`+10` social, `-8` stress) and recording a
  first-person entry in `diary_entries`. Occasionally a household receives a
  hometown care package (`care_package` life event), lifting morale across the
  household.
* **Shared-Roots Kinship (`bond_over_origins`)** — transplants who hail from
  the same `origin_state` discover their shared roots (`shared_roots` event),
  warming familiarity (`+5.0`) and affinity (`+6.0`).
* **First-Person Biographical Diaries (`write_milestone_diaries`)** — when a
  resident experiences a major personal milestone (marriage, ambition
  fulfilled, apprenticeship graduation, heirloom inheritance, acclaimed work,
  retirement send-off, or competition win), they pen a deterministic,
  temperament-voiced first-person diary entry in `diary_entries`.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import get_meta
from .events import MINOR, describe, emit
from .individual import profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

P_CARE_PACKAGE = 0.25
P_SHARED_ROOTS = 0.45

_DIARY_MILESTONE_TAGS = {
    "ambition_achieved",
    "apprenticeship_completed",
    "heirloom_inherited",
    "acclaimed_work",
    "retirement_sendoff",
    "milestone_birthday",
    "wedding_anniversary",
    "competition_won",
    "marriage",
    "promoted",
}


def _origin_label(city: str | None, state: str | None) -> str:
    if city and state:
        return f"{city}, {state}"
    return city or state or "back home"


def letters_and_packages(conn: sqlite3.Connection, tick: int,
                         seed: str = "miniville") -> dict[str, int]:
    """Lonely/reflective residents write letters home; some receive care packages."""
    day = day_of(tick)
    r = rng_for(seed, "letters", day)
    letters = packages = 0

    lonely = conn.execute(
        """SELECT a.id, a.name, a.origin_city, a.origin_state,
                  a.home_place_id, a.household_id, s.mood
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (s.mood IN ('lonely', 'miserable') OR s.social < 42)
             AND (a.origin_city IS NOT NULL OR a.origin_state IS NOT NULL)
           ORDER BY s.social ASC, a.id ASC LIMIT 4"""
    ).fetchall()

    for row in lonely:
        if r.random() >= 0.45:
            continue
        aid = int(row["id"])
        origin = _origin_label(row["origin_city"], row["origin_state"])
        prof = profile_of(conn, aid, seed)
        arch = prof.get("archetype", "resident")
        entry = (
            f"Dear folks in {origin} — it felt quiet here in Miniville today, "
            f"so as a {arch} I brewed a cup of tea and wrote to catch you up on life here."
        )
        conn.execute(
            """INSERT INTO diary_entries(agent_id, tick, day, kind, origin, entry)
               VALUES(?, ?, ?, 'letter_home', ?, ?)""",
            (aid, tick, day, origin, entry),
        )
        conn.execute(
            """UPDATE agent_state
               SET social = MIN(100, social + 10), stress = MAX(0, stress - 8)
               WHERE agent_id = ?""",
            (aid,),
        )
        emit(conn, tick, "life_event", place_id=row["home_place_id"], a=aid,
             importance=MINOR,
             text=f"wrote a long letter home to {origin}",
             tag="letter_home", origin=origin)
        letters += 1
        break

    if r.random() < P_CARE_PACKAGE:
        transplants = conn.execute(
            """SELECT id, name, origin_city, origin_state, home_place_id, household_id
               FROM agents
               WHERE alive = 1 AND is_child = 0
                 AND (origin_city IS NOT NULL OR origin_state IS NOT NULL)
               ORDER BY id"""
        ).fetchall()
        if transplants:
            rec = r.choice(transplants)
            aid = int(rec["id"])
            origin = _origin_label(rec["origin_city"], rec["origin_state"])
            treats = r.choice([
                "hometown preserves and family postcards",
                "regional coffee and handwritten letters",
                "favourite childhood snacks and family photos",
                "spices and a tin of homemade cookies",
            ])
            entry = f"A care package arrived from {origin} today — {treats}! Shared it around the kitchen table."
            conn.execute(
                """INSERT INTO diary_entries(agent_id, tick, day, kind, origin, entry)
                   VALUES(?, ?, ?, 'care_package', ?, ?)""",
                (aid, tick, day, origin, entry),
            )
            if rec["household_id"] is not None:
                conn.execute(
                    """UPDATE agent_state
                       SET fun = MIN(100, fun + 10), hunger = MIN(100, hunger + 8),
                           stress = MAX(0, stress - 6)
                       WHERE agent_id IN (
                           SELECT id FROM agents WHERE household_id = ? AND alive = 1
                       )""",
                    (rec["household_id"],),
                )
            else:
                conn.execute(
                    """UPDATE agent_state
                       SET fun = MIN(100, fun + 10), hunger = MIN(100, hunger + 8),
                           stress = MAX(0, stress - 6)
                       WHERE agent_id = ?""",
                    (aid,),
                )
            emit(conn, tick, "life_event", place_id=rec["home_place_id"], a=aid,
                 importance=MINOR,
                 text=f"received a care package of {treats} from {origin}",
                 tag="care_package", origin=origin)
            remember(conn, aid, tick,
                     f"Received a care package of {treats} from {origin}.",
                     kind="personal", importance=3)
            packages += 1

    return {"letters": letters, "care_packages": packages}


def bond_over_origins(conn: sqlite3.Connection, tick: int,
                      seed: str = "miniville") -> int:
    """Transplants from the same home state bond over shared roots."""
    day = day_of(tick)
    r = rng_for(seed, "shared_roots", day)
    if r.random() >= P_SHARED_ROOTS:
        return 0

    rows = conn.execute(
        """SELECT id, name, origin_city, origin_state, home_place_id
           FROM agents
           WHERE alive = 1 AND is_child = 0
             AND origin_state IS NOT NULL AND origin_state != ''
           ORDER BY origin_state, id"""
    ).fetchall()
    by_state: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_state.setdefault(str(row["origin_state"]), []).append(row)

    multi_states = [mems for mems in by_state.values() if len(mems) >= 2]
    if not multi_states:
        return 0

    group = r.choice(multi_states)
    a, b = r.sample(group, 2)
    aid, bid = int(a["id"]), int(b["id"])
    lo, hi = min(aid, bid), max(aid, bid)
    state = a["origin_state"]

    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,8.0,9.0,0.0,'acquaintance',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 5.0),
             affinity = MIN(100.0, relationships.affinity + 6.0),
             interactions = relationships.interactions + 1,
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )
    conn.execute(
        """UPDATE agent_state
           SET social = MIN(100, social + 7), fun = MIN(100, fun + 5)
           WHERE agent_id IN (?, ?)""",
        (aid, bid),
    )
    emit(conn, tick, "life_event", place_id=a["home_place_id"], a=aid, b=bid,
         importance=MINOR,
         text=f"bonded with {b['name']} over both hailing from {state}",
         tag="shared_roots", origin_state=state)
    conn.execute(
        """INSERT INTO diary_entries(agent_id, tick, day, kind, origin, entry)
           VALUES(?, ?, ?, 'shared_roots', ?, ?)""",
        (aid, tick, day, state,
         f"Ran into {b['name']} and realized we both grew up in {state} — traded stories about home for an hour."),
    )
    return 1


def write_milestone_diaries(conn: sqlite3.Connection, tick: int,
                            seed: str = "miniville") -> int:
    """Record first-person diary entries for notable personal events today."""
    day = day_of(tick)
    evs = conn.execute(
        """SELECT e.*, a.name, a.origin_city, a.origin_state
           FROM events e
           JOIN agents a ON a.id = e.a_id
           WHERE e.day = ? AND e.kind = 'life_event' AND e.importance >= 3
             AND a.alive = 1
           ORDER BY e.id DESC LIMIT 6""",
        (day,),
    ).fetchall()
    written = 0
    for ev in evs:
        try:
            d = json.loads(ev["data"] or "{}")
        except Exception:
            d = {}
        tag = d.get("tag", "")
        if tag not in _DIARY_MILESTONE_TAGS:
            continue
        aid = int(ev["a_id"])
        # Avoid duplicate diary entry for the same agent + day + tag
        exists = conn.execute(
            "SELECT 1 FROM diary_entries WHERE agent_id = ? AND day = ? AND kind = ?",
            (aid, day, f"milestone:{tag}"),
        ).fetchone()
        if exists:
            continue
        prof = profile_of(conn, aid, seed)
        origin = _origin_label(ev["origin_city"], ev["origin_state"])
        rendered = describe(conn, ev)
        if ":" in rendered:
            rendered = rendered.split(":", 1)[1].strip()
        rendered = rendered.replace(" their ", " my ").replace(" themselves", " myself")
        entry = (
            f"Day {day + 1} in Miniville — today I {rendered}. "
            f"Thinking back to my roots in {origin}, I feel proud of the life I'm building here."
        )
        conn.execute(
            """INSERT INTO diary_entries(agent_id, tick, day, kind, origin, entry)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (aid, tick, day, f"milestone:{tag}", origin, entry),
        )
        written += 1
    return written


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    lp = letters_and_packages(conn, tick, seed)
    roots = bond_over_origins(conn, tick, seed)
    diaries = write_milestone_diaries(conn, tick, seed)
    return {
        "letters": lp["letters"],
        "care_packages": lp["care_packages"],
        "shared_roots": roots,
        "milestone_diaries": diaries,
    }


def diary_of(conn: sqlite3.Connection, agent_id: int,
             limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT d.*, a.name FROM diary_entries d
           JOIN agents a ON a.id = d.agent_id
           WHERE d.agent_id = ?
           ORDER BY d.id DESC LIMIT ?""",
        (agent_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["day"] = int(item["day"]) + 1
        out.append(item)
    return out


def list_diaries(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT d.*, a.name, a.occupation FROM diary_entries d
           JOIN agents a ON a.id = d.agent_id
           WHERE a.alive = 1
           ORDER BY d.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["day"] = int(item["day"]) + 1
        out.append(item)
    return out


def write_diary_entry(conn: sqlite3.Connection, agent_id: int, tick: int,
                      tag: str, entry_text: str) -> int:
    cur = conn.execute(
        """INSERT INTO diary_entries(agent_id, day, tick, kind, entry)
           VALUES(?, ?, ?, ?, ?)""",
        (agent_id, day_of(tick), tick, tag, entry_text),
    )
    return int(cur.lastrowid)
