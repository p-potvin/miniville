"""Weekend Flea Market, secondhand keepsake trade, and yard sales (v0.29).

On Saturdays and Sundays (`day % 7 in (5, 6)`), residents set up tables at
`Lush Meadow Park` or on their front lawns:

* **Artisan & Secondhand Keepsake Sales** — residents who own 2+ keepsakes
  (or cash-strapped households looking to raise pocket money) offer a piece at
  the weekend flea market. Comfortable neighbours (`money_cents >= 250_000`)
  browse and purchase items for a strictly conserved `$18–$65` wallet-to-wallet
  transfer (`flea_market_sale`), transferring `keepsakes.owner_id` (and clearing
  `venue_id` if on loan) while appending a `"purchased"` provenance entry to
  `keepsakes.history_json`.
* **Vintage Yard-Sale Finds** — when a seller has no spare keepsake, they can
  sell a vintage household curio (creating a new keepsake in the buyer's hands)
  for the same conserved wallet-to-wallet transfer.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .events import MINOR, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

MIN_BUYER_WALLET_CENTS = 250_000
SALE_PRICE_MIN_CENTS = 1_800   # $18
SALE_PRICE_MAX_CENTS = 6_500   # $65

_VINTAGE_FINDS: tuple[tuple[str, str, str], ...] = (
    ("craft", "a brass porch lantern", "woodworking"),
    ("art", "a framed watercolour of the riverbend", "painting"),
    ("book", "a clothbound local history almanac", "history"),
    ("instrument", "a well-seasoned parlor ukulele", "music"),
    ("recipe_box", "a tin of hand-inked comfort recipes", "baking"),
    ("trophy", "a vintage cast-iron chess set", "chess"),
)


def run_weekend_market(conn: sqlite3.Connection, tick: int,
                       seed: str = "miniville") -> dict[str, int]:
    """Run weekend flea-market and yard-sale trades (strictly conserved)."""
    day = day_of(tick)
    if day % 7 not in (5, 6):
        return {"trades": 0, "volume_cents": 0}

    r = rng_for(seed, "flea_market", day)
    park = conn.execute(
        "SELECT id, name FROM places WHERE name = 'Lush Meadow Park' LIMIT 1"
    ).fetchone()
    venue_id = int(park["id"]) if park else None
    venue_name = park["name"] if park else "the weekend flea market"

    # Candidate sellers: residents with 2+ keepsakes OR low-cash adults
    multi_owners = conn.execute(
        """SELECT k.owner_id AS aid, a.name, s.money_cents, COUNT(k.id) AS k_cnt
           FROM keepsakes k
           JOIN agents a ON a.id = k.owner_id
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
           GROUP BY k.owner_id
           HAVING COUNT(k.id) >= 2
           ORDER BY COUNT(k.id) DESC, s.money_cents ASC, k.owner_id ASC"""
    ).fetchall()

    low_cash = conn.execute(
        """SELECT a.id AS aid, a.name, s.money_cents, 0 AS k_cnt
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents < 180000
           ORDER BY s.money_cents ASC, a.id ASC LIMIT 15"""
    ).fetchall()

    sellers_by_id: dict[int, sqlite3.Row] = {}
    for row in list(multi_owners) + list(low_cash):
        sellers_by_id.setdefault(int(row["aid"]), row)
    sellers = list(sellers_by_id.values())
    if not sellers:
        return {"trades": 0, "volume_cents": 0}

    buyers = conn.execute(
        """SELECT a.id AS aid, a.name, a.household_id, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents >= ?
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 40""",
        (MIN_BUYER_WALLET_CENTS,),
    ).fetchall()
    if not buyers:
        return {"trades": 0, "volume_cents": 0}

    max_trades = min(3, len(sellers), len(buyers))
    chosen_sellers = r.sample(sellers, max_trades)
    used_buyers: set[int] = set()
    trades = 0
    volume_cents = 0

    for seller in chosen_sellers:
        sid = int(seller["aid"])
        avail_buyers = [
            b for b in buyers
            if int(b["aid"]) != sid and int(b["aid"]) not in used_buyers
        ]
        if not avail_buyers:
            break
        buyer = r.choice(avail_buyers)
        bid = int(buyer["aid"])
        price = r.randint(SALE_PRICE_MIN_CENTS, SALE_PRICE_MAX_CENTS)

        # Verify buyer still has enough cash
        b_wallet = conn.execute(
            "SELECT money_cents FROM agent_state WHERE agent_id = ?", (bid,)
        ).fetchone()
        if not b_wallet or int(b_wallet["money_cents"]) < price:
            continue

        # Check if seller has a spare keepsake to sell
        ks = conn.execute(
            """SELECT id, title, history_json FROM keepsakes
               WHERE owner_id = ? ORDER BY id DESC LIMIT 1""",
            (sid,),
        ).fetchone()

        if ks:
            kid = int(ks["id"])
            title = ks["title"]
            try:
                hist = json.loads(ks["history_json"] or "[]")
            except Exception:
                hist = []
            hist.append({
                "event": "purchased",
                "from": seller["name"],
                "to": buyer["name"],
                "price_cents": price,
                "tick": tick,
            })
            conn.execute(
                """UPDATE keepsakes
                   SET owner_id = ?, venue_id = NULL, history_json = ?
                   WHERE id = ?""",
                (bid, json.dumps(hist), kid),
            )
        else:
            kind, base_title, craft = r.choice(_VINTAGE_FINDS)
            title = f"{base_title} from {seller['name']}'s yard sale"
            hist = [{
                "event": "purchased",
                "from": seller["name"],
                "to": buyer["name"],
                "price_cents": price,
                "tick": tick,
            }]
            cur = conn.execute(
                """INSERT INTO keepsakes(
                       owner_id, creator_id, kind, title,
                       craft, created_tick, generations, history_json)
                   VALUES(?, ?, ?, ?, ?, ?, 1, ?)""",
                (bid, sid, kind, title, craft, tick, json.dumps(hist)),
            )
            kid = int(cur.lastrowid)

        # Strictly conserved wallet-to-wallet transfer
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ?, fun = MIN(100, fun + 6) WHERE agent_id = ?",
            (price, bid),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ?, stress = MAX(0, stress - 5) WHERE agent_id = ?",
            (price, sid),
        )
        conn.execute(
            """INSERT INTO market_trades(
                   day, tick, seller_id, buyer_id, keepsake_id, item_title, price_cents)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (day, tick, sid, bid, kid, title, price),
        )
        lo, hi = min(sid, bid), max(sid, bid)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,5.0,6.0,0.0,'acquaintance',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 2.5),
                 affinity = MIN(100.0, relationships.affinity + 3.5),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        text = (
            f"bought {title} from {seller['name']} for ${price / 100:,.0f} "
            f"at {venue_name}"
        )
        emit(conn, tick, "life_event", place_id=venue_id, a=bid, b=sid,
             importance=MINOR, text=text, tag="flea_market_sale",
             price_cents=price, keepsake_id=kid)
        remember(conn, bid, tick,
                 f"I picked up {title} from {seller['name']} at {venue_name} for ${price / 100:,.0f}.",
                 kind="personal", importance=3)
        remember(conn, sid, tick,
                 f"Sold {title} to {buyer['name']} at {venue_name} for ${price / 100:,.0f}.",
                 kind="personal", importance=2)
        used_buyers.add(bid)
        trades += 1
        volume_cents += price

    return {"trades": trades, "volume_cents": volume_cents}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    return run_weekend_market(conn, tick, seed)


def list_trades(conn: sqlite3.Connection, limit: int = 40) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT m.*, sa.name AS seller_name, ba.name AS buyer_name
           FROM market_trades m
           LEFT JOIN agents sa ON sa.id = m.seller_id
           LEFT JOIN agents ba ON ba.id = m.buyer_id
           ORDER BY m.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def market_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(price_cents), 0) AS vol
           FROM market_trades"""
    ).fetchone()
    return {
        "total_trades": int(row["n"] or 0),
        "total_volume_cents": int(row["vol"] or 0),
        "recent_trades": list_trades(conn, limit=20),
    }
