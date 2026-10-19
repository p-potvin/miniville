"""Town Historical Archives, Cultural Eras, and Hall of Fame (v0.31).

As Miniville runs across months and years, its collective story crystallizes
into named historical eras and town record-holders:

* **Cultural & Economic Eras (`town_eras` table)** — every 30 simulation days
  (and on-demand via `ensure_eras`), the town's recent ledger is synthesized
  into a named chapter (`prosperity`, `craft_renaissance`, `civic_boom`,
  `festival_golden_age`, or `quiet_homestead`) with a human-readable summary.
* **Miniville Hall of Fame (`hall_of_fame`)** — live leaderboard of the town's
  most distinguished figures across 6 categories:
  1. *Pillar of Miniville* (highest civic standing)
  2. *Master Artisan* (most cultural keepsakes created & appreciated)
  3. *Grand Mentor* (most apprentices mentored/graduated)
  4. *Blue-Ribbon Legend* (most seasonal town competition victories)
  5. *Longest-Standing Couple* (deepest enduring marriage/partnership)
  6. *Town Historian / Diarist* (most personal milestone diary entries)
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_meta
from .events import NOTABLE, emit
from .timekeeper import TICKS_PER_DAY, day_of

ERA_SPAN_DAYS = 30


def _classify_window(conn: sqlite3.Connection, start_day: int,
                     end_day: int) -> tuple[str, str, str]:
    """Return `(name, theme, summary)` for the day window `[start_day, end_day]`."""
    crafts = conn.execute(
        """SELECT COUNT(*) n FROM events
           WHERE day BETWEEN ? AND ?
             AND (data LIKE '%"keepsake_created"%'
                  OR data LIKE '%"keepsake_exhibited"%'
                  OR data LIKE '%"competition_won"%')""",
        (start_day, end_day),
    ).fetchone()["n"]

    civic = conn.execute(
        """SELECT COUNT(*) n FROM events
           WHERE day BETWEEN ? AND ?
             AND (data LIKE '%"civic_landmark_completed"%'
                  OR data LIKE '%"block_party"%'
                  OR data LIKE '%"mutual_aid"%')""",
        (start_day, end_day),
    ).fetchone()["n"]

    weddings = conn.execute(
        """SELECT COUNT(*) n FROM events
           WHERE day BETWEEN ? AND ?
             AND (data LIKE '%"marriage"%' OR data LIKE '%"birth"%')""",
        (start_day, end_day),
    ).fetchone()["n"]

    econ = conn.execute(
        """SELECT COUNT(*) AS n_days, AVG(unemployment_bp) / 10000.0 AS u
           FROM economy_days WHERE day BETWEEN ? AND ?""",
        (start_day, end_day),
    ).fetchone()
    avg_u = float(econ["u"]) if (econ and int(econ["n_days"] or 0) >= 5 and econ["u"] is not None) else 0.12

    label_span = f"Days {start_day + 1}–{end_day + 1}"
    if civic >= 3:
        return (
            f"The Civic Renewal ({label_span})",
            "civic_boom",
            f"Neighbours rallied around public works, block parties, and mutual aid ({civic} civic milestones).",
        )
    if crafts >= 4:
        return (
            f"The Artisan Renaissance ({label_span})",
            "craft_renaissance",
            f"Local workshops, cultural exhibitions, and seasonal contests flourished ({crafts} cultural highlights).",
        )
    if avg_u <= 0.09:
        return (
            f"The High-Street Boom ({label_span})",
            "prosperity",
            f"Full storefronts and steady paychecks kept unemployment near {avg_u * 100:.1f}%.",
        )
    if weddings >= 2:
        return (
            f"The Hearth & Harvest Season ({label_span})",
            "hearth_season",
            f"New marriages and growing families anchored life across the districts ({weddings} family milestones).",
        )
    return (
        f"The Quiet Homestead Chapter ({label_span})",
        "quiet_homestead",
        f"Steady routines, porch conversations, and neighbourhood traditions carried the town through {label_span}.",
    )


def ensure_eras(conn: sqlite3.Connection, tick: int | None = None) -> int:
    """Populate `town_eras` for completed 30-day windows (or an initial chapter)."""
    if tick is None:
        tick = int(get_meta(conn, "tick", "0") or 0)
    cur_day = day_of(tick)
    existing = {
        (int(r["start_day"]), int(r["end_day"]))
        for r in conn.execute("SELECT start_day, end_day FROM town_eras").fetchall()
    }

    created = 0
    # Record every completed 30-day window
    w_start = 0
    while w_start + ERA_SPAN_DAYS - 1 <= cur_day:
        w_end = w_start + ERA_SPAN_DAYS - 1
        if (w_start, w_end) not in existing:
            name, theme, summary = _classify_window(conn, w_start, w_end)
            conn.execute(
                """INSERT INTO town_eras(start_day, end_day, name, theme, summary)
                   VALUES(?, ?, ?, ?, ?)""",
                (w_start, w_end, name, theme, summary),
            )
            existing.add((w_start, w_end))
            created += 1
        w_start += ERA_SPAN_DAYS

    # If the town is younger than 30 days and has no era row yet, seed the founding chapter
    if not existing:
        end_d = max(0, cur_day)
        name, theme, summary = _classify_window(conn, 0, end_d)
        name = f"The Founding Chapter (Days 1–{end_d + 1})"
        conn.execute(
            """INSERT INTO town_eras(start_day, end_day, name, theme, summary)
               VALUES(0, ?, ?, ?, ?)""",
            (end_d, name, theme, summary),
        )
        created += 1

    return created


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    day = day_of(tick)
    created = 0
    if day > 0 and day % ERA_SPAN_DAYS == 0:
        created = ensure_eras(conn, tick)
        if created > 0:
            latest = conn.execute(
                "SELECT * FROM town_eras ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if latest:
                emit(conn, tick, "town_event", importance=NOTABLE,
                     text=f"Town historians chronicled {latest['name']}: {latest['summary']}",
                     tag="era_chronicled", era_name=latest["name"])
    return {"eras_recorded": created}


def hall_of_fame(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Compute Miniville's Hall of Fame record-holders across 6 categories."""
    records: list[dict[str, Any]] = []

    # 1. Pillar of Miniville (highest standing)
    pillar = conn.execute(
        """SELECT id, name, occupation, COALESCE(standing, 0) AS val
           FROM agents WHERE alive = 1
           ORDER BY COALESCE(standing, 0) DESC, id ASC LIMIT 1"""
    ).fetchone()
    if pillar:
        records.append({
            "category": "Pillar of Miniville",
            "agent_id": int(pillar["id"]),
            "name": pillar["name"],
            "metric": f"{int(pillar['val']):+d} standing",
            "detail": pillar["occupation"] or "resident",
        })

    # 2. Master Artisan (most keepsakes created + appreciations)
    artisan = conn.execute(
        """SELECT a.id, a.name, COUNT(k.id) AS pieces,
                  COALESCE(SUM(k.appreciations), 0) AS likes
           FROM keepsakes k
           JOIN agents a ON a.id = k.creator_id
           WHERE a.alive = 1
           GROUP BY a.id
           ORDER BY (COUNT(k.id) * 2 + COALESCE(SUM(k.appreciations), 0)) DESC, a.id ASC
           LIMIT 1"""
    ).fetchone()
    if artisan:
        records.append({
            "category": "Master Artisan",
            "agent_id": int(artisan["id"]),
            "name": artisan["name"],
            "metric": f"{int(artisan['pieces'])} works ({int(artisan['likes'])} appreciations)",
            "detail": "Cultural Commons creator",
        })

    # 3. Grand Mentor (most apprentices mentored)
    mentor = conn.execute(
        """SELECT a.id, a.name, COUNT(m.apprentice_id) AS n_app,
                  SUM(CASE WHEN m.status = 'graduated' THEN 1 ELSE 0 END) AS n_grad,
                  MAX(m.craft) AS craft
           FROM mentorships m
           JOIN agents a ON a.id = m.mentor_id
           WHERE a.alive = 1
           GROUP BY a.id
           ORDER BY n_grad DESC, n_app DESC, m.lessons DESC, a.id ASC
           LIMIT 1"""
    ).fetchone()
    if mentor:
        records.append({
            "category": "Grand Mentor",
            "agent_id": int(mentor["id"]),
            "name": mentor["name"],
            "metric": f"{int(mentor['n_app'])} protégé(s) ({int(mentor['n_grad'] or 0)} graduated)",
            "detail": f"Master of {mentor['craft']}",
        })

    # 4. Blue-Ribbon Legend (most competition wins)
    champ = conn.execute(
        """SELECT a.id, a.name, COUNT(c.id) AS wins, MAX(c.title) AS last_title
           FROM competitions c
           JOIN agents a ON a.id = c.winner_id
           WHERE a.alive = 1
           GROUP BY a.id
           ORDER BY wins DESC, a.id ASC
           LIMIT 1"""
    ).fetchone()
    if champ:
        records.append({
            "category": "Blue-Ribbon Legend",
            "agent_id": int(champ["id"]),
            "name": champ["name"],
            "metric": f"{int(champ['wins'])} blue ribbon(s)",
            "detail": champ["last_title"] or "Town Contest Champion",
        })

    # 5. Enduring Sweethearts (highest affinity + familiarity spouse/partner pair)
    couple = conn.execute(
        """SELECT r.a_id, a.name AS aname, b.name AS bname,
                  r.affinity, r.familiarity, r.label
           FROM relationships r
           JOIN agents a ON a.id = r.a_id
           JOIN agents b ON b.id = r.b_id
           WHERE a.alive = 1 AND b.alive = 1
             AND r.label IN ('spouse', 'partner', 'sweetheart')
           ORDER BY (r.affinity + r.familiarity) DESC, r.a_id ASC
           LIMIT 1"""
    ).fetchone()
    if couple:
        records.append({
            "category": "Enduring Sweethearts",
            "agent_id": int(couple["a_id"]),
            "name": f"{couple['aname']} & {couple['bname']}",
            "metric": f"affinity {float(couple['affinity']):.0f} · fam {float(couple['familiarity']):.0f}",
            "detail": couple["label"],
        })

    # 6. Town Diarist (most personal diary entries)
    diarist = conn.execute(
        """SELECT a.id, a.name, COUNT(d.id) AS entries
           FROM diary_entries d
           JOIN agents a ON a.id = d.agent_id
           WHERE a.alive = 1
           GROUP BY a.id
           ORDER BY entries DESC, a.id ASC
           LIMIT 1"""
    ).fetchone()
    if diarist:
        records.append({
            "category": "Town Diarist",
            "agent_id": int(diarist["id"]),
            "name": diarist["name"],
            "metric": f"{int(diarist['entries'])} diary entries",
            "detail": "First-person chronicler",
        })

    return records


def history_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_eras(conn)
    eras = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM town_eras ORDER BY start_day DESC LIMIT 12"
        ).fetchall()
    ]
    return {
        "eras": eras,
        "hall_of_fame": hall_of_fame(conn),
    }
