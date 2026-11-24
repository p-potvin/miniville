"""Multi-Step Resident Life Chapters & Goal Chains (v0.64).

Maintains up to 14 active multi-month resident chapters across five arc types:
  - apprentice_to_master
  - civic_climber
  - entrepreneurs_ascent
  - homesteaders_dream
  - patron_of_arts

Chapters evaluate real simulation state (mentorships, standing, council,
wallets, businesses, home deeds, keepsakes, museum exhibits, and elapsed days)
to advance `stage 1 -> 2 -> 3` (`max_stage = 3`) at a multi-week pace. Stage
advancements grant modest morale/standing boosts and emit `chapter_milestone`
or `chapter_completed` events with zero money minted or destroyed.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .events import MAJOR, NOTABLE, emit
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY

MAX_ACTIVE_CHAPTERS = 14
MAX_STAGE = 3
MIN_STAGE_DAYS = 12
FALLBACK_ADVANCE_DAYS = 24

ARC_SPECS: dict[str, tuple[str, str]] = {
    "apprentice_to_master": (
        "From Apprentice to Master",
        "Honing a signature craft toward mastery.",
    ),
    "civic_climber": (
        "Voice of the District",
        "Earning the town's trust through civic service.",
    ),
    "entrepreneurs_ascent": (
        "Founder's Ledger",
        "Building steady savings and commercial enterprise.",
    ),
    "homesteaders_dream": (
        "A Place of One's Own",
        "Securing a family homestead in Miniville.",
    ),
    "patron_of_arts": (
        "Patron of the Commons",
        "Supporting local keepsakes, exhibits, and culture.",
    ),
}
ARC_TYPES = tuple(ARC_SPECS.keys())


def ensure_chapters_schema(conn: sqlite3.Connection) -> None:
    """Create life_chapters and chapters_days tables if they do not exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS life_chapters (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               agent_id INTEGER NOT NULL REFERENCES agents(id),
               arc_type TEXT NOT NULL,
               title TEXT NOT NULL,
               stage INTEGER NOT NULL DEFAULT 1,
               max_stage INTEGER NOT NULL DEFAULT 3,
               status TEXT NOT NULL DEFAULT 'active',
               started_day INTEGER NOT NULL,
               updated_day INTEGER NOT NULL,
               completed_day INTEGER,
               progress_note TEXT NOT NULL DEFAULT ''
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_life_chapters_agent ON life_chapters(agent_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_life_chapters_status ON life_chapters(status, updated_day DESC)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chapters_days (
               day INTEGER PRIMARY KEY
           )"""
    )


def _safe_count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _evaluate_advancement(
    conn: sqlite3.Connection,
    ch: sqlite3.Row,
    day: int,
) -> tuple[bool, str]:
    """Return `(should_advance, progress_note)` for an active chapter."""
    aid = int(ch["agent_id"])
    arc = str(ch["arc_type"])
    stage = int(ch["stage"])
    days_in_stage = day - int(ch["updated_day"])
    if days_in_stage < MIN_STAGE_DAYS:
        return False, ""

    agent = conn.execute(
        """SELECT a.standing, a.skills_json, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.id = ?""",
        (aid,),
    ).fetchone()
    if not agent:
        return False, ""

    standing = int(agent["standing"] or 0)
    money_cents = int(agent["money_cents"] or 0)
    try:
        skills_n = len(json.loads(agent["skills_json"] or "[]"))
    except (ValueError, TypeError):
        skills_n = 0

    next_stage = stage + 1
    if arc == "apprentice_to_master":
        grad = _safe_count(
            conn,
            "SELECT COUNT(*) FROM mentorships WHERE apprentice_id = ? AND status = 'graduated'",
            (aid,),
        )
        lessons = _safe_count(
            conn,
            "SELECT COALESCE(MAX(lessons), 0) FROM mentorships WHERE apprentice_id = ?",
            (aid,),
        )
        keepsakes = _safe_count(
            conn, "SELECT COUNT(*) FROM keepsakes WHERE creator_id = ?", (aid,)
        )
        if next_stage == 2 and (lessons >= 2 or keepsakes >= 1 or skills_n >= 2 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Completed foundational craft studies."
        if next_stage >= 3 and (grad >= 1 or keepsakes >= 2 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Recognized for craft mastery in Miniville."

    elif arc == "civic_climber":
        on_council = _safe_count(
            conn, "SELECT COUNT(*) FROM council WHERE agent_id = ?", (aid,)
        )
        if next_stage == 2 and (standing >= 15 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, f"Built civic goodwill (standing {standing})."
        if next_stage >= 3 and (standing >= 30 or on_council >= 1 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Celebrated as a trusted district leader."

    elif arc == "entrepreneurs_ascent":
        owns_biz = _safe_count(
            conn, "SELECT COUNT(*) FROM businesses WHERE owner_id = ? AND status = 'open'", (aid,)
        )
        if next_stage == 2 and (money_cents >= 120_000 or owns_biz >= 1 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Accumulated working capital for enterprise."
        if next_stage >= 3 and (money_cents >= 220_000 or owns_biz >= 1 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Established a thriving local livelihood."

    elif arc == "homesteaders_dream":
        has_deed = _safe_count(
            conn, "SELECT COUNT(*) FROM home_deeds WHERE owner_agent_id = ?", (aid,)
        )
        if next_stage == 2 and (money_cents >= 100_000 or has_deed >= 1 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Saved toward a permanent family homestead."
        if next_stage >= 3 and (has_deed >= 1 or money_cents >= 180_000 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Put down lasting roots in Miniville."

    elif arc == "patron_of_arts":
        exhibits = _safe_count(
            conn,
            "SELECT COUNT(*) FROM museum_exhibits WHERE donor_agent_id = ? OR subject_agent_id = ?",
            (aid, aid),
        )
        keepsakes = _safe_count(
            conn,
            "SELECT COUNT(*) FROM keepsakes WHERE owner_id = ? OR creator_id = ?",
            (aid, aid),
        )
        if next_stage == 2 and (keepsakes >= 1 or standing >= 12 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Championed local arts and keepsakes."
        if next_stage >= 3 and (exhibits >= 1 or keepsakes >= 2 or days_in_stage >= FALLBACK_ADVANCE_DAYS):
            return True, "Honoured as a dedicated patron of town culture."

    return False, ""


def daily_chapters(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Advance active resident life chapters, close deceased ones, and seed new ones.

    Idempotent per `day` via `chapters_days`. Never mints or burns money.
    """
    ensure_chapters_schema(conn)
    day = int(day)

    if conn.execute("SELECT 1 FROM chapters_days WHERE day = ?", (day,)).fetchone():
        return {"day": day, "seeded": 0, "advanced": 0, "completed": 0, "closed": 0, "already_ran": True}

    tick = day * TICKS_PER_DAY + 22
    closed_count = 0
    advanced_count = 0
    completed_count = 0

    # 1. Close active chapters for deceased residents
    dead_rows = conn.execute(
        """SELECT lc.id
           FROM life_chapters lc
           JOIN agents a ON a.id = lc.agent_id
           WHERE lc.status = 'active' AND a.alive = 0"""
    ).fetchall()
    for dr in dead_rows:
        conn.execute(
            """UPDATE life_chapters
               SET status = 'closed', updated_day = ?,
                   progress_note = 'Closed after resident passed away'
               WHERE id = ?""",
            (day, int(dr["id"])),
        )
        closed_count += 1

    # 2. Evaluate active chapters for stage progression
    active_rows = conn.execute(
        """SELECT lc.*, a.name AS agent_name
           FROM life_chapters lc
           JOIN agents a ON a.id = lc.agent_id
           WHERE lc.status = 'active' AND a.alive = 1
           ORDER BY lc.id ASC"""
    ).fetchall()
    for ch in active_rows:
        advance, note = _evaluate_advancement(conn, ch, day)
        if not advance:
            continue
        new_stage = int(ch["stage"]) + 1
        max_stage = int(ch["max_stage"])
        aid = int(ch["agent_id"])
        aname = str(ch["agent_name"])

        if new_stage >= max_stage:
            conn.execute(
                """UPDATE life_chapters
                   SET stage = ?, status = 'completed', updated_day = ?,
                       completed_day = ?, progress_note = ?
                   WHERE id = ?""",
                (max_stage, day, day, note, int(ch["id"])),
            )
            conn.execute(
                "UPDATE agent_state SET fun = MIN(100, fun + 6), stress = MAX(0, stress - 5) WHERE agent_id = ?",
                (aid,),
            )
            conn.execute(
                "UPDATE agents SET standing = MIN(100, COALESCE(standing, 0) + 2) WHERE id = ?",
                (aid,),
            )
            emit(
                conn,
                tick,
                "chapter_completed",
                a=aid,
                importance=MAJOR,
                chapter_id=int(ch["id"]),
                arc_type=ch["arc_type"],
                title=ch["title"],
                text=f"{aname} completed '{ch['title']}': {note}",
            )
            completed_count += 1
        else:
            conn.execute(
                """UPDATE life_chapters
                   SET stage = ?, updated_day = ?, progress_note = ?
                   WHERE id = ?""",
                (new_stage, day, note, int(ch["id"])),
            )
            conn.execute(
                "UPDATE agent_state SET fun = MIN(100, fun + 4), stress = MAX(0, stress - 3) WHERE agent_id = ?",
                (aid,),
            )
            conn.execute(
                "UPDATE agents SET standing = MIN(100, COALESCE(standing, 0) + 1) WHERE id = ?",
                (aid,),
            )
            emit(
                conn,
                tick,
                "chapter_milestone",
                a=aid,
                importance=NOTABLE,
                chapter_id=int(ch["id"]),
                arc_type=ch["arc_type"],
                stage=new_stage,
                title=ch["title"],
                text=f"{aname} reached stage {new_stage}/{max_stage} of '{ch['title']}': {note}",
            )
            advanced_count += 1

    # 3. Seed new chapters up to MAX_ACTIVE_CHAPTERS
    cur_active = _safe_count(
        conn, "SELECT COUNT(*) FROM life_chapters WHERE status = 'active'"
    )
    needed = max(0, MAX_ACTIVE_CHAPTERS - cur_active)
    seeded_count = 0
    if needed > 0:
        r = rng_for(seed, "life_chapters", day)
        candidates = conn.execute(
            """SELECT a.id, a.name
               FROM agents a
               WHERE a.alive = 1 AND a.is_child = 0
                 AND NOT EXISTS (
                     SELECT 1 FROM life_chapters lc
                     WHERE lc.agent_id = a.id AND lc.status = 'active'
                 )
               ORDER BY COALESCE(a.standing, 0) DESC, a.id ASC
               LIMIT 40"""
        ).fetchall()
        if candidates:
            pool = list(candidates)
            r.shuffle(pool)
            to_seed = min(needed, len(pool), 4)
            for cand in pool[:to_seed]:
                aid = int(cand["id"])
                done_arcs = {
                    row["arc_type"]
                    for row in conn.execute(
                        "SELECT arc_type FROM life_chapters WHERE agent_id = ?", (aid,)
                    ).fetchall()
                }
                avail_arcs = [a for a in ARC_TYPES if a not in done_arcs] or list(ARC_TYPES)
                arc_type = r.choice(avail_arcs)
                title, note = ARC_SPECS[arc_type]
                conn.execute(
                    """INSERT INTO life_chapters(
                           agent_id, arc_type, title, stage, max_stage,
                           status, started_day, updated_day, progress_note
                       ) VALUES (?, ?, ?, 1, ?, 'active', ?, ?, ?)""",
                    (aid, arc_type, title, MAX_STAGE, day, day, note),
                )
                seeded_count += 1

    conn.execute("INSERT INTO chapters_days(day) VALUES (?)", (day,))
    return {
        "day": day,
        "seeded": seeded_count,
        "advanced": advanced_count,
        "completed": completed_count,
        "closed": closed_count,
        "already_ran": False,
    }


def list_chapters(
    conn: sqlite3.Connection, status: str = "all", limit: int = 24
) -> list[dict[str, Any]]:
    """Return life chapters filtered by status ('all', 'active', 'completed', 'closed')."""
    ensure_chapters_schema(conn)
    if status != "all":
        rows = conn.execute(
            """SELECT lc.*, a.name AS agent_name, a.occupation
               FROM life_chapters lc
               LEFT JOIN agents a ON a.id = lc.agent_id
               WHERE lc.status = ?
               ORDER BY lc.updated_day DESC, lc.id DESC
               LIMIT ?""",
            (status, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT lc.*, a.name AS agent_name, a.occupation
               FROM life_chapters lc
               LEFT JOIN agents a ON a.id = lc.agent_id
               ORDER BY CASE lc.status WHEN 'active' THEN 0 WHEN 'completed' THEN 1 ELSE 2 END,
                        lc.updated_day DESC, lc.id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def chapters_of(conn: sqlite3.Connection, agent_id: int) -> list[dict[str, Any]]:
    """Return all life chapters for a given resident."""
    ensure_chapters_schema(conn)
    rows = conn.execute(
        """SELECT lc.*, a.name AS agent_name
           FROM life_chapters lc
           LEFT JOIN agents a ON a.id = lc.agent_id
           WHERE lc.agent_id = ?
           ORDER BY lc.started_day DESC, lc.id DESC""",
        (int(agent_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def chapters_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate counts and recent chapters across all arc types."""
    ensure_chapters_schema(conn)
    counts = {
        r["status"]: int(r["n"])
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM life_chapters GROUP BY status"
        ).fetchall()
    }
    by_arc = {
        r["arc_type"]: int(r["n"])
        for r in conn.execute(
            "SELECT arc_type, COUNT(*) AS n FROM life_chapters GROUP BY arc_type ORDER BY n DESC"
        ).fetchall()
    }
    return {
        "active_count": counts.get("active", 0),
        "completed_count": counts.get("completed", 0),
        "closed_count": counts.get("closed", 0),
        "by_arc_type": by_arc,
        "chapters": list_chapters(conn, status="all", limit=24),
    }
