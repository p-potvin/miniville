"""Tests for Neighboring Sister Towns, Tourism Waves & Conserved Regional Trade (v0.61)."""
import sqlite3

from miniville import db, festivals, museum, regional, world


def _build_town() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    places = world.create_world(c)
    home_id = next(iter(places.values()))
    c.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])

    for i in range(1, 16):
        c.execute(
            """INSERT INTO agents(
                   id, uuid, name, sex, age, is_child, marital_status, occupation,
                   household_id, home_place_id, work_place_id, alive, standing
               ) VALUES (?, ?, ?, 'Female', ?, 0, 'married', 'artisan', ?, ?, ?, 1, ?)""",
            (i, f"u-{i}", f"Trader {i}", 25 + i, hh_id, home_id, places["Old Mill Shops"], 10 + i),
        )
        c.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES (?, ?, 120000)",
            (i, home_id),
        )

    for pname in ("Old Mill Shops", "Miniville Grocer", "Riverside Diner"):
        c.execute(
            """INSERT INTO businesses(place_id, status, balance_cents, revenue_total, payroll_total, ema_traffic)
               VALUES (?, 'open', 60000, 150000, 80000, 20.0)""",
            (places[pname],),
        )

    c.execute("UPDATE town_account SET balance_cents = 250000 WHERE id = 1")
    c.commit()
    return c


def _three_way_money(conn: sqlite3.Connection) -> int:
    wallets = conn.execute("SELECT COALESCE(SUM(money_cents), 0) AS s FROM agent_state").fetchone()["s"]
    tills = conn.execute("SELECT COALESCE(SUM(balance_cents), 0) AS s FROM businesses").fetchone()["s"]
    purse = conn.execute("SELECT balance_cents FROM town_account WHERE id = 1").fetchone()["balance_cents"]
    return int(wallets) + int(tills) + int(purse)


def test_exact_three_way_money_conservation_and_schema():
    conn = _build_town()
    try:
        regional.ensure_regional_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(regional_days)")}
        assert {
            "day", "sister_town", "visitors_count", "trade_volume_cents",
            "customs_cents", "top_export", "top_import", "envoy_agent_id", "summary",
        }.issubset(cols)

        initial_total = _three_way_money(conn)
        total_trade = 0
        total_customs = 0
        for day in range(45):
            res = regional.daily_regional(conn, day, "regional_seed")
            total_trade += res["trade_volume_cents"]
            total_customs += res["customs_cents"]
            assert res["sister_town"] in regional.SISTER_TOWN_NAMES
            assert _three_way_money(conn) == initial_total

        assert total_trade > 0
        assert total_customs > 0

        summary = regional.regional_summary(conn, days=14)
        assert summary["total_days"] == 45
        assert summary["total_trade_cents"] == total_trade
        assert summary["total_customs_cents"] == total_customs
        assert len(summary["history"]) == 14
        assert len(summary["by_sister_town"]) == 3

        # Rate-limited events: at most ceil(45 / 5) = 9 events
        ev_count = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind IN ('regional_caravan', 'tourism_wave')"
        ).fetchone()["n"]
        assert 1 <= ev_count <= 9
    finally:
        conn.close()


def test_festival_weather_and_museum_coupling():
    c_boom = _build_town()
    c_storm = _build_town()
    try:
        festivals.ensure_festivals_schema(c_boom)
        museum.ensure_museum_schema(c_boom)
        c_boom.execute(
            """INSERT INTO festivals(season, year, name, start_day, end_day, status)
               VALUES ('spring', 1, 'Spring Blossom Fair', 0, 14, 'active')"""
        )
        c_boom.execute(
            """INSERT INTO museum_exhibits(day_curated, year_curated, category, title, description)
               VALUES (1, 1, 'keepsake', 'Grand Clock', 'A prized clock.')"""
        )

        boom_visitors = 0
        storm_visitors = 0
        for day in range(15):
            c_boom.execute(
                "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES (?, 'clear', 72, 0)",
                (day,),
            )
            c_storm.execute(
                "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES (?, 'storm', 58, 2)",
                (day,),
            )
            boom_visitors += regional.daily_regional(c_boom, day, "couple_seed")["visitors_count"]
            storm_visitors += regional.daily_regional(c_storm, day, "couple_seed")["visitors_count"]

        assert boom_visitors > storm_visitors
    finally:
        c_boom.close()
        c_storm.close()


def test_determinism_and_idempotency():
    c1 = _build_town()
    c2 = _build_town()
    try:
        for day in range(25):
            r1 = regional.daily_regional(c1, day, "det_regional")
            r2 = regional.daily_regional(c2, day, "det_regional")
            assert r1 == r2

        assert regional.regional_summary(c1) == regional.regional_summary(c2)

        before_money = _three_way_money(c1)
        before_summary = regional.regional_summary(c1)
        again = regional.daily_regional(c1, 10, "det_regional")
        assert again["already_ran"] is True
        assert _three_way_money(c1) == before_money
        assert regional.regional_summary(c1) == before_summary
    finally:
        c1.close()
        c2.close()
