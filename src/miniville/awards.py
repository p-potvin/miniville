"""Town Awards & Civic Honors (v0.58).

Once per season (mid-season honors day in Jan, Apr, Jul, Oct), Miniville
celebrates standout residents and local merchants across four categories:
  - Citizen of the Season
  - Merchant of the Season
  - Good Neighbor Award
  - Master Artisan

When the town purse holds at least `STIPEND_THRESHOLD_CENTS` (500,000 cents /
$5,000), each award carries a small strictly conserved stipend transferred from
`town_account` to `agent_state.money_cents`; otherwise it is an honorary medal
(`stipend_cents = 0`) so awards never cause a municipal deficit.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .economy import town_balance
from .events import MAJOR, emit
from .rng import rng_for
from .seasons import date_of, season_of
from .timekeeper import TICKS_PER_DAY

STIPEND_THRESHOLD_CENTS = 500_000
STIPEND_CENTS = 2_500
HONORS_MONTHS = {1, 4, 7, 10}
HONORS_MIN_DOM = 15

CATEGORIES = (
    "Citizen of the Season",
    "Merchant of the Season",
    "Good Neighbor Award",
    "Master Artisan",
)


def ensure_awards_schema(conn: sqlite3.Connection) -> None:
    """Create town_awards table and index if they do not exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS town_awards (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               day INTEGER NOT NULL,
               year INTEGER NOT NULL,
               season TEXT NOT NULL,
               category TEXT NOT NULL,
               recipient_agent_id INTEGER REFERENCES agents(id),
               recipient_business_id INTEGER REFERENCES places(id),
               stipend_cents INTEGER NOT NULL DEFAULT 0,
               citation TEXT NOT NULL
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_town_awards_day ON town_awards(day DESC)"
    )


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    return column in cols


def _is_living_adult(conn: sqlite3.Connection, agent_id: int | None) -> bool:
    if agent_id is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM agents WHERE id = ? AND alive = 1", (int(agent_id),)
    ).fetchone()
    return row is not None


def _pick_citizen(conn: sqlite3.Connection, r, excluded: set[int]) -> dict[str, Any] | None:
    rows = conn.execute(
        """SELECT id, name, occupation, COALESCE(standing, 0) AS standing, age
           FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY COALESCE(standing, 0) DESC, COALESCE(age, 0) DESC, id ASC
           LIMIT 8"""
    ).fetchall()
    pool = [dict(row) for row in rows if int(row["id"]) not in excluded] or [dict(row) for row in rows]
    if not pool:
        return None
    top_standing = int(pool[0]["standing"])
    tied = [p for p in pool if int(p["standing"]) >= top_standing - 2][:3]
    chosen = r.choice(tied)
    occ = chosen.get("occupation") or "resident"
    return {
        "agent_id": int(chosen["id"]),
        "business_id": None,
        "name": chosen["name"],
        "citation": f"Recognized for exemplary civic standing ({chosen['standing']}) as a dedicated {occ}.",
    }


def _pick_merchant(conn: sqlite3.Connection, r, excluded: set[int]) -> dict[str, Any] | None:
    has_owner = _has_column(conn, "businesses", "owner_id")
    owner_expr = "b.owner_id" if has_owner else "NULL AS owner_id"
    rows = conn.execute(
        f"""SELECT b.place_id, p.name AS place_name, {owner_expr},
                   (b.revenue_total - b.payroll_total) AS net_cents,
                   b.ema_traffic
            FROM businesses b
            JOIN places p ON p.id = b.place_id
            WHERE b.status = 'open'
            ORDER BY (b.revenue_total - b.payroll_total) DESC, b.ema_traffic DESC, b.place_id ASC
            LIMIT 6"""
    ).fetchall()
    if not rows:
        return None
    chosen = r.choice([dict(row) for row in rows[:3]])
    pid = int(chosen["place_id"])
    aid: int | None = int(chosen["owner_id"]) if chosen.get("owner_id") is not None else None
    if not _is_living_adult(conn, aid):
        jrow = conn.execute(
            """SELECT j.agent_id
               FROM jobs j
               JOIN agents a ON a.id = j.agent_id
               WHERE j.place_id = ? AND a.alive = 1
               ORDER BY j.rank DESC, j.wage_cents DESC, j.agent_id ASC
               LIMIT 1""",
            (pid,),
        ).fetchone()
        aid = int(jrow["agent_id"]) if jrow else None
    if aid is None:
        fallback = conn.execute(
            "SELECT id FROM agents WHERE alive = 1 AND is_child = 0 ORDER BY COALESCE(standing, 0) DESC, id ASC LIMIT 1"
        ).fetchone()
        aid = int(fallback["id"]) if fallback else None
    aname = chosen["place_name"]
    if aid is not None:
        arow = conn.execute("SELECT name FROM agents WHERE id = ?", (aid,)).fetchone()
        if arow:
            aname = f"{arow['name']} ({chosen['place_name']})"
    return {
        "agent_id": aid,
        "business_id": pid,
        "name": aname,
        "citation": f"Honoured for keeping {chosen['place_name']} thriving for Miniville patrons.",
    }


def _pick_good_neighbor(conn: sqlite3.Connection, r, excluded: set[int]) -> dict[str, Any] | None:
    rows = conn.execute(
        """SELECT a.id, a.name,
                  (SELECT COUNT(*) FROM relationships rel
                   WHERE (rel.a_id = a.id OR rel.b_id = a.id)
                     AND (rel.affinity >= 20 OR rel.familiarity >= 35)) AS warm_ties,
                  COALESCE(a.standing, 0) AS standing
           FROM agents a
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY warm_ties DESC, standing DESC, a.id ASC
           LIMIT 8"""
    ).fetchall()
    pool = [dict(row) for row in rows if int(row["id"]) not in excluded] or [dict(row) for row in rows]
    if not pool:
        return None
    chosen = r.choice(pool[:3])
    ties = int(chosen["warm_ties"])
    return {
        "agent_id": int(chosen["id"]),
        "business_id": None,
        "name": chosen["name"],
        "citation": f"Celebrated for neighbourly kindness and {ties} warm community ties.",
    }


def _pick_artisan(conn: sqlite3.Connection, r, excluded: set[int]) -> dict[str, Any] | None:
    try:
        rows = conn.execute(
            """SELECT a.id, a.name,
                      COUNT(k.id) AS works,
                      COALESCE(SUM(k.appreciations), 0) AS praise,
                      COALESCE(a.standing, 0) AS standing
               FROM agents a
               LEFT JOIN keepsakes k ON k.creator_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0
               GROUP BY a.id
               ORDER BY works DESC, praise DESC, standing DESC, a.id ASC
               LIMIT 8"""
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            """SELECT id, name, 0 AS works, 0 AS praise, COALESCE(standing, 0) AS standing
               FROM agents
               WHERE alive = 1 AND is_child = 0
               ORDER BY standing DESC, id ASC
               LIMIT 8"""
        ).fetchall()
    pool = [dict(row) for row in rows if int(row["id"]) not in excluded] or [dict(row) for row in rows]
    if not pool:
        return None
    chosen = r.choice(pool[:3])
    works = int(chosen["works"])
    detail = f"{works} crafted work(s)" if works > 0 else "creative dedication"
    return {
        "agent_id": int(chosen["id"]),
        "business_id": None,
        "name": chosen["name"],
        "citation": f"Awarded for {detail} enriching Miniville's craft heritage.",
    }


def daily_awards(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Run seasonal Town Awards ceremony once per season.

    Idempotent: if `(year, season)` has already been awarded, returns immediately
    without creating duplicate awards or transferring stipends.
    """
    ensure_awards_schema(conn)
    day = int(day)
    year, month, dom = date_of(day)
    season = season_of(day)

    if month not in HONORS_MONTHS or dom < HONORS_MIN_DOM:
        return {"day": day, "season": season, "year": year, "awarded": [], "stipends_cents": 0}

    already = conn.execute(
        "SELECT 1 FROM town_awards WHERE year = ? AND season = ? LIMIT 1",
        (year, season),
    ).fetchone()
    if already:
        return {"day": day, "season": season, "year": year, "awarded": [], "stipends_cents": 0, "already_ran": True}

    r = rng_for(seed, "town_awards", year, season)
    tick = day * TICKS_PER_DAY + 30
    pickers = (
        ("Citizen of the Season", _pick_citizen),
        ("Merchant of the Season", _pick_merchant),
        ("Good Neighbor Award", _pick_good_neighbor),
        ("Master Artisan", _pick_artisan),
    )

    awarded: list[dict[str, Any]] = []
    excluded: set[int] = set()
    total_stipends = 0

    for category, picker in pickers:
        pick = picker(conn, r, excluded)
        if not pick:
            continue
        aid = pick["agent_id"]
        bid = pick["business_id"]
        if aid is not None:
            excluded.add(aid)

        stipend = 0
        if aid is not None and _is_living_adult(conn, aid):
            purse = town_balance(conn)
            if purse >= STIPEND_THRESHOLD_CENTS and purse >= STIPEND_CENTS:
                stipend = STIPEND_CENTS
                conn.execute(
                    "UPDATE town_account SET balance_cents = balance_cents - ? WHERE id = 1",
                    (stipend,),
                )
                conn.execute(
                    "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                    (stipend, aid),
                )
                total_stipends += stipend

        cur = conn.execute(
            """INSERT INTO town_awards(
                   day, year, season, category, recipient_agent_id,
                   recipient_business_id, stipend_cents, citation
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (day, year, season, category, aid, bid, stipend, pick["citation"]),
        )
        award_id = int(cur.lastrowid)
        emit(
            conn,
            tick,
            "town_award",
            place_id=bid,
            a=aid,
            importance=MAJOR,
            award_id=award_id,
            category=category,
            season=season,
            year=year,
            stipend_cents=stipend,
            citation=pick["citation"],
            text=f"{category}: {pick['name']} — {pick['citation']}",
        )
        awarded.append({
            "id": award_id,
            "day": day,
            "year": year,
            "season": season,
            "category": category,
            "recipient_agent_id": aid,
            "recipient_business_id": bid,
            "stipend_cents": stipend,
            "citation": pick["citation"],
        })

    return {
        "day": day,
        "season": season,
        "year": year,
        "awarded": awarded,
        "stipends_cents": total_stipends,
    }


def list_awards(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent town awards enriched with recipient and business names."""
    ensure_awards_schema(conn)
    rows = conn.execute(
        """SELECT tw.*,
                  a.name AS recipient_name,
                  p.name AS business_name
           FROM town_awards tw
           LEFT JOIN agents a ON a.id = tw.recipient_agent_id
           LEFT JOIN places p ON p.id = tw.recipient_business_id
           ORDER BY tw.day DESC, tw.id DESC
           LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def awards_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate Town Awards metrics and recent recipients."""
    ensure_awards_schema(conn)
    tot = conn.execute(
        """SELECT COUNT(*) AS total_awards,
                  COALESCE(SUM(stipend_cents), 0) AS total_stipends_cents
           FROM town_awards"""
    ).fetchone()
    latest = conn.execute(
        "SELECT day, year, season FROM town_awards ORDER BY day DESC, id DESC LIMIT 1"
    ).fetchone()
    cats = conn.execute(
        "SELECT category, COUNT(*) AS n FROM town_awards GROUP BY category ORDER BY n DESC"
    ).fetchall()
    return {
        "total_awards": int(tot["total_awards"]),
        "total_stipends_cents": int(tot["total_stipends_cents"]),
        "latest_day": int(latest["day"]) if latest else None,
        "latest_year": int(latest["year"]) if latest else None,
        "latest_season": latest["season"] if latest else None,
        "by_category": {r["category"]: int(r["n"]) for r in cats},
        "awards": list_awards(conn, limit=20),
    }
