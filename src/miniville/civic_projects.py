"""Civic projects, participatory petitions, and durable town landmarks.

Miniville has a municipal purse (`town_account`), an elected 5-seat district
`council` (`politics.py`), wealthy proprietors (`enterprise.py`), and
community-minded volunteers. This module lets residents and the town build
permanent district improvements (`civic_projects` table):

* **Lakeshore Acoustic Bandstand** (`Lakeshore`, `Lush Meadow Park`)
* **Greenhill Community Greenhouse** (`Greenhill`, `Miniville School`)
* **Old Mill Heritage Clocktower** (`Old Mill Quarter`, `Old Mill Shops`)
* **The Flats Cooperative Tool Library** (`The Flats`, `Miniville Community Center`)
* **Downtown Sculpture & Reading Courtyard** (`Downtown`, `Miniville Public Library`)
* **Lakeshore Promenade & Fishing Pier** (`Lakeshore`, `Lakeshore Marina`)

Strict Money Conservation:
Every philanthropic donation from a wealthy resident (`$25–$60`) or municipal
grant from `town_account` (`$40–$80` when `town_account` has a surplus) is
immediately paid to the local trades contractor (`Flats Auto & Hardware`
business till, or retained in `town_account` if no trades shop is open), so
`total_money_cents` (`wallets + tills + purse`) stays 100% invariant on every
tick while `civic_projects.funded_cents` tracks cumulative construction funding!
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, emit
from .individual import profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

DONATION_MIN = 2_500          # $25 conserved donor contribution
DONATION_MAX = 6_000          # $60 max donor contribution
DONOR_MIN_WALLET = 400_000    # donor needs >= $4,000 in wallet
MUNICIPAL_GRANT = 5_000       # $50 conserved grant paid from town_account to trades contractor
PURSE_GRANT_CUSHION = 250_000 # town purse needs >= $2,500 to issue a grant

PROJECT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": "lakeshore_bandstand",
        "title": "Lakeshore Acoustic Bandstand",
        "district": "Lakeshore",
        "venue": "Lush Meadow Park",
        "target_cents": 35_000,
        "target_hours": 8,
        "capacity_bonus": 20,
        "benefit": "+20 capacity at Lush Meadow Park & +8% Lakeshore cohesion",
    },
    {
        "key": "flats_tool_library",
        "title": "The Flats Cooperative Tool Library",
        "district": "The Flats",
        "venue": "Miniville Community Center",
        "target_cents": 30_000,
        "target_hours": 8,
        "capacity_bonus": 15,
        "benefit": "+15 capacity at Community Center & +10% Flats cohesion",
    },
    {
        "key": "greenhill_greenhouse",
        "title": "Greenhill Community Greenhouse",
        "district": "Greenhill",
        "venue": "Miniville School",
        "target_cents": 35_000,
        "target_hours": 8,
        "capacity_bonus": 15,
        "benefit": "+15 capacity at Miniville School & +8% Greenhill cohesion",
    },
    {
        "key": "old_mill_clocktower",
        "title": "Old Mill Heritage Clocktower & Plaza",
        "district": "Old Mill Quarter",
        "venue": "Old Mill Shops",
        "target_cents": 40_000,
        "target_hours": 10,
        "capacity_bonus": 15,
        "benefit": "+15 capacity at Old Mill Shops & +8% Old Mill cohesion",
    },
    {
        "key": "downtown_reading_garden",
        "title": "Downtown Sculpture & Reading Courtyard",
        "district": "Downtown",
        "venue": "Miniville Public Library",
        "target_cents": 35_000,
        "target_hours": 8,
        "capacity_bonus": 15,
        "benefit": "+15 capacity at Public Library & +8% Downtown cohesion",
    },
    {
        "key": "marina_boardwalk",
        "title": "Lakeshore Promenade & Fishing Pier",
        "district": "Lakeshore",
        "venue": "Lakeshore Marina",
        "target_cents": 40_000,
        "target_hours": 10,
        "capacity_bonus": 20,
        "benefit": "+20 capacity at Lakeshore Marina & +8% Lakeshore cohesion",
    },
)


def ensure_projects(conn: sqlite3.Connection) -> int:
    """Seed the `civic_projects` catalog if not already present."""
    existing = {
        r["key"]
        for r in conn.execute("SELECT key FROM civic_projects").fetchall()
    }
    venues = {
        r["name"]: int(r["id"])
        for r in conn.execute("SELECT id, name FROM places WHERE kind != 'home'").fetchall()
    }
    added = 0
    for idx, spec in enumerate(PROJECT_CATALOG):
        if spec["key"] in existing:
            continue
        vid = venues.get(spec["venue"])
        status = "active" if idx == 0 and not existing else "proposed"
        conn.execute(
            """INSERT INTO civic_projects(
                   key, title, district, venue_id, sponsor_id,
                   target_cents, funded_cents, volunteer_hours,
                   target_hours, status, started_tick, benefit)
               VALUES(?, ?, ?, ?, NULL, ?, 0, 0, ?, ?, 0, ?)""",
            (
                spec["key"], spec["title"], spec["district"], vid,
                spec["target_cents"], spec["target_hours"], status,
                spec["benefit"],
            ),
        )
        added += 1
    return added


def _contractor_place_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """SELECT p.id FROM places p
           JOIN businesses b ON b.place_id = p.id
           WHERE b.status = 'open' AND p.tags LIKE '%"trades"%'
           ORDER BY p.id LIMIT 1"""
    ).fetchone()
    if row:
        return int(row["id"])
    row2 = conn.execute(
        "SELECT place_id FROM businesses WHERE status = 'open' ORDER BY place_id LIMIT 1"
    ).fetchone()
    return int(row2["place_id"]) if row2 else None


def _activate_next_project(conn: sqlite3.Connection, tick: int) -> sqlite3.Row | None:
    active = conn.execute(
        "SELECT * FROM civic_projects WHERE status = 'active' ORDER BY id LIMIT 1"
    ).fetchone()
    if active:
        return active
    nxt = conn.execute(
        "SELECT * FROM civic_projects WHERE status = 'proposed' ORDER BY id LIMIT 1"
    ).fetchone()
    if not nxt:
        return None
    sponsor = conn.execute(
        """SELECT a.id, a.name FROM agents a
           LEFT JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (p.district = ? OR COALESCE(a.standing, 0) >= 8)
           ORDER BY COALESCE(a.standing, 0) DESC, a.id ASC LIMIT 1""",
        (nxt["district"],),
    ).fetchone()
    sid = int(sponsor["id"]) if sponsor else None
    conn.execute(
        """UPDATE civic_projects
           SET status = 'active', sponsor_id = ?, started_tick = ?
           WHERE id = ?""",
        (sid, tick, nxt["id"]),
    )
    if sid and sponsor:
        emit(conn, tick, "life_event", place_id=nxt["venue_id"], a=sid,
             importance=MINOR,
             text=f"launched a civic petition for the {nxt['title']} in {nxt['district']}",
             tag="civic_petition", project_key=nxt["key"])
    return conn.execute(
        "SELECT * FROM civic_projects WHERE id = ?", (nxt["id"],)
    ).fetchone()


def step_active_project(conn: sqlite3.Connection, tick: int,
                        seed: str = "miniville") -> dict[str, int]:
    """Advance funding and volunteer hours on the active civic project."""
    ensure_projects(conn)
    proj = _activate_next_project(conn, tick)
    if not proj:
        return {"donated_cents": 0, "volunteered": 0, "completed": 0}

    day = day_of(tick)
    r = rng_for(seed, "civic_proj", day)
    pid = int(proj["id"])
    funded = int(proj["funded_cents"] or 0)
    target_c = int(proj["target_cents"] or 35_000)
    hours = int(proj["volunteer_hours"] or 0)
    target_h = int(proj["target_hours"] or 8)
    donated = volunteered = completed = 0
    contractor_id = _contractor_place_id(conn)

    # 1. Philanthropic donation from a wealthy resident (conserved wallet -> contractor till)
    if funded < target_c and r.random() < 0.55:
        champions = [
            int(x["agent_id"])
            for x in conn.execute(
                "SELECT agent_id FROM ambitions WHERE status='active' AND kind='champion_civic_project'"
            ).fetchall()
        ]
        donors = conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents >= ?
               ORDER BY s.money_cents DESC, a.id ASC LIMIT 10""",
            (DONOR_MIN_WALLET,),
        ).fetchall()
        if donors:
            # Prefer a donor who holds the champion_civic_project ambition
            champ_donors = [d for d in donors if int(d["id"]) in champions]
            donor = champ_donors[0] if champ_donors else r.choice(donors)
            did = int(donor["id"])
            amt = min(target_c - funded, r.randint(DONATION_MIN, DONATION_MAX))
            if amt > 0:
                conn.execute(
                    "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                    (amt, did),
                )
                if contractor_id is not None:
                    conn.execute(
                        "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
                        (amt, contractor_id),
                    )
                else:
                    conn.execute(
                        "UPDATE town_account SET balance_cents = balance_cents + ? WHERE id = 1",
                        (amt,),
                    )
                funded += amt
                donated += amt
                conn.execute(
                    "UPDATE civic_projects SET funded_cents = ? WHERE id = ?",
                    (funded, pid),
                )
                emit(conn, tick, "life_event", place_id=proj["venue_id"], a=did,
                     importance=MINOR,
                     text=f"donated ${amt / 100:,.0f} toward the {proj['title']}",
                     tag="civic_donation", project_key=proj["key"], amount_cents=amt)

    # 2. Municipal matching grant from town_account when purse is comfortable
    if funded < target_c:
        purse = conn.execute("SELECT balance_cents FROM town_account WHERE id = 1").fetchone()
        if purse and int(purse["balance_cents"] or 0) >= PURSE_GRANT_CUSHION and contractor_id is not None:
            grant = min(target_c - funded, MUNICIPAL_GRANT)
            if grant > 0:
                conn.execute(
                    "UPDATE town_account SET balance_cents = balance_cents - ? WHERE id = 1",
                    (grant,),
                )
                conn.execute(
                    "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
                    (grant, contractor_id),
                )
                funded += grant
                donated += grant
                conn.execute(
                    "UPDATE civic_projects SET funded_cents = ? WHERE id = ?",
                    (funded, pid),
                )

    # 3. Volunteer labour shift from industrious/community-minded residents
    if hours < target_h and r.random() < 0.65:
        vols = conn.execute(
            """SELECT a.id, a.name FROM agents a
               WHERE a.alive = 1 AND a.is_child = 0
               ORDER BY COALESCE(a.standing, 0) DESC, a.id ASC LIMIT 20"""
        ).fetchall()
        if vols:
            v = r.choice(vols)
            vid = int(v["id"])
            hours += 1
            volunteered += 1
            conn.execute(
                "UPDATE civic_projects SET volunteer_hours = ? WHERE id = ?",
                (hours, pid),
            )
            conn.execute(
                """UPDATE agent_state
                   SET social = MIN(100, social + 6), fun = MIN(100, fun + 5)
                   WHERE agent_id = ?""",
                (vid,),
            )
            emit(conn, tick, "life_event", place_id=proj["venue_id"], a=vid,
                 importance=MINOR,
                 text=f"volunteered a construction shift on the {proj['title']} ({hours}/{target_h} hrs)",
                 tag="civic_volunteer", project_key=proj["key"])

    # 4. Ribbon-cutting completion check
    if funded >= target_c and hours >= target_h:
        conn.execute(
            """UPDATE civic_projects
               SET status = 'completed', completed_tick = ?
               WHERE id = ?""",
            (tick, pid),
        )
        spec_map = {s["key"]: s for s in PROJECT_CATALOG}
        cap_bonus = int(spec_map.get(proj["key"], {}).get("capacity_bonus", 15))
        if proj["venue_id"] is not None:
            conn.execute(
                "UPDATE places SET capacity = capacity + ? WHERE id = ?",
                (cap_bonus, proj["venue_id"]),
            )
        conn.execute(
            """UPDATE district_watch
               SET cohesion = MIN(1.0, ROUND(cohesion + 0.08, 3)),
                   last_event_day = ?, last_event_kind = 'civic_landmark'
               WHERE district = ?""",
            (day, proj["district"]),
        )
        lead_id = proj["sponsor_id"]
        emit(conn, tick, "life_event", place_id=proj["venue_id"], a=lead_id,
             importance=NOTABLE,
             text=f"cut the ribbon on the completed {proj['title']} in {proj['district']} ({proj['benefit']})",
             tag="civic_project_completed", project_key=proj["key"])
        if lead_id:
            remember(conn, int(lead_id), tick,
                     f"We completed the {proj['title']} in {proj['district']}!",
                     kind="reflection", importance=4)
        completed = 1

    return {"donated_cents": donated, "volunteered": volunteered, "completed": completed}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    return step_active_project(conn, tick, seed)


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_projects(conn)
    rows = conn.execute(
        """SELECT cp.*, p.name AS venue_name, a.name AS sponsor_name
           FROM civic_projects cp
           LEFT JOIN places p ON p.id = cp.venue_id
           LEFT JOIN agents a ON a.id = cp.sponsor_id
           ORDER BY CASE cp.status
               WHEN 'active' THEN 0
               WHEN 'completed' THEN 1
               ELSE 2 END, cp.id ASC"""
    ).fetchall()
    return [dict(r) for r in rows]
