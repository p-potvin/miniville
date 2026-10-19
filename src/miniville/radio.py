"""Town Radio (WMNV 94.3 FM) & Oral History Broadcasts (v0.32).

Every small town has a community radio voice that ties neighborhoods together:

* **WMNV 94.3 FM (`radio_broadcasts` table)** — broadcast from the studio at
  `Miniville Community Center` (or `Miniville Gazette`), hosted by local
  journalists, musicians, and gregarious residents.
* **Program Rotation (`air_broadcast`)** — every two days, WMNV airs one of
  three signature programs:
  1. `oral_history` (*Voices of Miniville*) — interviews an elder (age 60+) or
     high-standing pillar about their signature knack, family tradition, or
     hometown roots, warming host–guest bonds and awarding +1 standing.
  2. `live_music` (*Front-Porch Sessions*) — features a musical resident playing
     live on-air, lifting town-wide morale (+2 fun, -1 stress for listeners).
  3. `main_street_spotlight` (*Main Street Almanac*) — spotlights an open local
     business and its staff, boosting morale.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, emit
from .individual import profile_of
from .memory import remember
from .rng import rng_for
from .seasons import season_of
from .timekeeper import day_of


def air_broadcast(conn: sqlite3.Connection, tick: int,
                  seed: str = "miniville") -> dict[str, Any] | None:
    """Air a WMNV 94.3 FM community radio program."""
    day = day_of(tick)
    if day % 2 != 1:
        return None

    already = conn.execute(
        "SELECT 1 FROM radio_broadcasts WHERE day = ? LIMIT 1", (day,)
    ).fetchone()
    if already:
        return None

    r = rng_for(seed, "wmnv_radio", day)
    studio = conn.execute(
        """SELECT id, name FROM places
           WHERE name IN ('Miniville Community Center', 'Miniville Gazette')
           ORDER BY id ASC LIMIT 1"""
    ).fetchone()
    studio_id = int(studio["id"]) if studio else None

    hosts = conn.execute(
        """SELECT id, name, occupation FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY COALESCE(standing, 0) DESC, id ASC LIMIT 35"""
    ).fetchall()
    if not hosts:
        return None

    host = r.choice(hosts)
    hid = int(host["id"])
    season = season_of(day)

    show_kind = r.choice(("oral_history", "live_music", "main_street_spotlight"))
    guest_id = None
    guest_name = None

    if show_kind == "oral_history":
        elders = conn.execute(
            """SELECT a.id, a.name, a.age, a.occupation, h.tradition
               FROM agents a
               LEFT JOIN households h ON h.id = a.household_id
               WHERE a.alive = 1 AND a.is_child = 0 AND a.id != ?
                 AND (a.age >= 55 OR COALESCE(a.standing, 0) >= 8)
               ORDER BY a.age DESC, COALESCE(a.standing, 0) DESC, a.id ASC
               LIMIT 25""",
            (hid,),
        ).fetchall()
        if elders:
            g = r.choice(elders)
            guest_id = int(g["id"])
            guest_name = g["name"]
            prof = profile_of(conn, guest_id, seed)
            knack = prof.get("knack", "their craft")
            trad = g["tradition"] or "Sunday porch gatherings"
            title = f"Voices of Miniville: {guest_name} on {knack.title()}"
            transcript = (
                f"On WMNV 94.3 FM, host {host['name']} interviewed {guest_name} "
                f"(age {g['age']}) about a lifetime of {knack} and keeping up "
                f"{trad} through the {season} months."
            )
            conn.execute(
                "UPDATE agents SET standing = COALESCE(standing, 0) + 1 WHERE id = ?",
                (guest_id,),
            )
        else:
            show_kind = "main_street_spotlight"

    if show_kind == "live_music":
        musicians = conn.execute(
            """SELECT id, name, hobbies_json, skills_json FROM agents
               WHERE alive = 1 AND is_child = 0 AND id != ?
                 AND (hobbies_json LIKE '%music%' OR hobbies_json LIKE '%guitar%'
                      OR hobbies_json LIKE '%sing%' OR skills_json LIKE '%music%')
               ORDER BY id ASC LIMIT 25""",
            (hid,),
        ).fetchall()
        if not musicians:
            musicians = [h for h in hosts if int(h["id"]) != hid]
        if musicians:
            g = r.choice(musicians)
            guest_id = int(g["id"])
            guest_name = g["name"]
            title = f"WMNV Front-Porch Acoustic Session with {guest_name}"
            transcript = (
                f"{guest_name} joined {host['name']} in the WMNV 94.3 FM studio "
                f"for an acoustic {season} evening set that had radios glowing across town."
            )
            listeners = conn.execute(
                "SELECT id FROM agents WHERE alive = 1 ORDER BY id LIMIT 40"
            ).fetchall()
            lids = [int(x["id"]) for x in listeners]
            if lids:
                ph = ",".join("?" * len(lids))
                conn.execute(
                    f"""UPDATE agent_state
                        SET fun = MIN(100, fun + 2), stress = MAX(0, stress - 1)
                        WHERE agent_id IN ({ph})""",
                    lids,
                )
        else:
            show_kind = "main_street_spotlight"

    if show_kind == "main_street_spotlight":
        biz = conn.execute(
            """SELECT p.id, p.name, p.district FROM businesses b
               JOIN places p ON p.id = b.place_id
               WHERE b.status = 'open'
               ORDER BY b.traffic_today DESC, p.id ASC LIMIT 12"""
        ).fetchall()
        vname = r.choice(biz)["name"] if biz else "Main Street"
        title = f"Main Street Almanac: {vname} Spotlight"
        transcript = (
            f"Host {host['name']} signed on WMNV 94.3 FM with the {season} "
            f"weather almanac and a spotlight on the crew at {vname}."
        )

    conn.execute(
        """INSERT INTO radio_broadcasts(
               day, tick, host_id, guest_id, show_kind, title, transcript)
           VALUES(?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, hid, guest_id, show_kind, title, transcript),
    )

    if guest_id:
        lo, hi = min(hid, guest_id), max(hid, guest_id)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,6.0,7.0,0.0,'acquaintance',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.5),
                 affinity = MIN(100.0, relationships.affinity + 4.5),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        remember(conn, guest_id, tick,
                 f"I was interviewed by {host['name']} on WMNV 94.3 FM ({title})!",
                 kind="personal", importance=4)

    remember(conn, hid, tick,
             f"Hosted '{title}' on WMNV 94.3 FM community radio.",
             kind="personal", importance=3)
    emit(conn, tick, "life_event", place_id=studio_id, a=hid, b=guest_id,
         importance=NOTABLE if show_kind == "oral_history" else MINOR,
         text=f"hosted '{title}' on WMNV 94.3 FM",
         tag="radio_broadcast", show_kind=show_kind, title=title)
    return {"day": day, "show_kind": show_kind, "title": title}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    res = air_broadcast(conn, tick, seed)
    return {"broadcasts": 1 if res else 0}


def list_broadcasts(conn: sqlite3.Connection, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.*, ha.name AS host_name, ga.name AS guest_name
           FROM radio_broadcasts r
           LEFT JOIN agents ha ON ha.id = r.host_id
           LEFT JOIN agents ga ON ga.id = r.guest_id
           ORDER BY r.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(x) for x in rows]


def radio_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) n FROM radio_broadcasts").fetchone()["n"]
    oral = conn.execute(
        "SELECT COUNT(*) n FROM radio_broadcasts WHERE show_kind = 'oral_history'"
    ).fetchone()["n"]
    music = conn.execute(
        "SELECT COUNT(*) n FROM radio_broadcasts WHERE show_kind = 'live_music'"
    ).fetchone()["n"]
    return {
        "total_broadcasts": int(total),
        "oral_histories": int(oral),
        "live_music_sessions": int(music),
        "recent": list_broadcasts(conn, limit=15),
    }
