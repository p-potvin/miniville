"""Tests for Town Culinary Heritage & Signature Recipes (v0.68)."""
import sqlite3

from miniville import cuisine, db, world


def _build_town(venue_balance_cents: int = 50_000) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    places = world.create_world(c)
    home_id = next(iter(places.values()))
    c.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])

    for i in range(1, 10):
        occ = "baker" if i <= 2 else "clerk"
        c.execute(
            """INSERT INTO agents(
                   id, uuid, name, sex, age, is_child, marital_status, occupation,
                   household_id, home_place_id, work_place_id, alive, standing
               ) VALUES (?, ?, ?, 'Female', ?, 0, 'married', ?, ?, ?, ?, 1, ?)""",
            (i, f"u-{i}", f"Chef {i}", 28 + i, occ, hh_id, home_id, places["Riverside Diner"], 15 + i),
        )
        c.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES (?, ?, 40000)",
            (i, home_id),
        )

    # Commercial food/coffee/drink venues + a public-tagged venue that must be skipped
    for pname in ("Riverside Diner", "The Scenic Bean", "The Quaint Corner Tavern", "Lush Meadow Park"):
        c.execute(
            """INSERT INTO businesses(place_id, status, balance_cents, revenue_today, revenue_total, payroll_total, ema_traffic)
               VALUES (?, 'open', ?, 0, 100000, 50000, 20.0)""",
            (places[pname], venue_balance_cents),
        )

    c.execute("UPDATE town_account SET balance_cents = 300000 WHERE id = 1")
    c.commit()
    return c


def _three_way_money(conn: sqlite3.Connection) -> int:
    wallets = conn.execute("SELECT COALESCE(SUM(money_cents), 0) AS s FROM agent_state").fetchone()["s"]
    tills = conn.execute("SELECT COALESCE(SUM(balance_cents), 0) AS s FROM businesses").fetchone()["s"]
    purse = conn.execute("SELECT balance_cents FROM town_account WHERE id = 1").fetchone()["balance_cents"]
    return int(wallets) + int(tills) + int(purse)


def test_debut_cadence_fame_growth_and_conservation():
    conn = _build_town(venue_balance_cents=50_000)
    try:
        cuisine.ensure_cuisine_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(town_recipes)")}
        assert {
            "id", "day_created", "year_created", "name", "category",
            "creator_agent_id", "venue_id", "season", "ingredients",
            "fame_score", "orders_count", "prize_cents", "summary",
        }.issubset(cols)

        initial_money = _three_way_money(conn)
        debut_days: list[int] = []
        total_prizes = 0

        for day in range(60):
            res = cuisine.daily_cuisine(conn, day, "cuisine_seed")
            if res["debut"] is not None:
                debut_days.append(day)
                total_prizes += res["prize_cents"]
            assert _three_way_money(conn) == initial_money

        # Bounded 15-22 day cadence over 60 days
        assert 2 <= len(debut_days) <= 4
        for a, b in zip(debut_days, debut_days[1:]):
            gap = b - a
            assert cuisine.MIN_DEBUT_GAP_DAYS <= gap <= cuisine.MAX_DEBUT_GAP_DAYS

        # revenue_today must never be modified by cuisine bonuses
        rev_today = conn.execute("SELECT SUM(revenue_today) AS s FROM businesses").fetchone()["s"]
        assert rev_today == 0

        # Never hosted at PUBLIC_TAGS venue (Lush Meadow Park)
        park_id = conn.execute("SELECT id FROM places WHERE name = 'Lush Meadow Park'").fetchone()["id"]
        recipes = cuisine.list_recipes(conn, limit=24)
        assert all(r["venue_id"] != park_id for r in recipes)

        # Orders & fame grew over time
        assert any(r["orders_count"] > 10 for r in recipes)
        assert any(r["fame_score"] > 1 for r in recipes)
        assert total_prizes > 0

        summary = cuisine.cuisine_summary(conn)
        assert summary["total_recipes"] == len(debut_days)
        assert summary["total_prizes_cents"] == total_prizes
        assert summary["top_recipe"] is not None

        ev_count = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind = 'signature_recipe'"
        ).fetchone()["n"]
        assert ev_count == len(debut_days)
    finally:
        conn.close()


def test_low_reserve_venue_skips_bonus():
    conn = _build_town(venue_balance_cents=5_000)  # Below 15,000 threshold
    try:
        initial_money = _three_way_money(conn)
        for day in range(30):
            res = cuisine.daily_cuisine(conn, day, "low_res")
            assert res["prize_cents"] == 0
            assert _three_way_money(conn) == initial_money
        assert cuisine.cuisine_summary(conn)["total_recipes"] >= 1
        assert cuisine.cuisine_summary(conn)["total_prizes_cents"] == 0
    finally:
        conn.close()


def test_determinism_and_idempotency():
    c1 = _build_town(venue_balance_cents=40_000)
    c2 = _build_town(venue_balance_cents=40_000)
    try:
        for day in range(40):
            r1 = cuisine.daily_cuisine(c1, day, "det_cuisine")
            r2 = cuisine.daily_cuisine(c2, day, "det_cuisine")
            assert r1 == r2

        assert cuisine.list_recipes(c1) == cuisine.list_recipes(c2)
        assert cuisine.cuisine_summary(c1) == cuisine.cuisine_summary(c2)

        before_money = _three_way_money(c1)
        before_summary = cuisine.cuisine_summary(c1)
        again = cuisine.daily_cuisine(c1, 18, "det_cuisine")
        assert again["already_ran"] is True
        assert _three_way_money(c1) == before_money
        assert cuisine.cuisine_summary(c1) == before_summary
    finally:
        c1.close()
        c2.close()
