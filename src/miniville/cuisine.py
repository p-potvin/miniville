"""Town Culinary Heritage & Signature Recipes (v0.68).

Every 15-22 days, a local cook, baker, or resident debuts a seasonal signature
dish, pastry, or brew at an open commercial food/coffee/drink venue (never a
public-service venue in `PUBLIC_TAGS`). Existing recipes accumulate daily
`orders_count` and `fame_score`. When the hosting venue holds at least
`MIN_VENUE_BONUS_RESERVE_CENTS` (15,000 cents), it pays a small conserved chef
bonus (`1,000-2,500` cents) from `businesses.balance_cents` to the creator's
`agent_state.money_cents` without touching `revenue_today`.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .economy import PUBLIC_TAGS
from .events import NOTABLE, emit
from .rng import rng_for
from .seasons import date_of, season_of
from .timekeeper import TICKS_PER_DAY

MIN_DEBUT_GAP_DAYS = 15
MAX_DEBUT_GAP_DAYS = 22
P_DEBUT_IN_WINDOW = 0.20

MIN_VENUE_BONUS_RESERVE_CENTS = 15_000
BONUS_MIN_CENTS = 1_000
BONUS_MAX_CENTS = 2_500

FOOD_TAGS = {"food", "coffee", "drink"}

_SEASONAL_RECIPES: dict[str, list[tuple[str, str, str]]] = {
    "spring": [
        ("Wildflower Honey Scone", "pastry", "clover honey, stone-ground flour, sweet cream"),
        ("Lakeshore Herb Omelette", "dish", "chives, farm eggs, aged cheddar"),
        ("Blossom Cold Brew Tonic", "brew", "single-origin espresso, citrus peel, sparkling water"),
        ("Greenhill Rhubarb Tart", "pastry", "garden rhubarb, flaky butter crust, vanilla bean"),
    ],
    "summer": [
        ("Sunburst Berry Galette", "pastry", "blackberries, peaches, toasted oats"),
        ("Marina Grilled Trout Plate", "dish", "lake trout, lemon thyme, blistered tomatoes"),
        ("Iced Maple Oat Cortado", "brew", "dark roast, maple syrup, oat milk"),
        ("Millpond Sweet Corn Chowder", "dish", "sweet corn, smoked paprika, fresh dill"),
    ],
    "autumn": [
        ("Orchard Spiced Apple Fritter", "pastry", "crisp apples, cinnamon glaze, nutmeg"),
        ("Old Mill Harvest Pot Pie", "dish", "roasted squash, sage, puff pastry"),
        ("Toasted Hazelnut Latte", "brew", "espresso, roasted hazelnuts, steamed milk"),
        ("Brown Butter Pumpkin Loaf", "pastry", "pumpkin puree, pepitas, brown butter"),
    ],
    "winter": [
        ("Fireside Cardamom Brioche", "pastry", "cardamom pods, cultured butter, pearl sugar"),
        ("Flats Hearty Beef & Barley Stew", "dish", "braised beef, pearl barley, root vegetables"),
        ("Spiced Gingerbread Mocha", "brew", "cocoa, ginger, molasses, espresso"),
        ("Snowfall Rosemary Focaccia", "pastry", "olive oil, sea salt, fresh rosemary"),
    ],
}


def ensure_cuisine_schema(conn: sqlite3.Connection) -> None:
    """Create town_recipes and cuisine_days tables if they do not exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS town_recipes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               day_created INTEGER NOT NULL,
               year_created INTEGER NOT NULL,
               name TEXT NOT NULL,
               category TEXT NOT NULL,
               creator_agent_id INTEGER REFERENCES agents(id),
               venue_id INTEGER REFERENCES places(id),
               season TEXT NOT NULL,
               ingredients TEXT NOT NULL,
               fame_score INTEGER NOT NULL DEFAULT 1,
               orders_count INTEGER NOT NULL DEFAULT 0,
               prize_cents INTEGER NOT NULL DEFAULT 0,
               summary TEXT NOT NULL
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_town_recipes_fame ON town_recipes(fame_score DESC, orders_count DESC)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cuisine_days (
               day INTEGER PRIMARY KEY
           )"""
    )


def _commercial_food_venues(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT p.id, p.name, p.kind, p.tags, b.balance_cents
           FROM businesses b
           JOIN places p ON p.id = b.place_id
           WHERE b.status = 'open' AND p.kind != 'civic'
           ORDER BY b.ema_traffic DESC, p.id ASC"""
    ).fetchall()
    out: list[sqlite3.Row] = []
    for r in rows:
        try:
            tags = set(json.loads(r["tags"] or "[]"))
        except (ValueError, TypeError):
            tags = set()
        if tags & PUBLIC_TAGS:
            continue
        if tags & FOOD_TAGS:
            out.append(r)
    return out


def _should_debut(conn: sqlite3.Connection, day: int, r) -> bool:
    row = conn.execute("SELECT MAX(day_created) AS last_day FROM town_recipes").fetchone()
    last_day = row["last_day"] if row and row["last_day"] is not None else None
    if last_day is None:
        if day >= 16:
            return True
        return r.random() < 0.25
    gap = day - int(last_day)
    if gap < MIN_DEBUT_GAP_DAYS:
        return False
    if gap >= MAX_DEBUT_GAP_DAYS:
        return True
    return r.random() < P_DEBUT_IN_WINDOW


def daily_cuisine(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Advance daily orders/fame on existing recipes and debut new seasonal recipes.

    Idempotent per `day` via `cuisine_days`. Transfers conserved chef bonuses
    strictly `businesses.balance_cents -> agent_state.money_cents` without
    touching `revenue_today`.
    """
    ensure_cuisine_schema(conn)
    day = int(day)

    if conn.execute("SELECT 1 FROM cuisine_days WHERE day = ?", (day,)).fetchone():
        return {"day": day, "debut": None, "orders_added": 0, "prize_cents": 0, "already_ran": True}

    season = season_of(day)
    year, _, _ = date_of(day)
    tick = day * TICKS_PER_DAY + 26

    # 1. Existing recipes accumulate daily orders and fame
    r_orders = rng_for(seed, "cuisine_orders", day)
    existing_recipes = conn.execute(
        """SELECT tr.id, tr.orders_count, tr.fame_score, b.status
           FROM town_recipes tr
           LEFT JOIN businesses b ON b.place_id = tr.venue_id
           ORDER BY tr.id ASC"""
    ).fetchall()
    orders_added = 0
    for rec in existing_recipes:
        if rec["status"] == "closed":
            continue
        add_orders = r_orders.randint(1, 4)
        new_orders = int(rec["orders_count"]) + add_orders
        new_fame = max(int(rec["fame_score"]), 1 + new_orders // 6)
        conn.execute(
            "UPDATE town_recipes SET orders_count = ?, fame_score = ? WHERE id = ?",
            (new_orders, new_fame, int(rec["id"])),
        )
        orders_added += add_orders

    # 2. Debut a new seasonal signature recipe on bounded 15-22 day cadence
    r_debut = rng_for(seed, "cuisine_debut", day)
    debut_info: dict[str, Any] | None = None
    prize_cents = 0

    if _should_debut(conn, day, r_debut):
        venues = _commercial_food_venues(conn)
        existing_names = {
            row["name"] for row in conn.execute("SELECT name FROM town_recipes").fetchall()
        }
        pool = [spec for spec in _SEASONAL_RECIPES[season] if spec[0] not in existing_names]
        if not pool:
            base_name, cat, ing = r_debut.choice(_SEASONAL_RECIPES[season])
            pool = [(f"{base_name} (Yr {year})", cat, ing)]

        if venues and pool:
            venue = r_debut.choice(venues)
            vid = int(venue["id"])
            vname = str(venue["name"])
            dish_name, category, ingredients = r_debut.choice(pool)

            # Prefer a cook/baker/food worker at that venue or any living adult
            cooks = conn.execute(
                """SELECT a.id, a.name
                   FROM agents a
                   LEFT JOIN jobs j ON j.agent_id = a.id
                   WHERE a.alive = 1 AND a.is_child = 0
                   ORDER BY CASE WHEN j.place_id = ? THEN 0 ELSE 1 END,
                            CASE WHEN LOWER(COALESCE(a.occupation, '')) LIKE '%cook%'
                                   OR LOWER(COALESCE(a.occupation, '')) LIKE '%baker%'
                                   OR LOWER(COALESCE(a.occupation, '')) LIKE '%chef%'
                                   OR LOWER(COALESCE(a.occupation, '')) LIKE '%food%' THEN 0 ELSE 1 END,
                            COALESCE(a.standing, 0) DESC, a.id ASC
                   LIMIT 8""",
                (vid,),
            ).fetchall()
            if cooks:
                creator = r_debut.choice(cooks[:4])
                cid = int(creator["id"])
                cname = str(creator["name"])

                if int(venue["balance_cents"]) >= MIN_VENUE_BONUS_RESERVE_CENTS:
                    prize_cents = r_debut.randint(BONUS_MIN_CENTS, BONUS_MAX_CENTS)
                    conn.execute(
                        "UPDATE businesses SET balance_cents = balance_cents - ? WHERE place_id = ?",
                        (prize_cents, vid),
                    )
                    conn.execute(
                        "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                        (prize_cents, cid),
                    )

                summary = (
                    f"{cname} debuted '{dish_name}' ({category}) at {vname} "
                    f"with {ingredients}."
                )
                cur = conn.execute(
                    """INSERT INTO town_recipes(
                           day_created, year_created, name, category, creator_agent_id,
                           venue_id, season, ingredients, fame_score, orders_count,
                           prize_cents, summary
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)""",
                    (day, year, dish_name, category, cid, vid, season, ingredients, prize_cents, summary),
                )
                rid = int(cur.lastrowid)
                emit(
                    conn,
                    tick,
                    "signature_recipe",
                    place_id=vid,
                    a=cid,
                    importance=NOTABLE,
                    recipe_id=rid,
                    name=dish_name,
                    category=category,
                    season=season,
                    prize_cents=prize_cents,
                    text=summary,
                )
                debut_info = {
                    "id": rid,
                    "name": dish_name,
                    "category": category,
                    "creator_agent_id": cid,
                    "venue_id": vid,
                    "prize_cents": prize_cents,
                }

    conn.execute("INSERT INTO cuisine_days(day) VALUES (?)", (day,))
    return {
        "day": day,
        "debut": debut_info,
        "orders_added": orders_added,
        "prize_cents": prize_cents,
        "already_ran": False,
    }


def list_recipes(conn: sqlite3.Connection, limit: int = 24) -> list[dict[str, Any]]:
    """Return town recipes ordered by fame and recency."""
    ensure_cuisine_schema(conn)
    rows = conn.execute(
        """SELECT tr.*,
                  a.name AS creator_name,
                  p.name AS venue_name
           FROM town_recipes tr
           LEFT JOIN agents a ON a.id = tr.creator_agent_id
           LEFT JOIN places p ON p.id = tr.venue_id
           ORDER BY tr.fame_score DESC, tr.orders_count DESC, tr.day_created DESC, tr.id DESC
           LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def cuisine_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate culinary heritage stats and top recipes."""
    ensure_cuisine_schema(conn)
    tot = conn.execute(
        """SELECT COUNT(*) AS total_recipes,
                  COALESCE(SUM(orders_count), 0) AS total_orders,
                  COALESCE(SUM(prize_cents), 0) AS total_prizes_cents
           FROM town_recipes"""
    ).fetchone()
    cats = conn.execute(
        "SELECT category, COUNT(*) AS n FROM town_recipes GROUP BY category ORDER BY n DESC"
    ).fetchall()
    recipes = list_recipes(conn, limit=24)
    return {
        "total_recipes": int(tot["total_recipes"]),
        "total_orders": int(tot["total_orders"]),
        "total_prizes_cents": int(tot["total_prizes_cents"]),
        "by_category": {r["category"]: int(r["n"]) for r in cats},
        "top_recipe": recipes[0] if recipes else None,
        "recipes": recipes,
    }
