"""Intergenerational mentorship, skill inheritance, and household traditions.

Real small towns pass knowledge and customs across generations:

* **Mentorships & Apprenticeships (`mentorships` table)** — seasoned residents
  (age 40+, senior/manager rank, or respected standing) take younger adults or
  junior coworkers under their wing at shared workplaces, clubs, or
  congregations. Across 5 coaching lessons they warm their bond, boost morale,
  and upon graduation pass their signature `craft` into the apprentice's
  `skills_json` (and upgrade generic knacks), conferring a career wage bump and
  lasting reflection memories for both mentor and protégé.
* **Household Traditions (`households.tradition`)** — each household derives a
  signature family custom from its founding adults' hobbies, skills, faith, and
  temperaments (e.g., Sunday potluck dinners, front-porch acoustic music,
  early-morning lake fishing, kitchen-table chess nights). Every Sunday evening
  housemates celebrate their tradition together, and young adults who move out
  or marry carry their family tradition into new households.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import get_meta
from .events import MINOR, NOTABLE, emit
from .individual import _parse_json_dict, _parse_json_list, clear_cache, profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY, day_of

TARGET_LESSONS = 5
P_DAILY_LESSON = 0.40
GRADUATION_WAGE_BUMP = 0.05

_TRADITION_RULES: list[tuple[set[str], str]] = [
    ({"fishing", "boating", "kayaking", "swimming"}, "early-morning lake fishing"),
    ({"baking", "cooking", "pastry", "bread"}, "Sunday potluck dinners & baking"),
    ({"music", "guitar", "piano", "singing", "choir"}, "front-porch acoustic music"),
    ({"woodworking", "carpentry", "repair", "diy", "crafts"}, "weekend workshop tinkering"),
    ({"reading", "poetry", "writing", "book", "history"}, "fireside book & story trading"),
    ({"chess", "board games", "puzzles", "cards"}, "kitchen-table chess & card nights"),
    ({"gardening", "plants", "botany", "hiking", "birdwatching"}, "Sunday garden tending & nature walks"),
    ({"running", "cycling", "fitness", "sports", "basketball"}, "Sunday park scrimmages"),
    ({"painting", "drawing", "photography", "art", "pottery"}, "weekend studio & craft afternoons"),
]

_FALLBACK_TRADITIONS = (
    "Sunday potluck dinners",
    "kitchen-table card nights",
    "Sunday evening porch sitting",
    "weekend pancake breakfasts",
    "family recipe cooking",
    "Sunday afternoon board games",
)


def _derive_household_tradition(members: list[sqlite3.Row], hid: int,
                                seed: str) -> str:
    r = rng_for(seed, "tradition", hid)
    tokens: set[str] = set()
    for m in members:
        for h in _parse_json_list(m["hobbies_json"]):
            for word in h.lower().split():
                tokens.add(word.strip(",."))
            tokens.add(h.lower())
        for s in _parse_json_list(m["skills_json"]):
            for word in s.lower().split():
                tokens.add(word.strip(",."))
            tokens.add(s.lower())
    for keywords, label in _TRADITION_RULES:
        if tokens & keywords:
            return label
    return r.choice(_FALLBACK_TRADITIONS)


def ensure_traditions(conn: sqlite3.Connection, seed: str = "miniville") -> int:
    """Populate `households.tradition` for any household missing one."""
    missing = conn.execute(
        "SELECT id, name FROM households WHERE tradition IS NULL OR tradition = ''"
    ).fetchall()
    if not missing:
        return 0
    members_by_hh: dict[int, list[sqlite3.Row]] = {}
    for a in conn.execute(
        """SELECT id, household_id, is_child, hobbies_json, skills_json
           FROM agents WHERE alive = 1 AND household_id IS NOT NULL
           ORDER BY is_child ASC, id ASC"""
    ).fetchall():
        members_by_hh.setdefault(int(a["household_id"]), []).append(a)

    updates = []
    for hh in missing:
        hid = int(hh["id"])
        mems = members_by_hh.get(hid, [])
        trad = _derive_household_tradition(mems, hid, seed)
        updates.append((trad, hid))
    if updates:
        conn.executemany("UPDATE households SET tradition = ? WHERE id = ?", updates)
    return len(updates)


def sunday_traditions(conn: sqlite3.Connection, tick: int,
                      seed: str = "miniville") -> int:
    """On Sundays, households with 2+ living members celebrate their tradition."""
    day = day_of(tick)
    if day % 7 != 6:
        return 0
    ensure_traditions(conn, seed)
    rows = conn.execute(
        """SELECT h.id AS hid, h.name AS hname, h.home_place_id, h.tradition,
                  a.id AS aid, a.name AS aname
           FROM households h
           JOIN agents a ON a.household_id = h.id
           WHERE a.alive = 1 AND h.tradition IS NOT NULL
           ORDER BY h.id, a.is_child ASC, a.id ASC"""
    ).fetchall()
    by_hh: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        by_hh.setdefault(int(r["hid"]), []).append(r)

    multi_hhs = [mems for mems in by_hh.values() if len(mems) >= 2]
    if not multi_hhs:
        return 0

    # Bulk boost morale for all multi-member households celebrating Sunday
    celebrating_ids = [int(m["aid"]) for mems in multi_hhs for m in mems]
    if celebrating_ids:
        ph = ",".join("?" * len(celebrating_ids))
        conn.execute(
            f"""UPDATE agent_state
                SET social = MIN(100, social + 4),
                    fun = MIN(100, fun + 4),
                    stress = MAX(0, stress - 3)
                WHERE agent_id IN ({ph})""",
            celebrating_ids,
        )

    # Pick up to 2 spotlight households to record in the ledger + memories
    r = rng_for(seed, "sunday_tradition", day)
    chosen_hhs = r.sample(multi_hhs, min(2, len(multi_hhs)))
    emitted = 0
    for mems in chosen_hhs:
        head = mems[0]
        second = mems[1]
        trad = head["tradition"]
        hname = head["hname"]
        lo, hi = min(int(head["aid"]), int(second["aid"])), max(int(head["aid"]), int(second["aid"]))
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,12.0,10.0,0.0,'kin',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 2.5),
                 affinity = MIN(100.0, relationships.affinity + 3.0),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        text = f"gathered the {hname} at home for {trad}"
        emit(conn, tick, "life_event", place_id=head["home_place_id"],
             a=int(head["aid"]), b=int(second["aid"]),
             importance=MINOR, text=text, tag="family_tradition",
             tradition=trad)
        remember(conn, int(head["aid"]), tick,
                 f"We kept up our family tradition of {trad} at home.",
                 kind="personal", importance=3)
        emitted += 1
    return emitted


def pair_mentors(conn: sqlite3.Connection, tick: int,
                 seed: str = "miniville") -> int:
    """Pair seasoned adults with young adults or junior coworkers as mentors."""
    day = day_of(tick)
    r = rng_for(seed, "mentor_pair", day)

    mentors = conn.execute(
        """SELECT a.id, a.name, a.age, a.work_place_id, a.household_id,
                  COALESCE(a.standing, 0) AS standing,
                  COALESCE(j.rank, 0) AS job_rank
           FROM agents a
           LEFT JOIN jobs j ON j.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (a.age >= 40 OR COALESCE(j.rank, 0) >= 1 OR COALESCE(a.standing, 0) >= 8)
             AND NOT EXISTS (
                 SELECT 1 FROM mentorships m
                 WHERE m.mentor_id = a.id AND m.status = 'active'
             )
           ORDER BY COALESCE(j.rank, 0) DESC, COALESCE(a.standing, 0) DESC, a.id ASC"""
    ).fetchall()
    if not mentors:
        return 0

    apprentices = conn.execute(
        """SELECT a.id, a.name, a.age, a.work_place_id, a.household_id,
                  COALESCE(j.rank, 0) AS job_rank
           FROM agents a
           LEFT JOIN jobs j ON j.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND a.age BETWEEN 18 AND 35
             AND COALESCE(j.rank, 0) <= 1
             AND NOT EXISTS (
                 SELECT 1 FROM mentorships m WHERE m.apprentice_id = a.id
             )
           ORDER BY a.age ASC, a.id ASC"""
    ).fetchall()
    if not apprentices:
        return 0

    # Preload group memberships for shared club/congregation matching
    groups_by_agent: dict[int, set[int]] = {}
    group_venue: dict[int, int | None] = {
        int(g["id"]): g["venue_id"]
        for g in conn.execute("SELECT id, venue_id FROM groups").fetchall()
    }
    for m in conn.execute("SELECT agent_id, group_id FROM memberships").fetchall():
        groups_by_agent.setdefault(int(m["agent_id"]), set()).add(int(m["group_id"]))

    place_names = {
        int(p["id"]): p["name"]
        for p in conn.execute("SELECT id, name FROM places").fetchall()
    }

    mentor_ambitious = {
        int(row["agent_id"])
        for row in conn.execute(
            "SELECT agent_id FROM ambitions WHERE status='active' AND kind='mentor_a_protege'"
        ).fetchall()
    }

    # Sort mentors so those with `mentor_a_protege` ambition match first
    mentor_list = sorted(
        mentors,
        key=lambda m: (0 if int(m["id"]) in mentor_ambitious else 1, -int(m["job_rank"]), int(m["id"])),
    )

    used_apprentices: set[int] = set()
    paired = 0
    max_new = max(3, len(apprentices) // 12)

    for mentor in mentor_list:
        if paired >= max_new:
            break
        mid = int(mentor["id"])
        m_work = mentor["work_place_id"]
        m_groups = groups_by_agent.get(mid, set())
        m_prof = profile_of(conn, mid, seed)
        craft = str(m_prof.get("knack") or "their trade")

        best_app = None
        best_score = -1
        best_venue = m_work

        for app in apprentices:
            aid = int(app["id"])
            if aid == mid or aid in used_apprentices:
                continue
            if int(app["age"] or 20) + 6 > int(mentor["age"] or 45) and int(mentor["job_rank"]) <= int(app["job_rank"]):
                continue
            score = 0
            venue_id = m_work or app["work_place_id"]
            if m_work and app["work_place_id"] == m_work:
                score += 6
                venue_id = m_work
            shared_g = m_groups & groups_by_agent.get(aid, set())
            if shared_g:
                score += 4
                g_id = sorted(shared_g)[0]
                venue_id = venue_id or group_venue.get(g_id)
            if mentor["household_id"] and app["household_id"] == mentor["household_id"]:
                score += 3
            if score > best_score:
                best_score = score
                best_app = app
                best_venue = venue_id

        if best_app is None or best_score <= 0:
            continue

        aid = int(best_app["id"])
        used_apprentices.add(aid)
        conn.execute(
            """INSERT OR IGNORE INTO mentorships(
                   apprentice_id, mentor_id, craft, venue_id,
                   started_tick, lessons, target_lessons, status)
               VALUES(?, ?, ?, ?, ?, 0, ?, 'active')""",
            (aid, mid, craft, best_venue, tick, TARGET_LESSONS),
        )
        lo, hi = min(aid, mid), max(aid, mid)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,8.0,10.0,0.0,'friend',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 5.0),
                 affinity = MIN(100.0, relationships.affinity + 6.0),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        vname = place_names.get(int(best_venue), "town") if best_venue else "town"
        emit(conn, tick, "life_event", place_id=best_venue, a=mid, b=aid,
             importance=MINOR,
             text=f"took {best_app['name']} under their wing in {craft} at {vname}",
             tag="mentorship_started", craft=craft)
        remember(conn, mid, tick,
                 f"I started mentoring {best_app['name']} in {craft}.",
                 kind="personal", importance=3)
        remember(conn, aid, tick,
                 f"{mentor['name']} agreed to mentor me in {craft}.",
                 kind="personal", importance=3)
        paired += 1

    return paired


def _inherit_skill(conn: sqlite3.Connection, apprentice_id: int,
                   craft: str, seed: str) -> None:
    """Add mastered `craft` to the apprentice's `skills_json` and update knack."""
    row = conn.execute(
        "SELECT skills_json, traits_json FROM agents WHERE id = ?",
        (apprentice_id,),
    ).fetchone()
    if not row:
        return
    skills = _parse_json_list(row["skills_json"])
    if craft.lower() not in {s.lower() for s in skills}:
        skills.insert(0, craft)
    traits = _parse_json_dict(row["traits_json"])
    if traits.get("knack") in (None, "", "helping out", "storytelling"):
        traits["knack"] = craft
    conn.execute(
        "UPDATE agents SET skills_json = ?, traits_json = ? WHERE id = ?",
        (json.dumps(skills), json.dumps(traits), apprentice_id),
    )
    clear_cache(conn)
    # Give employed apprentices a small mastery wage bump
    job = conn.execute(
        "SELECT wage_cents, base_wage_cents FROM jobs WHERE agent_id = ?",
        (apprentice_id,),
    ).fetchone()
    if job:
        base = int(job["base_wage_cents"] or job["wage_cents"])
        ceiling = int(base * 1.75)
        new_wage = min(ceiling, int(job["wage_cents"] * (1.0 + GRADUATION_WAGE_BUMP)))
        if new_wage > int(job["wage_cents"]):
            conn.execute(
                "UPDATE jobs SET wage_cents = ? WHERE agent_id = ?",
                (new_wage, apprentice_id),
            )


def step_mentorships(conn: sqlite3.Connection, tick: int,
                     seed: str = "miniville") -> dict[str, int]:
    """Advance active mentorships; graduate apprentices who reach target_lessons."""
    day = day_of(tick)
    rows = conn.execute(
        """SELECT m.*, ma.name AS mentor_name, ma.alive AS mentor_alive,
                  aa.name AS app_name, aa.alive AS app_alive
           FROM mentorships m
           JOIN agents ma ON ma.id = m.mentor_id
           JOIN agents aa ON aa.id = m.apprentice_id
           WHERE m.status = 'active'
           ORDER BY m.apprentice_id"""
    ).fetchall()
    if not rows:
        return {"lessons": 0, "graduated": 0}

    lessons_n = graduated_n = 0
    for m in rows:
        aid = int(m["apprentice_id"])
        mid = int(m["mentor_id"])
        if not m["mentor_alive"] or not m["app_alive"]:
            conn.execute(
                "DELETE FROM mentorships WHERE apprentice_id = ?", (aid,)
            )
            continue
        r = rng_for(seed, "mentor_step", aid, mid, day)
        if r.random() >= P_DAILY_LESSON:
            continue

        new_lessons = int(m["lessons"] or 0) + 1
        target = int(m["target_lessons"] or TARGET_LESSONS)
        craft = m["craft"]
        lo, hi = min(aid, mid), max(aid, mid)
        conn.execute(
            """UPDATE relationships
               SET familiarity = MIN(100.0, familiarity + 2.5),
                   affinity = MIN(100.0, affinity + 3.0),
                   last_met_tick = ?
               WHERE a_id = ? AND b_id = ?""",
            (tick, lo, hi),
        )
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100, fun + 4), stress = MAX(0, stress - 3)
               WHERE agent_id IN (?, ?)""",
            (aid, mid),
        )
        lessons_n += 1

        if new_lessons >= target:
            conn.execute(
                """UPDATE mentorships
                   SET lessons = ?, status = 'graduated', graduated_tick = ?
                   WHERE apprentice_id = ?""",
                (target, tick, aid),
            )
            _inherit_skill(conn, aid, craft, seed)
            emit(conn, tick, "life_event", place_id=m["venue_id"], a=aid, b=mid,
                 importance=NOTABLE,
                 text=f"completed their apprenticeship in {craft} under {m['mentor_name']}",
                 tag="apprenticeship_completed", craft=craft)
            remember(conn, aid, tick,
                     f"I mastered {craft} thanks to {m['mentor_name']}'s mentorship!",
                     kind="reflection", importance=4)
            remember(conn, mid, tick,
                     f"{m['app_name']} graduated from my mentorship in {craft}!",
                     kind="reflection", importance=4)
            graduated_n += 1
        else:
            conn.execute(
                "UPDATE mentorships SET lessons = ? WHERE apprentice_id = ?",
                (new_lessons, aid),
            )

    return {"lessons": lessons_n, "graduated": graduated_n}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    """Daily mentorship progression, weekly pairing, and Sunday traditions."""
    ensure_traditions(conn, seed)
    day = day_of(tick)
    paired = 0
    if day % 7 == 0:
        paired = pair_mentors(conn, tick, seed)
    prog = step_mentorships(conn, tick, seed)
    traditions = sunday_traditions(conn, tick, seed)
    return {
        "paired": paired,
        "lessons": prog["lessons"],
        "graduated": prog["graduated"],
        "traditions": traditions,
    }


def mentorship_for_resident(conn: sqlite3.Connection,
                            agent_id: int) -> dict[str, Any]:
    """Return mentor, apprentices, and household tradition for `agent_id`."""
    mentor_row = conn.execute(
        """SELECT m.mentor_id AS id, a.name, m.craft, m.lessons,
                  m.target_lessons, m.status, p.name AS venue
           FROM mentorships m
           JOIN agents a ON a.id = m.mentor_id
           LEFT JOIN places p ON p.id = m.venue_id
           WHERE m.apprentice_id = ?""",
        (agent_id,),
    ).fetchone()
    apprentice_rows = conn.execute(
        """SELECT m.apprentice_id AS id, a.name, m.craft, m.lessons,
                  m.target_lessons, m.status, p.name AS venue
           FROM mentorships m
           JOIN agents a ON a.id = m.apprentice_id
           LEFT JOIN places p ON p.id = m.venue_id
           WHERE m.mentor_id = ?
           ORDER BY m.status ASC, m.lessons DESC""",
        (agent_id,),
    ).fetchall()
    hh_row = conn.execute(
        """SELECT h.name, h.tradition FROM agents a
           JOIN households h ON h.id = a.household_id
           WHERE a.id = ?""",
        (agent_id,),
    ).fetchone()
    return {
        "mentor": dict(mentor_row) if mentor_row else None,
        "apprentices": [dict(r) for r in apprentice_rows],
        "household_tradition": hh_row["tradition"] if hh_row and hh_row["tradition"] else None,
    }


def list_mentorships(conn: sqlite3.Connection, status: str = "all",
                     limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT m.*, ma.name AS mentor_name, aa.name AS apprentice_name,
                  p.name AS venue_name
           FROM mentorships m
           JOIN agents ma ON ma.id = m.mentor_id
           JOIN agents aa ON aa.id = m.apprentice_id
           LEFT JOIN places p ON p.id = m.venue_id
           WHERE ma.alive = 1 AND aa.alive = 1
             AND (? = 'all' OR m.status = ?)
           ORDER BY m.status ASC, m.lessons DESC, m.apprentice_id ASC
           LIMIT ?""",
        (status, status, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def tradition_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_traditions(conn, get_meta(conn, "seed", "miniville") or "miniville")
    rows = conn.execute(
        """SELECT h.tradition, COUNT(DISTINCT h.id) AS households,
                  COUNT(a.id) AS residents
           FROM households h
           JOIN agents a ON a.household_id = h.id AND a.alive = 1
           WHERE h.tradition IS NOT NULL AND h.tradition != ''
           GROUP BY h.tradition
           ORDER BY households DESC, residents DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
