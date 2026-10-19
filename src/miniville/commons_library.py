"""Neighborhood Tool Library, Heirloom Seed Bank & Community Orchard (v0.41).

Every 3 days (`day % 3 == 1`), volunteer stewards at `Flats Auto & Hardware`,
`Miniville Community Center`, and `Lush Meadow Park` open the **Miniville
Commons Tool Library, Seed Bank & Community Orchard** (`tool_library_loans`
table):

* **Tool Loans & Seed Swaps (`tool_library_loan`)** — neighbours borrow shared
  woodworking/repair tools or heirloom seed packets at zero cost, boosting
  `fun` (`+5`) and warming bond affinity (`+4.5`) with the volunteer steward.
* **Community Orchard Gleaning (`orchard_gleaning`)** — households with high
  hunger gather fresh orchard fruit & nuts, reducing `hunger` by `-18`.
* **Conserved Patron Tool Endowment (`tool_library_donation`)** — a comfortable
  patron (`money_cents >= 260_000`) sponsors the volunteer steward's workbench
  with a strictly conserved `$18–$30` wallet-to-wallet stipend.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, emit
from .memory import remember
from .rng import rng_for
from .seasons import season_of
from .timekeeper import day_of

ENDOWMENT_MIN_CENTS = 1_800   # $18 conserved tool-bench stipend
ENDOWMENT_MAX_CENTS = 3_000   # $30 conserved tool-bench stipend

_COMMONS_ITEMS: tuple[tuple[str, str], ...] = (
    ("tool", "a dovetail woodworking plane & chisel set"),
    ("tool", "a heavy-duty apple cider press & canning kettle"),
    ("tool", "a post-hole auger & garden wheelbarrow"),
    ("seeds", "packets of heirloom Brandywine tomato & sweet pea seeds"),
    ("seeds", "a jar of saved winter squash & sunflower seeds"),
    ("orchard", "a peck of Community Orchard honeycrisp apples & pears"),
)


def run_commons_library(conn: sqlite3.Connection, tick: int,
                        seed: str = "miniville") -> dict[str, int]:
    """Run Tool Library loans, Seed Bank swaps, and Community Orchard gleaning."""
    day = day_of(tick)
    if day % 3 != 1:
        return {"loans": 0, "endowment_cents": 0}

    r = rng_for(seed, "commons_library", day)
    season = season_of(day)
    venue = conn.execute(
        """SELECT id, name FROM places
           WHERE name IN ('Flats Auto & Hardware', 'Lush Meadow Park', 'Miniville Community Center')
           ORDER BY id ASC LIMIT 1"""
    ).fetchone()
    venue_id = int(venue["id"]) if venue else None
    venue_name = venue["name"] if venue else "the Community Tool Library"

    stewards = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (a.skills_json LIKE '%carpent%' OR a.skills_json LIKE '%repair%'
                  OR a.hobbies_json LIKE '%garden%' OR a.hobbies_json LIKE '%wood%')
           ORDER BY a.id ASC LIMIT 25"""
    ).fetchall()
    if not stewards:
        stewards = conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0 ORDER BY a.id ASC LIMIT 10"""
        ).fetchall()
    if not stewards:
        return {"loans": 0, "endowment_cents": 0}

    steward = r.choice(stewards)
    sid = int(steward["id"])

    borrowers = conn.execute(
        """SELECT a.id, a.name, s.hunger, s.money_cents
           FROM agents a JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND a.id != ?
           ORDER BY s.hunger DESC, s.money_cents ASC, a.id ASC LIMIT 20""",
        (sid,),
    ).fetchall()
    if not borrowers:
        return {"loans": 0, "endowment_cents": 0}

    borrower = borrowers[0] if int(borrowers[0]["hunger"] or 0) >= 35 else r.choice(borrowers)
    bid = int(borrower["id"])
    item_kind, item_name = (
        ("orchard", "a basket of Community Orchard fruit & walnuts")
        if int(borrower["hunger"] or 0) >= 35
        else r.choice(_COMMONS_ITEMS)
    )

    conn.execute(
        """UPDATE agent_state
           SET hunger = MAX(0, hunger - ?),
               fun = MIN(100, fun + 5),
               social = MIN(100, social + 4),
               stress = MAX(0, stress - 4)
           WHERE agent_id = ?""",
        (18 if item_kind == "orchard" else 4, bid),
    )

    # Optional conserved endowment from a rich patron to the volunteer steward
    endowment_cents = 0
    patrons = conn.execute(
        """SELECT a.id, s.money_cents FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND a.id NOT IN (?, ?)
             AND s.money_cents >= 260000
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 5""",
        (sid, bid),
    ).fetchall()
    if patrons:
        pat_id = int(patrons[0]["id"])
        endowment_cents = r.randint(ENDOWMENT_MIN_CENTS, ENDOWMENT_MAX_CENTS)
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
            (endowment_cents, pat_id),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (endowment_cents, sid),
        )

    conn.execute(
        "UPDATE agents SET standing = COALESCE(standing, 0) + 1 WHERE id = ?",
        (sid,),
    )
    conn.execute(
        """INSERT INTO tool_library_loans(
               day, tick, season, steward_id, borrower_id,
               item_kind, item_name, endowment_cents)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, season, sid, bid, item_kind, item_name, endowment_cents),
    )

    lo, hi = min(sid, bid), max(sid, bid)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,6.0,8.0,0.0,'friend',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 4.0),
             affinity = MIN(100.0, relationships.affinity + 4.5),
             interactions = relationships.interactions + 1,
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )

    tag = "orchard_gleaning" if item_kind == "orchard" else "tool_library_loan"
    text = f"borrowed {item_name} from steward {steward['name']} at {venue_name}"
    emit(conn, tick, "life_event", place_id=venue_id, a=bid, b=sid,
         importance=MINOR, text=text, tag=tag,
         item_kind=item_kind, endowment_cents=endowment_cents)
    remember(conn, bid, tick,
             f"Checked out {item_name} with {steward['name']}'s help at {venue_name}.",
             kind="personal", importance=2)
    return {"loans": 1, "endowment_cents": endowment_cents}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    return run_commons_library(conn, tick, seed)


def commons_library_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tot = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(endowment_cents), 0) AS endw
           FROM tool_library_loans"""
    ).fetchone()
    rows = conn.execute(
        """SELECT t.*, sa.name AS steward_name, ba.name AS borrower_name
           FROM tool_library_loans t
           LEFT JOIN agents sa ON sa.id = t.steward_id
           LEFT JOIN agents ba ON ba.id = t.borrower_id
           ORDER BY t.id DESC LIMIT 20"""
    ).fetchall()
    return {
        "total_loans": int(tot["n"] or 0),
        "total_endowment_cents": int(tot["endw"] or 0),
        "recent": [dict(r) for r in rows],
    }
