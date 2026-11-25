"""Community Chest & Mutual Weather Relief (v0.70).

Triggers on severe weather (`weather_days.severity >= 2`), grid `brownout`
(`utility_days.grid_status == 'brownout'`), or every 14-21 days when low-wallet
adults need mutual aid. A high-standing organizer rallies wealthy donors
(`money_cents >= 120_000`) to contribute directly to the lowest-wallet living
adults in the same transaction (`raised_cents == distributed_cents`, exact 1:1
donor -> recipient conservation).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MAJOR, NOTABLE, emit
from .rng import rng_for
from .seasons import date_of
from .timekeeper import TICKS_PER_DAY
from .weather import today as weather_today

DONOR_MIN_CENTS = 120_000
DONOR_KEEP_CENTS = 100_000
EMERGENCY_COOLDOWN_DAYS = 7
MUTUAL_AID_MIN_DAYS = 14
MUTUAL_AID_MAX_DAYS = 21
MAX_DONORS = 12
MAX_RECIPIENTS = 10


def ensure_relief_schema(conn: sqlite3.Connection) -> None:
    """Create the `relief_drives` table if it does not yet exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relief_drives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day INTEGER,
            year INTEGER,
            trigger_kind TEXT,
            district TEXT,
            organizer_agent_id INTEGER,
            donors_count INTEGER,
            recipients_count INTEGER,
            raised_cents INTEGER,
            distributed_cents INTEGER,
            summary TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relief_day ON relief_drives(day)")


def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "day": int(r["day"]),
        "year": int(r["year"]),
        "trigger_kind": r["trigger_kind"],
        "district": r["district"],
        "organizer_agent_id": int(r["organizer_agent_id"]) if r["organizer_agent_id"] is not None else None,
        "organizer_name": r["organizer_name"] if "organizer_name" in r.keys() else None,
        "donors_count": int(r["donors_count"] or 0),
        "recipients_count": int(r["recipients_count"] or 0),
        "raised_cents": int(r["raised_cents"] or 0),
        "distributed_cents": int(r["distributed_cents"] or 0),
        "summary": r["summary"] or "",
    }


def _get_drive_for_day(conn: sqlite3.Connection, day: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT r.*, a.name AS organizer_name
           FROM relief_drives r
           LEFT JOIN agents a ON a.id = r.organizer_agent_id
           WHERE r.day = ?
           LIMIT 1""",
        (int(day),),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def _brownout_district(conn: sqlite3.Connection, day: int) -> str | None:
    try:
        row = conn.execute(
            "SELECT grid_status, outage_district FROM utility_days WHERE day = ?",
            (day,),
        ).fetchone()
        if row and row["grid_status"] == "brownout":
            return str(row["outage_district"] or "Townwide")
    except sqlite3.OperationalError:
        pass
    return None


def _detect_trigger(conn: sqlite3.Connection, day: int, seed: str) -> tuple[str | None, str | None]:
    last_row = conn.execute(
        "SELECT MAX(day) AS last_day FROM relief_drives WHERE day < ?",
        (day,),
    ).fetchone()
    last_day = last_row["last_day"] if last_row and last_row["last_day"] is not None else None
    gap = (day - int(last_day)) if last_day is not None else 999

    w = weather_today(conn, day)
    if int(w.get("severity", 0)) >= 2 and gap >= EMERGENCY_COOLDOWN_DAYS:
        return "severe_weather", "Townwide"

    outage_dist = _brownout_district(conn, day)
    if outage_dist is not None and gap >= EMERGENCY_COOLDOWN_DAYS:
        return "grid_brownout", outage_dist

    if (last_day is None and day >= 7) or gap >= MUTUAL_AID_MIN_DAYS:
        due = gap >= MUTUAL_AID_MAX_DAYS or rng_for(seed, "relief_due", day).random() < 0.55
        if due:
            low_count = conn.execute(
                """SELECT COUNT(*) FROM agents a
                   JOIN agent_state s ON s.agent_id = a.id
                   WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents < 25000"""
            ).fetchone()[0]
            if low_count > 0:
                return "mutual_aid", "Townwide"

    return None, None


def daily_relief(
    conn: sqlite3.Connection,
    day: int,
    seed: str = "miniville",
) -> dict[str, Any] | None:
    """Run daily Community Chest / mutual weather relief drive if triggered (idempotent)."""
    ensure_relief_schema(conn)
    day_i = int(day)
    existing = _get_drive_for_day(conn, day_i)
    if existing is not None:
        return existing

    trigger_kind, district = _detect_trigger(conn, day_i, seed)
    if trigger_kind is None:
        return None

    donors = conn.execute(
        """SELECT a.id, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents >= ?
           ORDER BY s.money_cents DESC, a.id ASC
           LIMIT ?""",
        (DONOR_MIN_CENTS, MAX_DONORS),
    ).fetchall()
    if not donors:
        return None

    donor_ids = {int(d["id"]) for d in donors}
    recip_cap = 80_000 if trigger_kind in ("severe_weather", "grid_brownout") else 35_000
    recip_rows = conn.execute(
        """SELECT a.id, s.money_cents, p.district AS home_district
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents < ?
           ORDER BY s.money_cents ASC, a.id ASC
           LIMIT ?""",
        (recip_cap, MAX_RECIPIENTS + len(donor_ids)),
    ).fetchall()
    recipients = [r for r in recip_rows if int(r["id"]) not in donor_ids][:MAX_RECIPIENTS]
    if not recipients:
        return None

    if district == "Townwide":
        first_dist = recipients[0]["home_district"]
        if first_dist:
            district = str(first_dist)

    organizer = conn.execute(
        """SELECT id, name FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY standing DESC, id ASC
           LIMIT 1"""
    ).fetchone()
    if organizer is None:
        return None
    organizer_id = int(organizer["id"])
    organizer_name = str(organizer["name"])

    # Compute donor contributions (exact 1:1 donor -> recipient)
    contributions: list[tuple[int, int]] = []
    for d in donors:
        surplus = int(d["money_cents"]) - DONOR_KEEP_CENTS
        amt = min(5_000, max(1_000, surplus // 10))
        if amt <= int(d["money_cents"]):
            contributions.append((int(d["id"]), amt))

    raised_cents = sum(amt for _, amt in contributions)
    if raised_cents <= 0:
        return None

    share = raised_cents // len(recipients)
    if share <= 0:
        return None
    rem = raised_cents - share * len(recipients)

    for did, amt in contributions:
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
            (amt, did),
        )
        conn.execute(
            "UPDATE agents SET standing = standing + 1 WHERE id = ?",
            (did,),
        )

    for idx, rec in enumerate(recipients):
        payout = share + (rem if idx == 0 else 0)
        conn.execute(
            """UPDATE agent_state
               SET money_cents = money_cents + ?,
                   stress = MAX(0.0, stress - 6.0),
                   fun = MIN(100.0, fun + 3.0)
               WHERE agent_id = ?""",
            (payout, int(rec["id"])),
        )

    distributed_cents = raised_cents
    year, _, _ = date_of(day_i)
    label = {
        "severe_weather": "severe-weather relief",
        "grid_brownout": "blackout relief",
        "mutual_aid": "Community Chest mutual aid",
    }.get(trigger_kind, "mutual relief")
    summary = (
        f"{organizer_name} rallied {len(contributions)} donors for {label} in {district}, "
        f"delivering ${distributed_cents / 100:,.2f} to {len(recipients)} neighbors"
    )

    cur = conn.execute(
        """INSERT INTO relief_drives
           (day, year, trigger_kind, district, organizer_agent_id, donors_count,
            recipients_count, raised_cents, distributed_cents, summary)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            day_i,
            year,
            trigger_kind,
            district,
            organizer_id,
            len(contributions),
            len(recipients),
            raised_cents,
            distributed_cents,
            summary,
        ),
    )

    imp = MAJOR if trigger_kind in ("severe_weather", "grid_brownout") else NOTABLE
    emit(
        conn,
        day_i * TICKS_PER_DAY,
        "town_event",
        a=organizer_id,
        importance=imp,
        tag="relief_drive",
        day=day_i,
        trigger_kind=trigger_kind,
        district=district,
        donors_count=len(contributions),
        recipients_count=len(recipients),
        raised_cents=raised_cents,
        distributed_cents=distributed_cents,
        text=summary,
    )

    return {
        "id": int(cur.lastrowid),
        "day": day_i,
        "year": year,
        "trigger_kind": trigger_kind,
        "district": district,
        "organizer_agent_id": organizer_id,
        "organizer_name": organizer_name,
        "donors_count": len(contributions),
        "recipients_count": len(recipients),
        "raised_cents": raised_cents,
        "distributed_cents": distributed_cents,
        "summary": summary,
    }


def list_drives(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent relief drives, newest first."""
    try:
        rows = conn.execute(
            """SELECT r.*, a.name AS organizer_name
               FROM relief_drives r
               LEFT JOIN agents a ON a.id = r.organizer_agent_id
               ORDER BY r.day DESC, r.id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_dict(r) for r in rows]


def relief_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate Community Chest relief stats and recent drives."""
    try:
        agg = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(raised_cents), 0) AS total_raised,
                      COALESCE(SUM(distributed_cents), 0) AS total_dist,
                      COALESCE(SUM(recipients_count), 0) AS total_recip
               FROM relief_drives"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "total_drives": 0,
            "total_raised_cents": 0,
            "total_distributed_cents": 0,
            "total_recipients": 0,
            "latest": None,
            "drives": [],
        }
    recent = list_drives(conn, limit=20)
    return {
        "total_drives": int(agg["cnt"]),
        "total_raised_cents": int(agg["total_raised"]),
        "total_distributed_cents": int(agg["total_dist"]),
        "total_recipients": int(agg["total_recip"]),
        "latest": recent[0] if recent else None,
        "drives": recent,
    }
