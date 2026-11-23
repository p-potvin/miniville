"""Tests for v0.57 Town Transit & Bus Loop."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import db, economy, transit, world


def _build_town(seed: str = "transit_seed") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    db.init_db(conn)
    world.create_world(conn)

    downtown_home = conn.execute(
        "SELECT id FROM places WHERE district='Downtown' LIMIT 1"
    ).fetchone()["id"]
    lakeshore_work = conn.execute(
        "SELECT id FROM places WHERE district='Lakeshore' LIMIT 1"
    ).fetchone()["id"]
    mill_work = conn.execute(
        "SELECT id FROM places WHERE district='Old Mill Quarter' LIMIT 1"
    ).fetchone()["id"]

    conn.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (downtown_home,))
    hh_id = conn.execute("SELECT id FROM households LIMIT 1").fetchone()["id"]

    # Mix of adults (some cross-district, one broke), youth (<18), and elders (>=65)
    residents = [
        ("u1", "Alice Commuter", "Female", 32, 0, "clerk", downtown_home, lakeshore_work, 5000),
        ("u2", "Bob Rider", "Male", 28, 0, "baker", downtown_home, mill_work, 5000),
        ("u3", "Clara Broke", "Female", 35, 0, "artist", downtown_home, lakeshore_work, 50),  # < 150 cents -> skipped
        ("u4", "Timmy Youth", "Male", 14, 1, "student", downtown_home, lakeshore_work, 0),    # free (<18)
        ("u5", "Martha Elder", "Female", 72, 0, "retired", downtown_home, mill_work, 0),     # free (>=65)
        ("u6", "Dan Driver", "Male", 44, 0, "bus driver", downtown_home, downtown_home, 5000),
        ("u7", "Eva Commuter", "Female", 40, 0, "teacher", downtown_home, lakeshore_work, 5000),
        ("u8", "Frank Commuter", "Male", 38, 0, "engineer", downtown_home, mill_work, 5000),
    ]
    for idx, (uid, name, sex, age, is_child, occ, hp, wp, wallet) in enumerate(residents, start=1):
        conn.execute(
            """INSERT INTO agents(
                   uuid, name, sex, age, is_child, marital_status, occupation,
                   household_id, home_place_id, work_place_id, standing)
               VALUES(?, ?, ?, ?, ?, 'never_married', ?, ?, ?, ?, 5)""",
            (uid, name, sex, age, is_child, occ, hh_id, hp, wp),
        )
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?, ?, ?)",
            (idx, hp, wallet),
        )

    db.set_meta(conn, "seed", seed)
    conn.commit()
    return conn


def _total_money(conn: sqlite3.Connection) -> int:
    agents = conn.execute("SELECT COALESCE(SUM(money_cents), 0) s FROM agent_state").fetchone()["s"]
    town = economy.town_balance(conn)
    return int(agents) + int(town)


@pytest.fixture()
def conn():
    c = _build_town()
    yield c
    c.close()


def test_schema_and_empty_summary(conn):
    assert transit.transit_summary(conn)["total_rides"] == 0
    transit.ensure_transit_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transit_days)").fetchall()}
    assert {
        "day", "total_rides", "storm_rides", "fare_revenue_cents",
        "on_time_pct", "top_route", "driver_agent_id", "note",
    }.issubset(cols)


def test_money_conservation_and_youth_elder_free_rides(conn):
    before_total = _total_money(conn)
    before_town = economy.town_balance(conn)

    # Run several days to ensure youth/elder/adult rides occur
    total_rev = 0
    for d in range(1, 8):
        res = transit.daily_transit(conn, d, "transit_seed")
        total_rev += res["fare_revenue_cents"]
        assert _total_money(conn) == before_total

    # Broke adult (u3, 50 cents < 150) must never go negative or be charged
    u3_bal = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=3").fetchone()["money_cents"]
    assert u3_bal == 50

    # Youth (u4) and Elder (u5) started at 0 and stay at 0 (free rides)
    u4_bal = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=4").fetchone()["money_cents"]
    u5_bal = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=5").fetchone()["money_cents"]
    assert u4_bal == 0
    assert u5_bal == 0

    assert economy.town_balance(conn) == before_town + total_rev
    summ = transit.transit_summary(conn, days=14)
    assert summ["total_revenue_cents"] == total_rev
    assert len(summ["history"]) == 7


def test_storm_impact_and_delay_event(conn):
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(10, 'clear', 72, 0)"
    )
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(11, 'storm', 64, 2)"
    )

    clear_res = transit.daily_transit(conn, 10, "transit_seed")
    storm_res = transit.daily_transit(conn, 11, "transit_seed")

    assert clear_res["storm_rides"] == 0
    assert storm_res["storm_rides"] == storm_res["total_rides"]
    assert storm_res["on_time_pct"] < clear_res["on_time_pct"]
    assert storm_res["on_time_pct"] < transit.DELAY_THRESHOLD_PCT

    delay_ev = conn.execute(
        "SELECT kind FROM events WHERE kind='transit_delay' AND day=11"
    ).fetchone()
    assert delay_ev is not None


def test_determinism_and_idempotency(conn):
    c2 = _build_town("transit_seed")
    try:
        r1 = transit.daily_transit(conn, 5, "transit_seed")
        r2 = transit.daily_transit(c2, 5, "transit_seed")
        assert r1 == r2

        before_money = _total_money(conn)
        before_town = economy.town_balance(conn)
        r1_again = transit.daily_transit(conn, 5, "transit_seed")
        assert r1_again["already_ran"] is True
        assert _total_money(conn) == before_money
        assert economy.town_balance(conn) == before_town
        assert conn.execute("SELECT COUNT(*) n FROM transit_days WHERE day=5").fetchone()["n"] == 1
    finally:
        c2.close()
