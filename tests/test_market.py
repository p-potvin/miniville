"""Tests for Town Market Prices, Supply Waves & Cost-of-Living Index (v0.56)."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import db, economy, market, world
from miniville.seasons import season_of


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    market.ensure_market_schema(c)
    db.set_meta(c, "seed", "market_test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def _make_world_conn(seed: str = "market_test") -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    market.ensure_market_schema(c)
    db.set_meta(c, "seed", seed)
    db.set_meta(c, "tick", "0")
    c.commit()
    return c


def test_query_helpers_safe_before_schema_or_data():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    assert market.get_market_day(c, 0) is None
    assert market.sector_multiplier(c, 0, "food") == 1.0
    assert market.sector_multiplier(c, 0, "unknown_sector") == 1.0
    assert market.market_history(c, days=14) == []
    c.close()


def test_bounds_across_full_year_and_extreme_supply(conn):
    # First run 365 days normally
    for d in range(365):
        row = market.daily_market(conn, d, seed="market_test")
        for s in market.SECTORS:
            val = row[f"{s}_mult"]
            assert market.MIN_MULT <= val <= market.MAX_MULT
        assert 85.0 <= row["cpi_index"] <= 120.0

    # Also test extreme case: severe blizzard + all businesses closed
    conn2 = _make_world_conn("extreme_seed")
    try:
        winter_day = next(d for d in range(365) if season_of(d) == "winter")
        conn2.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (winter_day, "blizzard", 8, 2),
        )
        conn2.execute("UPDATE businesses SET status = 'closed'")
        row = market.daily_market(conn2, winter_day, seed="extreme_seed")
        for s in market.SECTORS:
            assert market.MIN_MULT <= row[f"{s}_mult"] <= market.MAX_MULT
        assert 85.0 <= row["cpi_index"] <= 120.0
    finally:
        conn2.close()


def test_determinism_and_idempotency():
    c1 = _make_world_conn("det_seed")
    c2 = _make_world_conn("det_seed")
    try:
        for d in range(30):
            r1 = market.daily_market(c1, d, seed="det_seed")
            r2 = market.daily_market(c2, d, seed="det_seed")
            assert r1 == r2

        # Calling daily_market again on same day returns identical row without overwrite
        again = market.daily_market(c1, 10, seed="det_seed")
        assert again == market.get_market_day(c1, 10)
    finally:
        c1.close()
        c2.close()


def test_weather_responsiveness():
    c_clear = _make_world_conn("w_resp")
    c_storm = _make_world_conn("w_resp")
    try:
        day = 40
        c_clear.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (day, "clear", 70, 0),
        )
        c_storm.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (day, "storm", 64, 2),
        )
        r_clear = market.daily_market(c_clear, day, seed="w_resp")
        r_storm = market.daily_market(c_storm, day, seed="w_resp")

        assert r_storm["food_mult"] > r_clear["food_mult"]
        assert r_storm["goods_mult"] > r_clear["goods_mult"]
        assert r_storm["leisure_mult"] < r_clear["leisure_mult"]
        assert "storm disruption" in r_storm["driver_note"]
    finally:
        c_clear.close()
        c_storm.close()


def test_supply_responsiveness_to_closed_businesses():
    c_open = _make_world_conn("sup_resp")
    c_closed = _make_world_conn("sup_resp")
    try:
        day = 25
        c_closed.execute("UPDATE businesses SET status = 'closed'")
        r_open = market.daily_market(c_open, day, seed="sup_resp")
        r_closed = market.daily_market(c_closed, day, seed="sup_resp")

        for s in market.SECTORS:
            assert r_closed[f"{s}_mult"] > r_open[f"{s}_mult"]
        assert r_closed["cpi_index"] > r_open["cpi_index"]
        assert "supply" in r_closed["driver_note"]
    finally:
        c_open.close()
        c_closed.close()


def test_seasonal_harvest_vs_winter():
    c = _make_world_conn("season_cmp")
    try:
        autumn_day = next(d for d in range(365) if season_of(d) == "autumn")
        winter_day = next(d for d in range(365) if season_of(d) == "winter")
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (autumn_day, "clear", 58, 0),
        )
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (winter_day, "clear", 30, 0),
        )
        r_aut = market.daily_market(c, autumn_day, seed="season_cmp")
        r_win = market.daily_market(c, winter_day, seed="season_cmp")
        assert r_win["food_mult"] > r_aut["food_mult"]
        assert r_win["cpi_index"] > r_aut["cpi_index"]
    finally:
        c.close()


def test_cpi_math_matches_weights(conn):
    assert market.compute_cpi(1.0, 1.0, 1.0, 1.0) == 100.0
    assert market.compute_cpi(1.2, 1.2, 1.2, 1.2) == 120.0
    assert market.compute_cpi(0.85, 0.85, 0.85, 0.85) == 85.0

    row = market.daily_market(conn, 12, seed="market_test")
    expected_cpi = round(
        100.0
        * (
            0.35 * row["food_mult"]
            + 0.25 * row["dining_mult"]
            + 0.25 * row["goods_mult"]
            + 0.15 * row["leisure_mult"]
        ),
        2,
    )
    assert row["cpi_index"] == expected_cpi


def test_market_bulletin_on_swings_and_rate_limit():
    c = _make_world_conn("bulletin_test")
    try:
        # Normal clear spring day should not trigger a bulletin
        normal_day = next(d for d in range(365) if season_of(d) == "spring")
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (normal_day, "clear", 65, 0),
        )
        r_norm = market.daily_market(c, normal_day, seed="bulletin_test")
        assert market.CPI_LOW_THRESHOLD < r_norm["cpi_index"] < market.CPI_HIGH_THRESHOLD
        assert (
            c.execute("SELECT COUNT(*) FROM events WHERE data LIKE '%market_bulletin%'").fetchone()[0]
            == 0
        )

        # Force high CPI swing across consecutive winter days with blizzard + closed businesses
        winter_start = next(d for d in range(365) if season_of(d) == "winter")
        c.execute("UPDATE businesses SET status = 'closed'")
        for offset in range(10):
            d = winter_start + offset
            c.execute(
                "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
                (d, "blizzard", 10, 2),
            )
            r_swing = market.daily_market(c, d, seed="bulletin_test")
            assert r_swing["cpi_index"] >= market.CPI_HIGH_THRESHOLD

        bulletins = c.execute(
            "SELECT day FROM events WHERE data LIKE '%market_bulletin%' ORDER BY day"
        ).fetchall()
        # Rate-limited: over 10 consecutive swing days with 7-day cooldown, at most 2 bulletins
        assert 1 <= len(bulletins) <= 2
        if len(bulletins) == 2:
            assert bulletins[1]["day"] - bulletins[0]["day"] >= market.BULLETIN_COOLDOWN_DAYS
    finally:
        c.close()


def test_get_market_day_sector_multiplier_and_history(conn):
    for d in range(20):
        market.daily_market(conn, d, seed="market_test")

    d5 = market.get_market_day(conn, 5)
    assert d5 is not None
    assert d5["day"] == 5
    for s in market.SECTORS:
        assert market.sector_multiplier(conn, 5, s) == d5[f"{s}_mult"]
    assert market.sector_multiplier(conn, 5, "invalid") == 1.0

    hist = market.market_history(conn, days=14)
    assert len(hist) == 14
    assert [h["day"] for h in hist] == list(range(19, 5, -1))
