"""Seasonal Town Festivals & Fairgrounds (v0.54).

Miniville hosts four 2-day seasonal festivals each year:

* **Winter Lantern Market** (Feb 14–15)
* **Spring Blossom Fair** (May 10–11)
* **Lakeshore Summer Regatta** (Jul 18–19)
* **Autumn Harvest Jamboree** (Oct 18–19)

Each festival checks `weather_days` via `weather.today()` and automatically
shifts from its outdoor fairground to an indoor shelter venue when storm or
severe weather (`severity >= 2`) strikes. All booth fees, patron spending, and
competition prizes are 100% conserved transfers between `agent_state.money_cents`,
`businesses.balance_cents`, and `town_account.balance_cents` (zero net
mint/burn).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import economy, weather
from .events import NOTABLE, emit
from .rng import rng_for
from .seasons import date_of
from .timekeeper import day_of

BOOTH_FEE_CENTS = 2_500          # $25 per participating commercial booth
PATRON_SPEND_CENTS = 800         # $8 fairground spending per attending adult
BOOTH_SHARE_CENTS = 500          # $5 to the booth business
GATE_SHARE_CENTS = 300           # $3 to the town purse
PRIZE_POOL_CENTS = 5_000         # $50 conserved prize pool (capped by town purse)
MAX_BOOTHS = 4
MAX_PATRONS_PER_DAY = 30

SEVERE_CONDITIONS = frozenset({"storm", "blizzard", "heatwave"})

FESTIVAL_SPECS: dict[str, dict[str, Any]] = {
    "winter": {
        "name": "Winter Lantern Market",
        "month": 2,
        "dom": 14,
        "outdoor_venue": "Lush Meadow Park",
        "indoor_venue": "Miniville Community Center",
        "keywords": {"crafts", "woodworking", "baking", "cooking", "music", "knitting", "art"},
    },
    "spring": {
        "name": "Spring Blossom Fair",
        "month": 5,
        "dom": 10,
        "outdoor_venue": "Lush Meadow Park",
        "indoor_venue": "Miniville Community Center",
        "keywords": {"gardening", "plants", "botany", "baking", "painting", "photography", "art"},
    },
    "summer": {
        "name": "Lakeshore Summer Regatta",
        "month": 7,
        "dom": 18,
        "outdoor_venue": "Lakeshore Marina",
        "indoor_venue": "The Bijou Theater",
        "keywords": {"fishing", "boating", "kayaking", "swimming", "outdoors", "sailing", "music"},
    },
    "autumn": {
        "name": "Autumn Harvest Jamboree",
        "month": 10,
        "dom": 18,
        "outdoor_venue": "Lush Meadow Park",
        "indoor_venue": "Miniville Community Center",
        "keywords": {"baking", "cooking", "gardening", "woodworking", "storytelling", "history"},
    },
}


def ensure_festivals_schema(conn: sqlite3.Connection) -> None:
    """Create the `festivals` table and indexes if they do not exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS festivals (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            season             TEXT NOT NULL,
            year               INTEGER NOT NULL,
            name               TEXT NOT NULL,
            venue_id           INTEGER REFERENCES places(id),
            start_day          INTEGER NOT NULL,
            end_day            INTEGER NOT NULL,
            status             TEXT NOT NULL DEFAULT 'active',
            attendance_count   INTEGER NOT NULL DEFAULT 0,
            revenue_cents      INTEGER NOT NULL DEFAULT 0,
            winner_agent_id    INTEGER REFERENCES agents(id),
            winner_business_id INTEGER REFERENCES places(id),
            weather_note       TEXT NOT NULL DEFAULT '',
            summary            TEXT NOT NULL DEFAULT '',
            UNIQUE(season, year)
        );
        CREATE INDEX IF NOT EXISTS idx_festivals_days ON festivals(start_day, end_day);
        CREATE INDEX IF NOT EXISTS idx_festivals_status ON festivals(status);
        """
    )


def festival_schedule_for_day(day: int) -> dict[str, Any] | None:
    """Return festival schedule metadata if `day` falls in a 2-day festival window."""
    if day < 0:
        return None
    year, month, dom = date_of(day)
    for season, spec in FESTIVAL_SPECS.items():
        if month == spec["month"] and dom in (spec["dom"], spec["dom"] + 1):
            start_day = day if dom == spec["dom"] else day - 1
            return {
                "season": season,
                "year": year,
                "start_day": start_day,
                "end_day": start_day + 1,
                "is_start": day == start_day,
                "is_end": day == start_day + 1,
                "spec": spec,
            }
    return None


def _is_severe_weather(w: dict[str, Any]) -> bool:
    return int(w.get("severity", 0)) >= 2 or str(w.get("condition", "")) in SEVERE_CONDITIONS


def _resolve_venue(
    conn: sqlite3.Connection, spec: dict[str, Any], day: int
) -> tuple[int | None, str, str, bool]:
    """Choose outdoor fairground or indoor shelter based on `weather_days`."""
    w = weather.today(conn, day)
    severe = _is_severe_weather(w)

    def _find_place(name: str) -> sqlite3.Row | None:
        return conn.execute(
            """SELECT p.id, p.name, COALESCE(b.status, 'open') AS status
               FROM places p
               LEFT JOIN businesses b ON b.place_id = p.id
               WHERE p.name = ? LIMIT 1""",
            (name,),
        ).fetchone()

    outdoor = _find_place(spec["outdoor_venue"])
    indoor = _find_place(spec["indoor_venue"])

    if severe or outdoor is None or outdoor["status"] == "closed":
        chosen = indoor
        if chosen is None or chosen["status"] == "closed":
            chosen = conn.execute(
                """SELECT p.id, p.name
                   FROM places p
                   LEFT JOIN businesses b ON b.place_id = p.id
                   WHERE p.kind IN ('civic', 'public')
                     AND COALESCE(b.status, 'open') = 'open'
                     AND p.tags NOT LIKE '%outdoors%'
                     AND p.tags NOT LIKE '%water%'
                   ORDER BY p.id ASC LIMIT 1"""
            ).fetchone()
        venue_id = int(chosen["id"]) if chosen else None
        venue_name = chosen["name"] if chosen else spec["indoor_venue"]
        if severe:
            note = (
                f"Moved indoors to {venue_name} due to {w['label']} "
                f"({w['temp_f']}°F)"
            )
        else:
            note = f"Held indoors at {venue_name} ({w['label']}, {w['temp_f']}°F)"
        return venue_id, venue_name, note, True

    venue_id = int(outdoor["id"])
    venue_name = outdoor["name"]
    note = f"Held outdoors at {venue_name} ({w['label']}, {w['temp_f']}°F)"
    return venue_id, venue_name, note, False


def _select_booth_businesses(
    conn: sqlite3.Connection, seed: str, year: int, season: str
) -> list[int]:
    """Pick open commercial businesses that can afford a booth fee."""
    economy.ensure_businesses(conn)
    rows = conn.execute(
        """SELECT b.place_id, b.balance_cents, p.kind, p.tags
           FROM businesses b
           JOIN places p ON p.id = b.place_id
           WHERE b.status = 'open' AND p.kind != 'home'
             AND b.balance_cents >= ?
           ORDER BY b.place_id ASC""",
        (BOOTH_FEE_CENTS,),
    ).fetchall()
    eligible: list[int] = []
    for r in rows:
        tags = set(json.loads(r["tags"] or "[]"))
        if tags & economy.PUBLIC_TAGS or r["kind"] == "civic":
            continue
        eligible.append(int(r["place_id"]))
    if not eligible:
        return []
    r = rng_for(seed, "festival_booths", year, season)
    count = min(MAX_BOOTHS, len(eligible))
    return sorted(r.sample(eligible, count))


def _collect_booth_fees(conn: sqlite3.Connection, booth_ids: list[int]) -> int:
    """Transfer booth fees from participating businesses to `town_account`."""
    total = 0
    for pid in booth_ids:
        row = conn.execute(
            "SELECT balance_cents FROM businesses WHERE place_id = ?", (pid,)
        ).fetchone()
        if not row or int(row["balance_cents"]) < BOOTH_FEE_CENTS:
            continue
        conn.execute(
            "UPDATE businesses SET balance_cents = balance_cents - ? WHERE place_id = ?",
            (BOOTH_FEE_CENTS, pid),
        )
        economy.town_credit(conn, BOOTH_FEE_CENTS)
        total += BOOTH_FEE_CENTS
    return total


def _run_patron_spending(
    conn: sqlite3.Connection,
    day: int,
    seed: str,
    season: str,
    year: int,
    booth_ids: list[int],
) -> tuple[int, int]:
    """Simulate fairground attendance & conserved patron spending for one day."""
    adults = conn.execute(
        """SELECT a.id, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY a.id ASC"""
    ).fetchall()
    if not adults:
        return 0, 0

    r = rng_for(seed, "festival_patrons", year, season, day)
    n_attend = min(MAX_PATRONS_PER_DAY, max(1, len(adults) // 2))
    if len(adults) <= MAX_PATRONS_PER_DAY:
        attendees = list(adults)
    else:
        attendees = r.sample(list(adults), n_attend)

    revenue = 0
    for idx, row in enumerate(attendees):
        aid = int(row["id"])
        wallet = int(row["money_cents"])
        if wallet >= PATRON_SPEND_CENTS:
            conn.execute(
                """UPDATE agent_state
                   SET money_cents = money_cents - ?,
                       fun = MIN(100, fun + 8),
                       stress = MAX(0, stress - 5)
                   WHERE agent_id = ?""",
                (PATRON_SPEND_CENTS, aid),
            )
            if booth_ids:
                booth_pid = booth_ids[idx % len(booth_ids)]
                conn.execute(
                    "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
                    (BOOTH_SHARE_CENTS, booth_pid),
                )
                economy.town_credit(conn, GATE_SHARE_CENTS)
            else:
                economy.town_credit(conn, PATRON_SPEND_CENTS)
            revenue += PATRON_SPEND_CENTS
        else:
            conn.execute(
                """UPDATE agent_state
                   SET fun = MIN(100, fun + 5),
                       stress = MAX(0, stress - 3)
                   WHERE agent_id = ?""",
                (aid,),
            )

    return len(attendees), revenue


def _select_winner_agent(
    conn: sqlite3.Connection,
    seed: str,
    year: int,
    season: str,
    keywords: set[str],
) -> tuple[int | None, str]:
    """Deterministically pick the festival competition champion."""
    adults = conn.execute(
        """SELECT a.id, a.name, a.occupation, a.hobbies_json, a.skills_json,
                  COALESCE(a.standing, 0) AS standing
           FROM agents a
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY a.id ASC"""
    ).fetchall()
    if not adults:
        return None, "nobody"

    scored: list[tuple[float, int, str]] = []
    for a in adults:
        aid = int(a["id"])
        tokens: set[str] = set()
        if a["occupation"]:
            tokens.update(str(a["occupation"]).lower().split())
        for raw in (a["hobbies_json"], a["skills_json"]):
            try:
                items = json.loads(raw or "[]")
            except (TypeError, ValueError):
                items = []
            for item in items:
                s = str(item).lower()
                tokens.add(s)
                tokens.update(w.strip(",.") for w in s.split())
        hits = len(tokens & keywords)
        roll = rng_for(seed, "festival_winner", year, season, aid).uniform(0.0, 20.0)
        score = hits * 10.0 + float(a["standing"]) * 0.5 + roll
        scored.append((score, -aid, aid, str(a["name"])))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    _, _, winner_id, winner_name = scored[0]
    return winner_id, winner_name


def _select_winner_business(
    conn: sqlite3.Connection,
    seed: str,
    year: int,
    season: str,
    booth_ids: list[int],
) -> tuple[int | None, str | None]:
    """Deterministically pick the winning festival booth business."""
    candidates = booth_ids
    if not candidates:
        rows = conn.execute(
            """SELECT b.place_id
               FROM businesses b
               JOIN places p ON p.id = b.place_id
               WHERE b.status = 'open' AND p.kind != 'home' AND p.kind != 'civic'
               ORDER BY b.place_id ASC"""
        ).fetchall()
        candidates = [int(r["place_id"]) for r in rows]
    if not candidates:
        return None, None

    best_pid = max(
        candidates,
        key=lambda pid: (
            rng_for(seed, "festival_biz_winner", year, season, pid).random(),
            -pid,
        ),
    )
    row = conn.execute("SELECT name FROM places WHERE id = ?", (best_pid,)).fetchone()
    return best_pid, (str(row["name"]) if row else None)


def _award_prize_pool(
    conn: sqlite3.Connection,
    winner_agent_id: int | None,
    winner_business_id: int | None,
) -> int:
    """Transfer a conserved prize from `town_account` to the festival winners."""
    if winner_agent_id is None and winner_business_id is None:
        return 0
    available = economy.town_balance(conn)
    prize = min(PRIZE_POOL_CENTS, max(0, available))
    if prize <= 0:
        return 0

    short = economy.town_debit(conn, prize)
    if short > 0:
        # Should never happen due to min(PRIZE_POOL_CENTS, available), guard anyway.
        economy.town_credit(conn, short)
        return 0

    if winner_agent_id is not None and winner_business_id is not None:
        agent_share = (prize * 70) // 100
        biz_share = prize - agent_share
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (agent_share, winner_agent_id),
        )
        conn.execute(
            "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
            (biz_share, winner_business_id),
        )
    elif winner_agent_id is not None:
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (prize, winner_agent_id),
        )
    elif winner_business_id is not None:
        conn.execute(
            "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
            (prize, winner_business_id),
        )
    return prize


def due(conn: sqlite3.Connection, tick: int, seed: str = "miniville") -> dict[str, Any]:
    """Open, run, and close seasonal 2-day festivals idempotently."""
    ensure_festivals_schema(conn)
    day = day_of(tick)
    sched = festival_schedule_for_day(day)
    if sched is None:
        return {"active": False, "action": "none", "day": day}

    season = sched["season"]
    year = sched["year"]
    start_day = sched["start_day"]
    end_day = sched["end_day"]
    spec = sched["spec"]
    name = spec["name"]

    existing = conn.execute(
        "SELECT * FROM festivals WHERE season = ? AND year = ?",
        (season, year),
    ).fetchone()

    if existing and existing["status"] == "closed":
        return {"active": False, "action": "already_closed", "festival_id": int(existing["id"])}

    booth_ids = _select_booth_businesses(conn, seed, year, season)

    # Opening on start_day (or late start if end_day reached without start_day)
    if existing is None:
        venue_id, venue_name, weather_note, is_indoor = _resolve_venue(conn, spec, day)
        booth_rev = _collect_booth_fees(conn, booth_ids)
        att, patron_rev = _run_patron_spending(conn, day, seed, season, year, booth_ids)
        total_rev = booth_rev + patron_rev

        cur = conn.execute(
            """INSERT INTO festivals(
                   season, year, name, venue_id, start_day, end_day,
                   status, attendance_count, revenue_cents, weather_note, summary)
               VALUES(?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
            (
                season, year, name, venue_id, start_day, end_day,
                att, total_rev, weather_note,
                f"{name} opened at {venue_name}.",
            ),
        )
        fid = int(cur.lastrowid or 0)
        emit(
            conn, tick, "festival_opened", place_id=venue_id, importance=NOTABLE,
            festival_id=fid, name=name, season=season, year=year,
            indoor_shelter=is_indoor, weather_note=weather_note,
            text=f"{name} opened at {venue_name} ({weather_note})",
        )
        if not sched["is_end"]:
            return {
                "active": True,
                "action": "opened",
                "festival_id": fid,
                "name": name,
                "venue_id": venue_id,
                "indoor_shelter": is_indoor,
                "attendance_count": att,
                "revenue_cents": total_rev,
            }
        existing = conn.execute("SELECT * FROM festivals WHERE id = ?", (fid,)).fetchone()

    # Already opened on start_day, and today is still start_day -> idempotent no-op
    if not sched["is_end"]:
        return {
            "active": True,
            "action": "already_opened",
            "festival_id": int(existing["id"]),
        }

    # Closing on end_day
    fid = int(existing["id"])
    venue_id, venue_name, weather_note, is_indoor = _resolve_venue(conn, spec, day)
    att2, patron_rev2 = _run_patron_spending(conn, day, seed, season, year, booth_ids)
    total_att = int(existing["attendance_count"]) + att2
    total_rev = int(existing["revenue_cents"]) + patron_rev2

    winner_agent_id, winner_agent_name = _select_winner_agent(
        conn, seed, year, season, spec["keywords"]
    )
    winner_business_id, winner_business_name = _select_winner_business(
        conn, seed, year, season, booth_ids
    )
    prize_paid = _award_prize_pool(conn, winner_agent_id, winner_business_id)

    biz_note = f" Best Booth: {winner_business_name}." if winner_business_name else ""
    summary = (
        f"{name} concluded at {venue_name} with {total_att} attendees, "
        f"${total_rev / 100:,.2f} in fairground activity, and champion "
        f"{winner_agent_name}.{biz_note}"
    )

    conn.execute(
        """UPDATE festivals
           SET venue_id = ?,
               status = 'closed',
               attendance_count = ?,
               revenue_cents = ?,
               winner_agent_id = ?,
               winner_business_id = ?,
               weather_note = ?,
               summary = ?
           WHERE id = ?""",
        (
            venue_id, total_att, total_rev,
            winner_agent_id, winner_business_id,
            weather_note, summary, fid,
        ),
    )

    emit(
        conn, tick, "festival_winner", place_id=venue_id, a=winner_agent_id,
        importance=NOTABLE, festival_id=fid, name=name,
        winner_business_id=winner_business_id, prize_cents=prize_paid,
        text=f"{winner_agent_name} won the {name} competition",
    )
    emit(
        conn, tick, "festival_closed", place_id=venue_id, a=winner_agent_id,
        importance=NOTABLE, festival_id=fid, name=name,
        attendance_count=total_att, revenue_cents=total_rev,
        summary=summary, text=summary,
    )

    return {
        "active": False,
        "action": "closed",
        "festival_id": fid,
        "name": name,
        "venue_id": venue_id,
        "indoor_shelter": is_indoor,
        "attendance_count": total_att,
        "revenue_cents": total_rev,
        "winner_agent_id": winner_agent_id,
        "winner_business_id": winner_business_id,
        "prize_cents": prize_paid,
        "summary": summary,
    }


def active_festival(conn: sqlite3.Connection, day: int) -> dict[str, Any] | None:
    """Return the currently active festival covering `day`, if any."""
    try:
        row = conn.execute(
            """SELECT f.*,
                      p.name AS venue_name,
                      a.name AS winner_agent_name,
                      wb.name AS winner_business_name
               FROM festivals f
               LEFT JOIN places p ON p.id = f.venue_id
               LEFT JOIN agents a ON a.id = f.winner_agent_id
               LEFT JOIN places wb ON wb.id = f.winner_business_id
               WHERE f.status = 'active' AND f.start_day <= ? AND f.end_day >= ?
               ORDER BY f.id DESC LIMIT 1""",
            (int(day), int(day)),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def list_festivals(conn: sqlite3.Connection, limit: int = 12) -> list[dict[str, Any]]:
    """Return recent and active festivals ordered newest-first."""
    try:
        rows = conn.execute(
            """SELECT f.*,
                      p.name AS venue_name,
                      a.name AS winner_agent_name,
                      wb.name AS winner_business_name
               FROM festivals f
               LEFT JOIN places p ON p.id = f.venue_id
               LEFT JOIN agents a ON a.id = f.winner_agent_id
               LEFT JOIN places wb ON wb.id = f.winner_business_id
               ORDER BY f.start_day DESC, f.id DESC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]
