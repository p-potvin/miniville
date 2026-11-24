"""Tests for v0.63 Town Courts, Civil Mediation & Jury Hearings."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import courts, db, economy, world


def _build_town(seed: str = "courts_seed") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    db.init_db(conn)
    world.create_world(conn)

    home_id = conn.execute("SELECT id FROM places LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = conn.execute("SELECT id FROM households LIMIT 1").fetchone()["id"]

    residents = [
        ("u1", "Judge Elena", "Female", 58, "magistrate", 25, 40000),
        ("u2", "Alice Lender", "Female", 40, "baker", 10, 30000),
        ("u3", "Bob Borrower", "Male", 35, "carpenter", 4, 15000),
        ("u4", "Clara Rival", "Female", 32, "artist", 5, 20000),
        ("u5", "Dan Rival", "Male", 36, "clerk", 5, 20000),
        ("u6", "Juror One", "Female", 45, "teacher", 8, 10000),
        ("u7", "Juror Two", "Male", 50, "mechanic", 7, 10000),
        ("u8", "Juror Three", "Female", 29, "nurse", 6, 10000),
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
            "INSERT INTO agent_state(agent_id, place_id, money_cents, stress) VALUES(?, ?, ?, 50)",
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


def test_schema_and_empty_summary(conn):
    assert courts.list_cases(conn) == []
    assert courts.courts_summary(conn)["total_cases"] == 0

    courts.ensure_courts_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(court_cases)").fetchall()}
    assert {
        "id", "day", "year", "kind", "plaintiff_id", "defendant_id",
        "judge_id", "jury_count", "verdict", "restitution_cents",
        "fine_cents", "summary",
    }.issubset(cols)


def test_loan_default_hearing_and_exact_conservation(conn):
    conn.execute(
        """INSERT INTO loans(lender_id, borrower_id, principal_cents, remaining_cents,
                             weekly_payment_cents, status)
           VALUES(2, 3, 10000, 6000, 1000, 'defaulted')"""
    )
    before = _total_money(conn)

    res = courts.daily_courts(conn, 18, "courts_seed")
    assert res["heard"] is True
    assert res["kind"] == "loan_default_hearing"
    assert res["plaintiff_id"] == 2
    assert res["defendant_id"] == 3
    assert res["judge_id"] == 1
    assert 3 <= res["jury_count"] <= 5
    assert _total_money(conn) == before

    ev = conn.execute("SELECT kind FROM events WHERE kind = 'court_verdict'").fetchone()
    assert ev is not None


def test_civil_mediation_heals_rivalry_affinity(conn):
    conn.execute(
        """INSERT INTO relationships(a_id, b_id, familiarity, affinity, label)
           VALUES(4, 5, 40.0, -45.0, 'rival')"""
    )
    before = _total_money(conn)

    res = courts.daily_courts(conn, 18, "courts_seed")
    assert res["heard"] is True
    assert res["kind"] == "civil_mediation"
    assert _total_money(conn) == before

    rel = conn.execute(
        "SELECT affinity FROM relationships WHERE a_id = 4 AND b_id = 5"
    ).fetchone()
    if res["verdict"] != "dismissed":
        assert float(rel["affinity"]) > -45.0


def test_determinism_and_idempotency(conn):
    conn.execute(
        """INSERT INTO loans(lender_id, borrower_id, principal_cents, remaining_cents,
                             weekly_payment_cents, status)
           VALUES(2, 3, 10000, 6000, 1000, 'defaulted')"""
    )
    c2 = _build_town("courts_seed")
    c2.execute(
        """INSERT INTO loans(lender_id, borrower_id, principal_cents, remaining_cents,
                             weekly_payment_cents, status)
           VALUES(2, 3, 10000, 6000, 1000, 'defaulted')"""
    )
    try:
        r1 = courts.daily_courts(conn, 18, "courts_seed")
        r2 = courts.daily_courts(c2, 18, "courts_seed")
        assert r1 == r2

        before = _total_money(conn)
        r1_again = courts.daily_courts(conn, 18, "courts_seed")
        assert r1_again["already_ran"] is True
        assert _total_money(conn) == before
        assert conn.execute("SELECT COUNT(*) n FROM court_cases WHERE day = 18").fetchone()["n"] == 1
    finally:
        c2.close()
