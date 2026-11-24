"""Town Courts, Civil Mediation & Jury Hearings (v0.63).

On a bounded cadence (every 10-18 days), Miniville convenes a Town Hall
Hearing or Civil Mediation when there is a defaulted loan, a bitter rivalry
(``relationships.affinity <= -25``), or a recent crime/conflict event.

All financial outcomes are 100% conserved:
* ``restitution_cents`` transfers strictly ``defendant -> plaintiff``
  (``agent_state.money_cents``), capped at affordable cash.
* ``fine_cents`` transfers strictly ``defendant -> town_account``
  (``economy.town_credit``).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import economy
from .events import MAJOR, emit
from .rng import rng_for
from .seasons import date_of
from .timekeeper import TICKS_PER_DAY

MIN_CADENCE_DAYS = 10
MAX_CADENCE_DAYS = 18
P_HEAR_IN_WINDOW = 0.35
RIVALRY_THRESHOLD = -25.0

VERDICTS = (
    "mediated_settlement",
    "restitution_ordered",
    "community_service",
    "dismissed",
)


def ensure_courts_schema(conn: sqlite3.Connection) -> None:
    """Create the `court_cases` table and index if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS court_cases (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            day               INTEGER NOT NULL,
            year              INTEGER NOT NULL,
            kind              TEXT NOT NULL,
            plaintiff_id      INTEGER REFERENCES agents(id),
            defendant_id      INTEGER REFERENCES agents(id),
            judge_id          INTEGER REFERENCES agents(id),
            jury_count        INTEGER NOT NULL DEFAULT 3,
            verdict           TEXT NOT NULL,
            restitution_cents INTEGER NOT NULL DEFAULT 0,
            fine_cents        INTEGER NOT NULL DEFAULT 0,
            summary           TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_court_cases_day ON court_cases(day DESC)"
    )


def _find_dispute(conn: sqlite3.Connection, day: int) -> tuple[str, int | None, int] | None:
    """Locate an active dispute: defaulted loan, bitter rivalry, or crime/conflict."""
    # 1. Defaulted loan
    try:
        loan = conn.execute(
            """SELECT l.lender_id, l.borrower_id
               FROM loans l
               JOIN agents la ON la.id = l.lender_id AND la.alive = 1 AND la.is_child = 0
               JOIN agents ba ON ba.id = l.borrower_id AND ba.alive = 1 AND ba.is_child = 0
               WHERE l.status = 'defaulted'
                 AND l.lender_id != l.borrower_id
                 AND NOT EXISTS (
                     SELECT 1 FROM court_cases c
                     WHERE c.kind = 'loan_default_hearing'
                       AND c.plaintiff_id = l.lender_id
                       AND c.defendant_id = l.borrower_id
                       AND c.day >= ?
                 )
               ORDER BY l.id ASC LIMIT 1""",
            (day - 30,),
        ).fetchone()
        if loan:
            return "loan_default_hearing", int(loan["lender_id"]), int(loan["borrower_id"])
    except sqlite3.OperationalError:
        pass

    # 2. Bitter rivalry (affinity <= -25)
    riv = conn.execute(
        """SELECT r.a_id, r.b_id
           FROM relationships r
           JOIN agents a ON a.id = r.a_id AND a.alive = 1 AND a.is_child = 0
           JOIN agents b ON b.id = r.b_id AND b.alive = 1 AND b.is_child = 0
           WHERE r.affinity <= ?
             AND NOT EXISTS (
                 SELECT 1 FROM court_cases c
                 WHERE c.kind = 'civil_mediation'
                   AND ((c.plaintiff_id = r.a_id AND c.defendant_id = r.b_id)
                        OR (c.plaintiff_id = r.b_id AND c.defendant_id = r.a_id))
                   AND c.day >= ?
             )
           ORDER BY r.affinity ASC, r.a_id ASC, r.b_id ASC LIMIT 1""",
        (RIVALRY_THRESHOLD, day - 30),
    ).fetchone()
    if riv:
        return "civil_mediation", int(riv["a_id"]), int(riv["b_id"])

    # 3. Recent crime or conflict event
    ev = conn.execute(
        """SELECT e.a_id, e.b_id
           FROM events e
           JOIN agents da ON da.id = e.a_id AND da.alive = 1 AND da.is_child = 0
           WHERE e.kind IN ('crime', 'conflict')
             AND e.day >= ?
             AND NOT EXISTS (
                 SELECT 1 FROM court_cases c
                 WHERE c.kind = 'town_hall_hearing'
                   AND c.defendant_id = e.a_id
                   AND c.day >= ?
             )
           ORDER BY e.id DESC LIMIT 1""",
        (day - 18, day - 20),
    ).fetchone()
    if ev:
        def_id = int(ev["a_id"])
        pl_id: int | None = None
        if ev["b_id"] is not None and int(ev["b_id"]) != def_id:
            pl_row = conn.execute(
                "SELECT id FROM agents WHERE id = ? AND alive = 1 AND is_child = 0",
                (int(ev["b_id"]),),
            ).fetchone()
            if pl_row:
                pl_id = int(pl_row["id"])
        return "town_hall_hearing", pl_id, def_id

    return None


def _pick_judge_and_jury(
    conn: sqlite3.Connection,
    plaintiff_id: int | None,
    defendant_id: int,
    r,
) -> tuple[int | None, int]:
    excluded = {defendant_id}
    if plaintiff_id is not None:
        excluded.add(plaintiff_id)
    candidates = conn.execute(
        """SELECT id FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY COALESCE(standing, 0) DESC, id ASC"""
    ).fetchall()
    eligible = [int(c["id"]) for c in candidates if int(c["id"]) not in excluded]
    if not eligible:
        return None, 0
    judge_id = eligible[0]
    remaining = max(0, len(eligible) - 1)
    jury_count = min(r.randint(3, 5), remaining) if remaining > 0 else 0
    return judge_id, jury_count


def _agent_name(conn: sqlite3.Connection, agent_id: int | None) -> str:
    if agent_id is None:
        return "Town of Miniville"
    row = conn.execute("SELECT name FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return str(row["name"]) if row else f"Resident #{agent_id}"


def daily_courts(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Convene a Town Hall Hearing or Civil Mediation when due."""
    ensure_courts_schema(conn)
    day_i = int(day)

    existing = conn.execute(
        "SELECT * FROM court_cases WHERE day = ? LIMIT 1", (day_i,)
    ).fetchone()
    if existing:
        out = dict(existing)
        out["heard"] = False
        out["already_ran"] = True
        return out

    if day_i < MIN_CADENCE_DAYS:
        return {"day": day_i, "heard": False, "reason": "early"}

    last_row = conn.execute("SELECT MAX(day) AS last_day FROM court_cases").fetchone()
    last_day = int(last_row["last_day"]) if last_row and last_row["last_day"] is not None else 0
    gap = day_i - last_day
    if gap < MIN_CADENCE_DAYS:
        return {"day": day_i, "heard": False, "reason": "cadence"}

    r = rng_for(seed, "courts", day_i)
    if gap < MAX_CADENCE_DAYS and r.random() >= P_HEAR_IN_WINDOW:
        return {"day": day_i, "heard": False, "reason": "window_skip"}

    dispute = _find_dispute(conn, day_i)
    if dispute is None:
        return {"day": day_i, "heard": False, "reason": "no_disputes"}

    kind, plaintiff_id, defendant_id = dispute
    judge_id, jury_count = _pick_judge_and_jury(conn, plaintiff_id, defendant_id, r)
    if judge_id is None:
        return {"day": day_i, "heard": False, "reason": "no_judge"}

    if kind == "civil_mediation":
        verdict = r.choices(
            ("mediated_settlement", "community_service", "dismissed"),
            weights=(0.65, 0.20, 0.15),
            k=1,
        )[0]
    elif kind == "loan_default_hearing":
        verdict = r.choices(
            ("restitution_ordered", "mediated_settlement", "dismissed"),
            weights=(0.65, 0.25, 0.10),
            k=1,
        )[0]
    else:
        choices = (
            ("restitution_ordered", "community_service", "mediated_settlement", "dismissed")
            if plaintiff_id is not None
            else ("community_service", "mediated_settlement", "dismissed")
        )
        verdict = r.choice(choices)

    wallet_row = conn.execute(
        "SELECT money_cents FROM agent_state WHERE agent_id = ?", (defendant_id,)
    ).fetchone()
    def_wallet = max(0, int(wallet_row["money_cents"])) if wallet_row else 0

    restitution_cents = 0
    fine_cents = 0

    if verdict == "restitution_ordered" and plaintiff_id is not None and def_wallet > 0:
        restitution_cents = min(def_wallet, r.randint(1500, 4500))
        if restitution_cents > 0:
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (restitution_cents, defendant_id),
            )
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                (restitution_cents, plaintiff_id),
            )
            def_wallet -= restitution_cents

    if verdict in ("restitution_ordered", "community_service") and def_wallet >= 500:
        fine_cents = min(def_wallet, r.randint(500, 1500))
        if fine_cents > 0:
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (fine_cents, defendant_id),
            )
            economy.town_credit(conn, fine_cents)

    if (verdict == "mediated_settlement" or kind == "civil_mediation") and verdict != "dismissed":
        if plaintiff_id is not None:
            lo, hi = min(plaintiff_id, defendant_id), max(plaintiff_id, defendant_id)
            boost = round(r.uniform(12.0, 20.0), 1)
            conn.execute(
                """UPDATE relationships
                   SET affinity = MIN(100.0, affinity + ?),
                       familiarity = MIN(100.0, familiarity + 4.0)
                   WHERE a_id = ? AND b_id = ?""",
                (boost, lo, hi),
            )
            conn.execute(
                "UPDATE agent_state SET stress = MAX(0, stress - 6) WHERE agent_id IN (?, ?)",
                (plaintiff_id, defendant_id),
            )
        else:
            conn.execute(
                "UPDATE agent_state SET stress = MAX(0, stress - 5) WHERE agent_id = ?",
                (defendant_id,),
            )

    p_name = _agent_name(conn, plaintiff_id)
    d_name = _agent_name(conn, defendant_id)
    j_name = _agent_name(conn, judge_id)

    money_bits = []
    if restitution_cents:
        money_bits.append(f"${restitution_cents / 100:,.2f} restitution")
    if fine_cents:
        money_bits.append(f"${fine_cents / 100:,.2f} fine")
    money_txt = f" ({', '.join(money_bits)})" if money_bits else ""
    summary = (
        f"Judge {j_name} & {jury_count}-juror panel heard {p_name} vs. {d_name}: "
        f"{verdict.replace('_', ' ')}{money_txt}."
    )

    year, _, _ = date_of(day_i)
    cur = conn.execute(
        """INSERT INTO court_cases(
               day, year, kind, plaintiff_id, defendant_id, judge_id,
               jury_count, verdict, restitution_cents, fine_cents, summary)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day_i, year, kind, plaintiff_id, defendant_id, judge_id,
            jury_count, verdict, restitution_cents, fine_cents, summary,
        ),
    )
    case_id = int(cur.lastrowid)

    hall = conn.execute(
        "SELECT id FROM places WHERE name = 'Town Hall' LIMIT 1"
    ).fetchone()
    hall_id = int(hall["id"]) if hall else None

    emit(
        conn,
        day_i * TICKS_PER_DAY,
        "court_verdict",
        place_id=hall_id,
        a=defendant_id,
        b=plaintiff_id,
        importance=MAJOR,
        case_id=case_id,
        case_kind=kind,
        judge_id=judge_id,
        jury_count=jury_count,
        verdict=verdict,
        restitution_cents=restitution_cents,
        fine_cents=fine_cents,
        text=summary,
    )

    return {
        "id": case_id,
        "day": day_i,
        "year": year,
        "kind": kind,
        "plaintiff_id": plaintiff_id,
        "defendant_id": defendant_id,
        "judge_id": judge_id,
        "jury_count": jury_count,
        "verdict": verdict,
        "restitution_cents": restitution_cents,
        "fine_cents": fine_cents,
        "summary": summary,
        "heard": True,
    }


def list_cases(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent court cases enriched with party and judge names."""
    try:
        rows = conn.execute(
            """SELECT c.*,
                      pa.name AS plaintiff_name,
                      da.name AS defendant_name,
                      ja.name AS judge_name
               FROM court_cases c
               LEFT JOIN agents pa ON pa.id = c.plaintiff_id
               LEFT JOIN agents da ON da.id = c.defendant_id
               LEFT JOIN agents ja ON ja.id = c.judge_id
               ORDER BY c.day DESC, c.id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def courts_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate court docket metrics and recent cases."""
    try:
        agg = conn.execute(
            """SELECT COUNT(*) AS total_cases,
                      COALESCE(SUM(restitution_cents), 0) AS total_restitution_cents,
                      COALESCE(SUM(fine_cents), 0) AS total_fines_cents,
                      SUM(CASE WHEN verdict = 'mediated_settlement' THEN 1 ELSE 0 END) AS settlements
               FROM court_cases"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "total_cases": 0,
            "settlements": 0,
            "total_restitution_cents": 0,
            "total_fines_cents": 0,
            "latest": None,
            "cases": [],
        }

    recent = list_cases(conn, limit=20)
    return {
        "total_cases": int(agg["total_cases"] or 0),
        "settlements": int(agg["settlements"] or 0),
        "total_restitution_cents": int(agg["total_restitution_cents"] or 0),
        "total_fines_cents": int(agg["total_fines_cents"] or 0),
        "latest": recent[0] if recent else None,
        "cases": recent,
    }
