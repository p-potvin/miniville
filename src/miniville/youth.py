"""School mentors, youth skill discovery, and civic scholarships (v0.30).

Growing up in Miniville shapes who children become at 18:

* **School Discovery (`school_discoveries`)** — on school days, teachers and
  staff at `Miniville School` inspire students (`is_child = 1, age 8..17`) to
  discover new hobbies and practical skills (`hobbies_json` and `skills_json`),
  recording a warm bond with their teacher and emitting `youth_discovery`.
* **Miniville Civic Scholarships (`award_scholarships`)** — when a student who
  has cultivated 2+ skills/hobbies comes of age at 18 (`coming_of_age`), if
  `town_account.balance_cents >= 200_000`, the town awards a strictly conserved
  `$100` (`10_000` cents) **Miniville Civic Scholarship** from `town_account`
  into the graduate's wallet (`civic_scholarship`), upgrading their
  `education_level` to `'bachelors'` and refreshing their temperament profile.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, emit
from .individual import _parse_json_list, clear_cache, derive_profile
from .memory import remember
from .rng import rng_for
from .seasons import school_in_session
from .timekeeper import day_of

SCHOLARSHIP_CENTS = 10_000         # $100 conserved scholarship from town_account
TOWN_SCHOLARSHIP_FLOOR = 200_000   # town_account must hold >= $2,000

_YOUTH_DISCOVERIES: tuple[tuple[str, str], ...] = (
    ("woodworking", "carpentry"),
    ("baking", "pastry baking"),
    ("music", "guitar"),
    ("painting", "illustration"),
    ("chess", "strategic planning"),
    ("gardening", "botany"),
    ("reading", "creative writing"),
    ("photography", "darkroom photography"),
    ("running", "athletic coaching"),
    ("science", "electronics repair"),
)


def school_discoveries(conn: sqlite3.Connection, tick: int,
                       seed: str = "miniville") -> int:
    """On school days, teachers spark new hobbies and skills in school-age youth."""
    day = day_of(tick)
    if not school_in_session(day):
        return 0
    r = rng_for(seed, "youth_school", day)
    if r.random() >= 0.45:
        return 0

    school = conn.execute(
        "SELECT id, name FROM places WHERE name = 'Miniville School' LIMIT 1"
    ).fetchone()
    school_id = int(school["id"]) if school else None

    students = conn.execute(
        """SELECT id, name, age, hobbies_json, skills_json
           FROM agents
           WHERE alive = 1 AND is_child = 1 AND age BETWEEN 8 AND 17
           ORDER BY id"""
    ).fetchall()
    if not students:
        return 0

    teachers = []
    if school_id:
        teachers = conn.execute(
            """SELECT a.id, a.name FROM agents a
               JOIN jobs j ON j.agent_id = a.id
               WHERE a.alive = 1 AND j.place_id = ?
               ORDER BY j.rank DESC, a.id ASC""",
            (school_id,),
        ).fetchall()

    student = r.choice(students)
    sid = int(student["id"])
    hobby, skill = r.choice(_YOUTH_DISCOVERIES)
    hobbies = _parse_json_list(student["hobbies_json"])
    skills = _parse_json_list(student["skills_json"])
    if hobby not in [h.lower() for h in hobbies]:
        hobbies.append(hobby)
    if skill not in [s.lower() for s in skills]:
        skills.insert(0, skill)

    conn.execute(
        "UPDATE agents SET hobbies_json = ?, skills_json = ? WHERE id = ?",
        (json.dumps(hobbies), json.dumps(skills), sid),
    )
    conn.execute(
        "UPDATE agent_state SET fun = MIN(100, fun + 8), social = MIN(100, social + 5) WHERE agent_id = ?",
        (sid,),
    )
    clear_cache(conn)

    teacher_id = None
    teacher_txt = "in class at Miniville School"
    if teachers:
        t = r.choice(teachers)
        teacher_id = int(t["id"])
        teacher_txt = f"with teacher {t['name']} at Miniville School"
        lo, hi = min(sid, teacher_id), max(sid, teacher_id)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,8.0,10.0,0.0,'friend',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.5),
                 affinity = MIN(100.0, relationships.affinity + 4.0),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )

    text = f"discovered a passion for {hobby} and {skill} {teacher_txt}"
    emit(conn, tick, "life_event", place_id=school_id, a=sid, b=teacher_id,
         importance=MINOR, text=text, tag="youth_discovery",
         hobby=hobby, skill=skill)
    remember(conn, sid, tick,
             f"I discovered a love for {hobby} and {skill} {teacher_txt}!",
             kind="personal", importance=3)
    return 1


def award_scholarships(conn: sqlite3.Connection, tick: int,
                       seed: str = "miniville") -> int:
    """Award conserved $100 Civic Scholarships to skilled 18-year-old graduates."""
    day = day_of(tick)
    grads = conn.execute(
        """SELECT a.id, e.a_id AS aid, a.name, a.hobbies_json, a.skills_json,
                  a.persona, a.professional_persona, a.occupation,
                  a.home_place_id
           FROM events e
           JOIN agents a ON a.id = e.a_id
           WHERE e.day = ? AND e.kind = 'life_event'
             AND e.data LIKE '%"coming_of_age"%'
             AND a.alive = 1
           ORDER BY a.id""",
        (day,),
    ).fetchall()
    if not grads:
        return 0

    hall = conn.execute(
        "SELECT id FROM places WHERE name = 'Town Hall' LIMIT 1"
    ).fetchone()
    hall_id = int(hall["id"]) if hall else None

    awarded = 0
    for g in grads:
        aid = int(g["aid"])
        already = conn.execute(
            """SELECT 1 FROM events
               WHERE a_id = ? AND kind = 'life_event'
                 AND data LIKE '%"civic_scholarship"%' LIMIT 1""",
            (aid,),
        ).fetchone()
        if already:
            continue
        skills = _parse_json_list(g["skills_json"])
        hobbies = _parse_json_list(g["hobbies_json"])
        if len(skills) + len(hobbies) < 2:
            continue
        treasury = conn.execute(
            "SELECT balance_cents FROM town_account WHERE id = 1"
        ).fetchone()
        if not treasury or int(treasury["balance_cents"]) < TOWN_SCHOLARSHIP_FLOOR + SCHOLARSHIP_CENTS:
            break

        # Conserved transfer from town_account to graduate's wallet
        conn.execute(
            "UPDATE town_account SET balance_cents = balance_cents - ? WHERE id = 1",
            (SCHOLARSHIP_CENTS,),
        )
        conn.execute(
            """UPDATE agent_state
               SET money_cents = money_cents + ?, fun = MIN(100, fun + 15)
               WHERE agent_id = ?""",
            (SCHOLARSHIP_CENTS, aid),
        )
        prof = derive_profile(g, seed)
        conn.execute(
            """UPDATE agents
               SET education_level = 'bachelors',
                   standing = COALESCE(standing, 0) + 2,
                   traits_json = ?
               WHERE id = ?""",
            (json.dumps(prof), aid),
        )
        clear_cache(conn)
        focus = skills[0] if skills else (hobbies[0] if hobbies else "their studies")
        text = (
            f"was awarded a ${SCHOLARSHIP_CENTS / 100:,.0f} Miniville Civic Scholarship "
            f"upon turning 18 for excellence in {focus}"
        )
        emit(conn, tick, "life_event", place_id=hall_id or g["home_place_id"],
             a=aid, importance=NOTABLE, text=text, tag="civic_scholarship",
             scholarship_cents=SCHOLARSHIP_CENTS, focus=focus)
        remember(conn, aid, tick,
                 f"Town Hall awarded me a ${SCHOLARSHIP_CENTS / 100:,.0f} Civic Scholarship for {focus}!",
                 kind="reflection", importance=4)
        awarded += 1

    return awarded


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    disc = school_discoveries(conn, tick, seed)
    schol = award_scholarships(conn, tick, seed)
    return {"discoveries": disc, "scholarships": schol}


def youth_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    disc_n = conn.execute(
        'SELECT COUNT(*) n FROM events WHERE data LIKE \'%"youth_discovery"%\''
    ).fetchone()["n"]
    schol_n = conn.execute(
        'SELECT COUNT(*) n FROM events WHERE data LIKE \'%"civic_scholarship"%\''
    ).fetchone()["n"]
    recent = conn.execute(
        """SELECT e.tick, e.day, e.a_id, a.name, e.data
           FROM events e
           JOIN agents a ON a.id = e.a_id
           WHERE e.kind = 'life_event'
             AND (e.data LIKE '%"youth_discovery"%' OR e.data LIKE '%"civic_scholarship"%')
           ORDER BY e.id DESC LIMIT 20"""
    ).fetchall()
    items = []
    for r in recent:
        try:
            d = json.loads(r["data"] or "{}")
        except Exception:
            d = {}
        items.append({
            "tick": int(r["tick"]),
            "day": int(r["day"]) + 1,
            "agent_id": int(r["a_id"]),
            "name": r["name"],
            "tag": d.get("tag"),
            "text": d.get("text", ""),
        })
    return {
        "discoveries": int(disc_n),
        "scholarships": int(schol_n),
        "recent": items,
    }
