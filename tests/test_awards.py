"""Tests for Town Awards & Civic Honors (v0.58)."""
import sqlite3

from miniville import awards, db, world


def _build_town(town_purse_cents: int = 800_000) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    places = world.create_world(c)
    home_id = next(iter(places.values()))
    c.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])

    for i in range(1, 13):
        c.execute(
            """INSERT INTO agents(
                   id, uuid, name, sex, age, is_child, marital_status, occupation,
                   household_id, home_place_id, work_place_id, alive, standing
               ) VALUES (?, ?, ?, 'Female', ?, 0, 'married', 'artisan', ?, ?, ?, 1, ?)""",
            (i, f"u-{i}", f"Honoree {i}", 28 + i, hh_id, home_id, places["Old Mill Shops"], 10 + i * 3),
        )
        c.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES (?, ?, 50000)",
            (i, home_id),
        )

    # Businesses & jobs
    c.execute(
        """INSERT INTO businesses(place_id, status, balance_cents, revenue_total, payroll_total, ema_traffic)
           VALUES (?, 'open', 100000, 450000, 200000, 25.0)""",
        (places["Old Mill Shops"],),
    )
    c.execute(
        """INSERT INTO jobs(agent_id, place_id, role, wage_cents, shift_start, shift_end, rank)
           VALUES (2, ?, 'manager', 15000, 16, 32, 2)""",
        (places["Old Mill Shops"],),
    )

    # Relationships & keepsakes
    for j in range(2, 8):
        c.execute(
            "INSERT INTO relationships(a_id, b_id, familiarity, affinity, label) VALUES (1, ?, 50, 40, 'friend')",
            (j,),
        )
    c.execute(
        """INSERT INTO keepsakes(title, kind, craft, creator_id, owner_id, venue_id, created_tick, generations, appreciations)
           VALUES ('Cedar Chest', 'craft', 'woodwork', 3, 3, ?, 10, 1, 4)""",
        (places["Old Mill Shops"],),
    )

    c.execute("UPDATE town_account SET balance_cents = ? WHERE id = 1", (town_purse_cents,))
    c.commit()
    return c


def _total_money(conn: sqlite3.Connection) -> int:
    wallets = conn.execute("SELECT COALESCE(SUM(money_cents), 0) AS s FROM agent_state").fetchone()["s"]
    purse = conn.execute("SELECT balance_cents FROM town_account WHERE id = 1").fetchone()["balance_cents"]
    return int(wallets) + int(purse)


def test_seasonal_cadence_conservation_and_events():
    conn = _build_town(town_purse_cents=800_000)
    try:
        awards.ensure_awards_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(town_awards)")}
        assert {
            "id", "day", "year", "season", "category",
            "recipient_agent_id", "recipient_business_id",
            "stipend_cents", "citation",
        }.issubset(cols)

        initial_money = _total_money(conn)
        total_stipends = 0
        ceremony_days = []
        for day in range(180):
            res = awards.daily_awards(conn, day, "awards_seed")
            if res["awarded"]:
                ceremony_days.append(day)
                total_stipends += res["stipends_cents"]
            assert _total_money(conn) == initial_money

        # 180 days (Jan 1 -> Jun 29) covers Winter (Jan) and Spring (Apr)
        assert len(ceremony_days) == 2
        assert total_stipends > 0

        listed = awards.list_awards(conn, limit=20)
        assert len(listed) == 8
        categories = {a["category"] for a in listed}
        assert categories == set(awards.CATEGORIES)

        ev_count = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind = 'town_award' AND importance = 4"
        ).fetchone()["n"]
        assert ev_count == 8

        summary = awards.awards_summary(conn)
        assert summary["total_awards"] == 8
        assert summary["total_stipends_cents"] == total_stipends
    finally:
        conn.close()


def test_deficit_safe_stipend_gating():
    conn = _build_town(town_purse_cents=120_000)  # Below 500,000 threshold
    try:
        initial_money = _total_money(conn)
        for day in range(120):
            res = awards.daily_awards(conn, day, "poor_town")
            assert res["stipends_cents"] == 0
            assert _total_money(conn) == initial_money

        purse = conn.execute("SELECT balance_cents FROM town_account WHERE id = 1").fetchone()["balance_cents"]
        assert purse == 120_000
        summary = awards.awards_summary(conn)
        assert summary["total_awards"] == 8
        assert summary["total_stipends_cents"] == 0
    finally:
        conn.close()


def test_determinism_and_idempotency():
    c1 = _build_town(town_purse_cents=750_000)
    c2 = _build_town(town_purse_cents=750_000)
    try:
        for day in range(120):
            r1 = awards.daily_awards(c1, day, "det_awards")
            r2 = awards.daily_awards(c2, day, "det_awards")
            assert r1 == r2

        assert awards.list_awards(c1) == awards.list_awards(c2)
        assert awards.awards_summary(c1) == awards.awards_summary(c2)

        # Idempotency: re-running on an already-awarded day does nothing
        before_money = _total_money(c1)
        before_count = awards.awards_summary(c1)["total_awards"]
        again = awards.daily_awards(c1, 14, "det_awards")
        assert again.get("already_ran") is True
        assert _total_money(c1) == before_money
        assert awards.awards_summary(c1)["total_awards"] == before_count
    finally:
        c1.close()
        c2.close()
