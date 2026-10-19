"""Seasonal Harvest Cellars & Farmers' Co-op (v0.34).

Gardeners, bakers, and home cooks bring seasonal produce and pantry goods to
the **Miniville Farmers' Co-op** (`harvest_coops` table):

* **Seasonal Co-op Boxes (`coop_market_box`)** — growers sell fresh seasonal
  boxes (*Spring maple syrup & early greens*, *Summer heirloom tomatoes & berry
  jam*, *Autumn orchard cider & squash*, *Winter root-cellar jars & sourdough*)
  to well-off neighbours (`money_cents >= 220_000`) for a strictly conserved
  `$14–$28` wallet-to-wallet transfer, reducing the buyer's hunger (`-15`) and
  lifting fun (`+5`).
* **Harvest Pantry Sharing (`harvest_basket_shared`)** — growers also share
  surplus harvest baskets with hungry or low-cash households (`hunger >= 40` or
  `money_cents < 150_000`), cutting hunger (`-22`) and warming neighbour
  affinity (`+5.0`).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, emit
from .memory import remember
from .rng import rng_for
from .seasons import season_of
from .timekeeper import day_of

COOP_BOX_MIN_CENTS = 1_400   # $14
COOP_BOX_MAX_CENTS = 2_800   # $28

_SEASON_PRODUCE: dict[str, tuple[str, ...]] = {
    "spring": (
        "a crate of early garden greens & maple syrup",
        "a basket of rhubarb tarts & wildflower honey",
        "a bundle of heirloom herb seedlings & radishes",
    ),
    "summer": (
        "a crate of sun-ripened heirloom tomatoes & sweet corn",
        "a basket of blackberry preserves & orchard peaches",
        "a box of garden zucchini, basil & porch lemonade",
    ),
    "autumn": (
        "a jug of pressed orchard cider & roasted butternut squash",
        "a basket of honeycrisp apples & spiced pumpkin loaves",
        "a crate of cellar potatoes, onions & apple butter",
    ),
    "winter": (
        "a root-cellar box of pickled beets, jams & sourdough bread",
        "a basket of dried orchard fruit, walnuts & rosemary loaves",
        "a pantry crate of winter squash & simmered broth jars",
    ),
}


def run_coop_day(conn: sqlite3.Connection, tick: int,
                 seed: str = "miniville") -> dict[str, int]:
    """Run Farmers' Co-op box trades (conserved) and surplus harvest sharing."""
    day = day_of(tick)
    if day % 3 != 2:
        return {"boxes_sold": 0, "baskets_shared": 0, "volume_cents": 0}

    r = rng_for(seed, "harvest_coop", day)
    season = season_of(day)
    items = _SEASON_PRODUCE.get(season, _SEASON_PRODUCE["summer"])

    venue = conn.execute(
        """SELECT id, name FROM places
           WHERE name IN ('Green Grocer', 'Miniville Community Center', 'Lush Meadow Park')
           ORDER BY id ASC LIMIT 1"""
    ).fetchone()
    venue_id = int(venue["id"]) if venue else None
    venue_name = venue["name"] if venue else "the Farmers' Co-op"

    growers = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (a.hobbies_json LIKE '%garden%' OR a.hobbies_json LIKE '%bak%'
                  OR a.hobbies_json LIKE '%cook%' OR a.skills_json LIKE '%garden%'
                  OR a.skills_json LIKE '%bak%')
           ORDER BY a.id ASC LIMIT 30"""
    ).fetchall()
    if not growers:
        growers = conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0 ORDER BY a.id ASC LIMIT 15"""
        ).fetchall()
    if not growers:
        return {"boxes_sold": 0, "baskets_shared": 0, "volume_cents": 0}

    boxes_sold = baskets_shared = volume_cents = 0

    grower = r.choice(growers)
    gid = int(grower["id"])
    buyers = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND a.id != ?
             AND s.money_cents >= 220000
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 25""",
        (gid,),
    ).fetchall()
    if buyers:
        buyer = r.choice(buyers)
        bid = int(buyer["id"])
        price = r.randint(COOP_BOX_MIN_CENTS, COOP_BOX_MAX_CENTS)
        produce = r.choice(items)

        conn.execute(
            """UPDATE agent_state
               SET money_cents = money_cents - ?,
                   hunger = MAX(0, hunger - 15),
                   fun = MIN(100, fun + 5)
               WHERE agent_id = ?""",
            (price, bid),
        )
        conn.execute(
            """UPDATE agent_state
               SET money_cents = money_cents + ?,
                   fun = MIN(100, fun + 4)
               WHERE agent_id = ?""",
            (price, gid),
        )
        conn.execute(
            """INSERT INTO harvest_coops(
                   day, tick, season, grower_id, recipient_id,
                   produce_item, kind, price_cents)
               VALUES(?, ?, ?, ?, ?, ?, 'sale', ?)""",
            (day, tick, season, gid, bid, produce, price),
        )
        text = (
            f"picked up {produce} from {grower['name']}'s co-op table "
            f"at {venue_name} (${price / 100:,.0f})"
        )
        emit(conn, tick, "life_event", place_id=venue_id, a=bid, b=gid,
             importance=MINOR, text=text, tag="coop_market_box",
             season=season, price_cents=price)
        remember(conn, bid, tick,
                 f"Bought {produce} from {grower['name']} at {venue_name}.",
                 kind="personal", importance=2)
        boxes_sold += 1
        volume_cents += price

    needy = conn.execute(
        """SELECT a.id, a.name, s.hunger, s.money_cents
           FROM agents a JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND a.id != ?
             AND (s.hunger >= 35 OR s.money_cents < 160000)
           ORDER BY s.hunger DESC, s.money_cents ASC, a.id ASC LIMIT 15""",
        (gid,),
    ).fetchall()
    if needy:
        rec = r.choice(needy)
        rid = int(rec["id"])
        produce = r.choice(items)
        conn.execute(
            """UPDATE agent_state
               SET hunger = MAX(0, hunger - 22),
                   stress = MAX(0, stress - 6),
                   social = MIN(100, social + 5)
               WHERE agent_id = ?""",
            (rid,),
        )
        conn.execute(
            """INSERT INTO harvest_coops(
                   day, tick, season, grower_id, recipient_id,
                   produce_item, kind, price_cents)
               VALUES(?, ?, ?, ?, ?, ?, 'shared', 0)""",
            (day, tick, season, gid, rid, produce),
        )
        lo, hi = min(gid, rid), max(gid, rid)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,6.0,9.0,0.0,'friend',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.5),
                 affinity = MIN(100.0, relationships.affinity + 5.0),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        text = f"shared {produce} with {rec['name']} from the {season} harvest pantry"
        emit(conn, tick, "life_event", place_id=venue_id, a=gid, b=rid,
             importance=MINOR, text=text, tag="harvest_basket_shared",
             season=season)
        remember(conn, rid, tick,
                 f"{grower['name']} shared {produce} with our household.",
                 kind="personal", importance=3)
        baskets_shared += 1

    return {
        "boxes_sold": boxes_sold,
        "baskets_shared": baskets_shared,
        "volume_cents": volume_cents,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    return run_coop_day(conn, tick, seed)


def harvest_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    sales = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(price_cents), 0) AS vol
           FROM harvest_coops WHERE kind = 'sale'"""
    ).fetchone()
    shared = conn.execute(
        "SELECT COUNT(*) AS n FROM harvest_coops WHERE kind = 'shared'"
    ).fetchone()
    recent = conn.execute(
        """SELECT h.*, ga.name AS grower_name, ra.name AS recipient_name
           FROM harvest_coops h
           LEFT JOIN agents ga ON ga.id = h.grower_id
           LEFT JOIN agents ra ON ra.id = h.recipient_id
           ORDER BY h.id DESC LIMIT 20"""
    ).fetchall()
    return {
        "boxes_sold": int(sales["n"] or 0),
        "baskets_shared": int(shared["n"] or 0),
        "total_volume_cents": int(sales["vol"] or 0),
        "recent": [dict(r) for r in recent],
    }
