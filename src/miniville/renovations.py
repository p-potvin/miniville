"""Home Renovations, Front-Porch Additions & Heirloom Gardens (v0.38).

Prosperous households invest in permanent home upgrades (`home_renovations`
table) by hiring a local carpenter or builder:

* **Conserved Craftsman Contracting (`run_renovations`)** — every 5 days, a
  thriving household (`money_cents >= 380_000`) commissions one of 5 upgrades:
  1. `wraparound_porch` (*Wrap-Around Cedar Front Porch*)
  2. `heirloom_garden` (*Fenced Heirloom Kitchen Garden & Arbor*)
  3. `sunroom_addition` (*South-Facing Conservatory & Sunroom*)
  4. `attic_studio` (*Skylit Attic Craft & Writing Studio*)
  5. `hearth_remodel` (*Fieldstone Hearth & Farmhouse Kitchen*)
* **Strict Money Conservation & Capacity Upgrade** — the homeowner pays `$65–$120`
  (`6,500–12,000` cents) directly wallet-to-wallet to a living carpenter/builder
  resident (`home_renovation` event), permanently increasing their home's
  `places.capacity` by `+1` and lifting household morale!
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

RENOVATION_MIN_CENTS = 6_500    # $65 conserved payment to local builder
RENOVATION_MAX_CENTS = 12_000   # $120 conserved payment to local builder
HOMEOWNER_MIN_WALLET = 380_000

_UPGRADES: tuple[tuple[str, str], ...] = (
    ("wraparound_porch", "a wrap-around cedar front porch"),
    ("heirloom_garden", "a fenced heirloom kitchen garden & arbor"),
    ("sunroom_addition", "a south-facing conservatory & sunroom"),
    ("attic_studio", "a skylit attic craft & writing studio"),
    ("hearth_remodel", "a fieldstone hearth & farmhouse kitchen"),
)


def run_renovations(conn: sqlite3.Connection, tick: int,
                    seed: str = "miniville") -> dict[str, int]:
    """Commission a conserved home renovation for a prosperous household."""
    day = day_of(tick)
    if day % 5 != 3:
        return {"renovations": 0, "spent_cents": 0}

    r = rng_for(seed, "home_renovation", day)
    homeowners = conn.execute(
        """SELECT a.id AS aid, a.name, a.household_id, a.home_place_id,
                  h.name AS hname, s.money_cents
           FROM agents a
           JOIN households h ON h.id = a.household_id
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND a.home_place_id IS NOT NULL
             AND s.money_cents >= ?
             AND (SELECT COUNT(*) FROM home_renovations hr WHERE hr.household_id = a.household_id) < 2
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 20""",
        (HOMEOWNER_MIN_WALLET,),
    ).fetchall()
    if not homeowners:
        return {"renovations": 0, "spent_cents": 0}

    owner = r.choice(homeowners)
    oid = int(owner["aid"])
    hid = int(owner["household_id"])
    home_pid = int(owner["home_place_id"])

    builders = conn.execute(
        """SELECT a.id, a.name FROM agents a
           WHERE a.alive = 1 AND a.is_child = 0 AND a.id != ?
             AND (a.skills_json LIKE '%carpent%' OR a.skills_json LIKE '%wood%'
                  OR a.skills_json LIKE '%repair%' OR a.occupation LIKE '%carpent%'
                  OR a.occupation LIKE '%construct%')
           ORDER BY a.id ASC LIMIT 20""",
        (oid,),
    ).fetchall()
    if not builders:
        builders = conn.execute(
            """SELECT id, name FROM agents
               WHERE alive = 1 AND is_child = 0 AND id != ?
               ORDER BY id ASC LIMIT 10""",
            (oid,),
        ).fetchall()
    if not builders:
        return {"renovations": 0, "spent_cents": 0}

    builder = r.choice(builders)
    bid = int(builder["id"])
    cost = r.randint(RENOVATION_MIN_CENTS, RENOVATION_MAX_CENTS)
    kind, title = r.choice(_UPGRADES)

    # Strictly conserved wallet-to-wallet contractor payment
    conn.execute(
        "UPDATE agent_state SET money_cents = money_cents - ?, fun = MIN(100, fun + 10), stress = MAX(0, stress - 6) WHERE agent_id = ?",
        (cost, oid),
    )
    conn.execute(
        "UPDATE agent_state SET money_cents = money_cents + ?, fun = MIN(100, fun + 5) WHERE agent_id = ?",
        (cost, bid),
    )
    conn.execute(
        "UPDATE places SET capacity = COALESCE(capacity, 6) + 1 WHERE id = ?",
        (home_pid,),
    )
    conn.execute(
        """INSERT INTO home_renovations(
               day, tick, household_id, home_place_id, builder_id,
               upgrade_kind, title, cost_cents)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, hid, home_pid, bid, kind, title, cost),
    )

    lo, hi = min(oid, bid), max(oid, bid)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,7.0,8.0,0.0,'friend',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 3.5),
             affinity = MIN(100.0, relationships.affinity + 4.5),
             interactions = relationships.interactions + 1,
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )

    text = (
        f"hired {builder['name']} (${cost / 100:,.0f}) to build {title} "
        f"for the {owner['hname']}"
    )
    emit(conn, tick, "life_event", place_id=home_pid, a=oid, b=bid,
         importance=NOTABLE, text=text, tag="home_renovation",
         upgrade_kind=kind, cost_cents=cost)
    remember(conn, oid, tick,
             f"{builder['name']} finished building {title} on our home!",
             kind="reflection", importance=4)
    remember(conn, bid, tick,
             f"Built {title} for {owner['name']} (${cost / 100:,.0f}).",
             kind="personal", importance=3)
    return {"renovations": 1, "spent_cents": cost}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    return run_renovations(conn, tick, seed)


def renovations_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tot = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(cost_cents), 0) AS vol
           FROM home_renovations"""
    ).fetchone()
    rows = conn.execute(
        """SELECT hr.*, h.name AS household_name, ba.name AS builder_name,
                  p.district
           FROM home_renovations hr
           LEFT JOIN households h ON h.id = hr.household_id
           LEFT JOIN agents ba ON ba.id = hr.builder_id
           LEFT JOIN places p ON p.id = hr.home_place_id
           ORDER BY hr.id DESC LIMIT 20"""
    ).fetchall()
    return {
        "total_renovations": int(tot["n"] or 0),
        "total_spent_cents": int(tot["vol"] or 0),
        "recent": [dict(r) for r in rows],
    }
