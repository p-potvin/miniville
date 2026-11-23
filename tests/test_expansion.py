"""Tests for v0.60 Dynamic Town Expansion, Lot Zoning & New Venue Construction."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import db, economy, expansion, world


def _build_town(seed: str = "exp_seed", purse_cents: int = 800_000) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    db.init_db(conn)
    world.create_world(conn)
    economy.ensure_businesses(conn)
    conn.execute("UPDATE businesses SET balance_cents = 50000")
    economy.town_credit(conn, purse_cents)

    home_id = conn.execute("SELECT id FROM places LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = conn.execute("SELECT id FROM households LIMIT 1").fetchone()["id"]

    residents = [
        ("u1", "Alice Carpenter", "Female", 34, "carpenter", 10, 20000),
        ("u2", "Bob Mechanic", "Male", 41, "mechanic", 7, 20000),
        ("u3", "Clara Builder", "Female", 29, "builder", 5, 20000),
        ("u4", "Dan Clerk", "Male", 45, "clerk", 4, 20000),
    ]
    for idx, (uid, name, sex, age, occ, standing, wallet) in enumerate(residents, start=1):
        conn.execute(
            """INSERT INTO agents(
                   uuid, name, sex, age, is_child, marital_status, occupation,
                   household_id, home_place_id, standing)
               VALUES(?, ?, ?, ?, 0, 'never_married', ?, ?, ?, ?)""",
            (uid, name, sex, age, occ, hh_id, home_id, standing),
        )
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?, ?, ?)",
            (idx, home_id, wallet),
        )

    db.set_meta(conn, "seed", seed)
    conn.commit()
    return conn


def _total_money(conn: sqlite3.Connection) -> int:
    agents = conn.execute("SELECT COALESCE(SUM(money_cents), 0) s FROM agent_state").fetchone()["s"]
    biz = conn.execute("SELECT COALESCE(SUM(balance_cents), 0) s FROM businesses").fetchone()["s"]
    town = economy.town_balance(conn)
    return int(agents) + int(biz) + int(town)


@pytest.fixture()
def conn():
    c = _build_town()
    yield c
    c.close()


def test_schema_and_empty_queries(conn):
    assert expansion.list_expansions(conn) == []
    assert expansion.expansion_summary(conn)["total_expansions"] == 0

    expansion.ensure_expansion_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(town_expansions)").fetchall()}
    assert {
        "id", "day", "year", "district", "kind", "place_id", "place_name",
        "cost_cents", "sponsor_name", "builders_paid_cents", "status", "summary",
    }.issubset(cols)


def test_deficit_gating(conn):
    conn.execute("UPDATE town_account SET balance_cents = 250000 WHERE id = 1")
    res = expansion.daily_expansion(conn, 45, "exp_seed")
    assert res["expanded"] is False
    assert res["reason"] == "insufficient_purse"
    assert expansion.list_expansions(conn) == []


def test_conserved_expansion_landmark_and_wing_addition(conn):
    initial_total = _total_money(conn)
    initial_places = conn.execute("SELECT COUNT(*) n FROM places").fetchone()["n"]

    # Run through 360 days; purse stays >= 600_000 enough to trigger multiple expansions
    built_kinds: set[str] = set()
    for d in range(1, 361):
        # Top up purse in test if needed only via conserved transfer? No, 800k - 600k = 200k allows ~3 expansions
        res = expansion.daily_expansion(conn, d, "exp_seed")
        if res.get("expanded"):
            built_kinds.add(res["kind"])
        assert _total_money(conn) == initial_total

    summ = expansion.expansion_summary(conn)
    assert summ["total_expansions"] >= 2
    assert summ["landmarks_built"] <= expansion.MAX_NEW_LANDMARKS
    assert "new_landmark" in built_kinds or "wing_addition" in built_kinds

    new_places = conn.execute("SELECT COUNT(*) n FROM places").fetchone()["n"]
    assert new_places == initial_places + summ["landmarks_built"]

    ev_count = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE kind = 'town_expansion'"
    ).fetchone()["n"]
    assert ev_count == summ["total_expansions"]


def test_determinism_and_idempotency(conn):
    c2 = _build_town("exp_seed")
    try:
        r1 = expansion.daily_expansion(conn, 45, "exp_seed")
        r2 = expansion.daily_expansion(c2, 45, "exp_seed")
        assert r1 == r2
        assert r1["expanded"] is True

        before_money = _total_money(conn)
        r1_again = expansion.daily_expansion(conn, 45, "exp_seed")
        assert r1_again["already_ran"] is True
        assert _total_money(conn) == before_money
        assert conn.execute("SELECT COUNT(*) n FROM town_expansions WHERE day = 45").fetchone()["n"] == 1
    finally:
        c2.close()
