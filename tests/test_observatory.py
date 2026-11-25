"""Tests for v0.67 Greenhill Skywatch & Town Almanac."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import db, economy, observatory, world


def _build_town(seed: str = "obs_seed") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    db.init_db(conn)
    world.create_world(conn)

    home_id = conn.execute("SELECT id FROM places LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = conn.execute("SELECT id FROM households LIMIT 1").fetchone()["id"]

    residents = [
        ("u1", "Ada Stargazer", "Female", 34, "astronomer", '["astronomy", "photography"]', 5, 20000),
        ("u2", "Bob Curious", "Male", 41, "teacher", '["science", "reading"]', 4, 15000),
        ("u3", "Clara Baker", "Female", 29, "baker", '["baking"]', 3, 12000),
        ("u4", "Dan Clerk", "Male", 45, "clerk", '["chess"]', 2, 10000),
        ("u5", "Eve Walker", "Female", 26, "artist", '["painting"]', 2, 8000),
        ("u6", "Frank Tinker", "Male", 52, "mechanic", '["repair"]', 3, 2000),
    ]
    for idx, (uid, name, sex, age, occ, hobbies, standing, wallet) in enumerate(residents, start=1):
        conn.execute(
            """INSERT INTO agents(
                   uuid, name, sex, age, is_child, marital_status, occupation,
                   hobbies_json, household_id, home_place_id, standing)
               VALUES(?, ?, ?, ?, 0, 'never_married', ?, ?, ?, ?, ?)""",
            (uid, name, sex, age, occ, hobbies, hh_id, home_id, standing),
        )
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents, fun, stress) VALUES(?, ?, ?, 50, 40)",
            (idx, home_id, wallet),
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


def test_schema_and_empty_queries(conn):
    assert observatory.list_skywatch(conn) == []
    assert observatory.observatory_summary(conn)["total_events"] == 0

    observatory.ensure_observatory_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(skywatch_events)").fetchall()}
    assert {
        "id", "day", "year", "season", "kind", "title", "visibility",
        "weather_condition", "attendees_count", "discoverer_agent_id",
        "host_place_id", "donation_cents", "summary",
    }.issubset(cols)


def test_crystal_clear_vs_clouded_out_and_conservation(conn):
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(28, 'clear', 64, 0)"
    )
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(56, 'storm', 58, 2)"
    )

    before = _total_money(conn)
    r_clear = observatory.daily_observatory(conn, 28, "obs_seed")
    assert r_clear["scheduled"] is True
    assert r_clear["visibility"] == "crystal_clear"
    assert _total_money(conn) == before

    r_cloud = observatory.daily_observatory(conn, 56, "obs_seed")
    assert r_cloud["scheduled"] is True
    assert r_cloud["visibility"] == "clouded_out"
    assert "moved indoors" in r_cloud["summary"]
    assert _total_money(conn) == before

    summ = observatory.observatory_summary(conn)
    assert summ["total_events"] == 2
    assert summ["clear_nights"] == 1
    assert summ["clouded_nights"] == 1
    assert conn.execute(
        "SELECT COUNT(*) n FROM events WHERE kind = 'skywatch_event'"
    ).fetchone()["n"] == 2


def test_determinism_and_idempotency(conn):
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(28, 'breezy', 60, 0)"
    )
    c2 = _build_town("obs_seed")
    c2.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(28, 'breezy', 60, 0)"
    )
    try:
        r1 = observatory.daily_observatory(conn, 28, "obs_seed")
        r2 = observatory.daily_observatory(c2, 28, "obs_seed")
        assert r1 == r2

        before = _total_money(conn)
        r1_again = observatory.daily_observatory(conn, 28, "obs_seed")
        assert r1_again["already_ran"] is True
        assert _total_money(conn) == before
        assert conn.execute("SELECT COUNT(*) n FROM skywatch_events WHERE day = 28").fetchone()["n"] == 1
    finally:
        c2.close()
