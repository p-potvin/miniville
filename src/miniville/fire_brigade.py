"""Volunteer Fire Brigade, Safety Inspections & Rebuild Drives (v0.36).

Resilient, civic-minded residents across Miniville's districts staff the
**Miniville Volunteer Fire Brigade** (`fire_brigade` table):

* **District Brigades & Captains (`ensure_brigade`)** — each district recruits
  able-bodied adults (`vitality >= 60` or `resilience >= 0.05`) and elects a
  senior **Fire Captain**.
* **Safety Inspections & Hose Drills (`run_brigade_drills`)** — every 4 days,
  the brigade conducts a hose drill (+3 vitality, +5 social, -3 stress) and
  inspects an open commercial venue (`fire_inspection_passed`).
* **Fire Rebuild Drives (`brigade_rebuild_aid`)** — whenever a venue is closed
  for repairs after a fire or shock (`businesses.status = 'closed'` and
  `reopen_day IS NOT NULL`), the brigade pitches in to clear debris and rebuild,
  moving `reopen_day` 1 day closer!
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_meta
from .events import MINOR, NOTABLE, emit
from .individual import profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of


def ensure_brigade(conn: sqlite3.Connection, tick: int = 0,
                   seed: str = "miniville") -> int:
    """Ensure each district has a Volunteer Fire Brigade roster and captain."""
    existing = {
        int(r["agent_id"])
        for r in conn.execute("SELECT agent_id FROM fire_brigade").fetchall()
    }
    rows = conn.execute(
        """SELECT a.id, a.name, a.age, p.district,
                  COALESCE(s.vitality, 65) AS vitality,
                  COALESCE(a.standing, 0) AS standing
           FROM agents a
           JOIN places p ON p.id = a.home_place_id
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND a.age BETWEEN 18 AND 62
             AND p.district IS NOT NULL AND p.district != ''
           ORDER BY COALESCE(s.vitality, 65) DESC, COALESCE(a.standing, 0) DESC, a.id ASC"""
    ).fetchall()

    by_dist: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_dist.setdefault(r["district"], []).append(r)

    added = 0
    for dist, candidates in sorted(by_dist.items()):
        cur_dist = conn.execute(
            "SELECT COUNT(*) n FROM fire_brigade WHERE district = ?", (dist,)
        ).fetchone()["n"]
        need = max(0, 3 - int(cur_dist))
        for c in candidates:
            if need <= 0:
                break
            aid = int(c["id"])
            if aid in existing:
                continue
            prof = profile_of(conn, aid, seed)
            if float(prof.get("resilience", 0.0)) < -0.2 and int(c["vitality"]) < 60:
                continue
            role = "captain" if int(cur_dist) == 0 and added == 0 else "volunteer"
            conn.execute(
                """INSERT OR IGNORE INTO fire_brigade(
                       agent_id, district, role, drills_completed, rescues_count, joined_tick)
                   VALUES(?, ?, ?, 0, 0, ?)""",
                (aid, dist, role, tick),
            )
            existing.add(aid)
            cur_dist += 1
            need -= 1
            added += 1

        # Ensure every district with members has a captain
        cap = conn.execute(
            """SELECT fb.agent_id FROM fire_brigade fb
               JOIN agents a ON a.id = fb.agent_id
               WHERE fb.district = ? AND a.alive = 1
               ORDER BY (CASE WHEN fb.role = 'captain' THEN 0 ELSE 1 END),
                        fb.drills_completed DESC, fb.agent_id ASC LIMIT 1""",
            (dist,),
        ).fetchone()
        if cap:
            conn.execute(
                "UPDATE fire_brigade SET role = 'volunteer' WHERE district = ?",
                (dist,),
            )
            conn.execute(
                "UPDATE fire_brigade SET role = 'captain' WHERE agent_id = ?",
                (int(cap["agent_id"]),),
            )

    return added


def run_brigade_drills(conn: sqlite3.Connection, tick: int,
                       seed: str = "miniville") -> dict[str, int]:
    """Run brigade drills, safety inspections, and post-fire rebuild drives."""
    day = day_of(tick)
    recruited = ensure_brigade(conn, tick, seed)
    if day % 4 != 2:
        return {"recruited": recruited, "drills": 0, "rebuilds": 0}

    members = conn.execute(
        """SELECT fb.*, a.name FROM fire_brigade fb
           JOIN agents a ON a.id = fb.agent_id
           WHERE a.alive = 1
           ORDER BY (CASE WHEN fb.role = 'captain' THEN 0 ELSE 1 END), fb.agent_id ASC"""
    ).fetchall()
    if not members:
        return {"recruited": recruited, "drills": 0, "rebuilds": 0}

    r = rng_for(seed, "fire_brigade", day)
    mids = [int(m["agent_id"]) for m in members]
    ph = ",".join("?" * len(mids))
    conn.execute(
        f"""UPDATE agent_state
            SET vitality = MIN(100, COALESCE(vitality, 65) + 3),
                social = MIN(100, social + 4),
                stress = MAX(0, stress - 3)
            WHERE agent_id IN ({ph})""",
        mids,
    )
    conn.execute(
        f"UPDATE fire_brigade SET drills_completed = drills_completed + 1 WHERE agent_id IN ({ph})",
        mids,
    )

    lead = r.choice(members)
    lid = int(lead["agent_id"])
    rebuilds = 0

    # Check if any closed venue needs post-fire / post-shock rebuild assistance
    closed_biz = conn.execute(
        """SELECT b.place_id, b.reopen_day, p.name, p.district
           FROM businesses b
           JOIN places p ON p.id = b.place_id
           WHERE b.status = 'closed' AND b.reopen_day IS NOT NULL AND b.reopen_day > ?
           ORDER BY b.reopen_day ASC LIMIT 1""",
        (day + 1,),
    ).fetchone()
    if closed_biz:
        pid = int(closed_biz["place_id"])
        new_reopen = max(day + 1, int(closed_biz["reopen_day"]) - 1)
        conn.execute(
            "UPDATE businesses SET reopen_day = ? WHERE place_id = ?",
            (new_reopen, pid),
        )
        conn.execute(
            "UPDATE fire_brigade SET rescues_count = rescues_count + 1 WHERE agent_id = ?",
            (lid,),
        )
        emit(conn, tick, "life_event", place_id=pid, a=lid, importance=NOTABLE,
             text=f"led the Volunteer Fire Brigade rebuild crew at {closed_biz['name']}, speeding its reopening to Day {new_reopen + 1}",
             tag="brigade_rebuild_aid", venue=closed_biz["name"])
        remember(conn, lid, tick,
                 f"Our Fire Brigade crew helped rebuild {closed_biz['name']} ahead of schedule!",
                 kind="personal", importance=4)
        rebuilds += 1
    else:
        open_biz = conn.execute(
            """SELECT p.id, p.name FROM businesses b
               JOIN places p ON p.id = b.place_id
               WHERE b.status = 'open' ORDER BY p.id ASC LIMIT 15"""
        ).fetchall()
        venue = r.choice(open_biz) if open_biz else None
        vid = int(venue["id"]) if venue else None
        vname = venue["name"] if venue else "Town Hall"
        emit(conn, tick, "life_event", place_id=vid, a=lid, importance=MINOR,
             text=f"led the {lead['district']} Volunteer Fire Brigade hose drill and safety inspection at {vname}",
             tag="fire_drill", district=lead["district"])
        remember(conn, lid, tick,
                 f"Completed a brigade hose drill and safety check at {vname}.",
                 kind="personal", importance=2)

    return {"recruited": recruited, "drills": 1, "rebuilds": rebuilds}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    return run_brigade_drills(conn, tick, seed)


def brigade_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_brigade(conn, int(get_meta(conn, "tick", "0") or 0),
                   get_meta(conn, "seed", "miniville") or "miniville")
    rows = conn.execute(
        """SELECT fb.*, a.name, COALESCE(s.vitality, 65) AS vitality
           FROM fire_brigade fb
           JOIN agents a ON a.id = fb.agent_id
           LEFT JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1
           ORDER BY (CASE WHEN fb.role = 'captain' THEN 0 ELSE 1 END),
                    fb.drills_completed DESC, fb.agent_id ASC"""
    ).fetchall()
    items = [dict(r) for r in rows]
    return {
        "total_volunteers": len(items),
        "captains": sum(1 for x in items if x["role"] == "captain"),
        "total_drills": sum(int(x["drills_completed"] or 0) for x in items),
        "total_rebuilds": sum(int(x["rescues_count"] or 0) for x in items),
        "roster": items[:20],
    }
