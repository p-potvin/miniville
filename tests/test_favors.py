"""Favors/debt/drama behavior tests on synthetic agents."""
import sqlite3

import pytest

from miniville import db
from miniville.favors import (drama_enabled, maybe_repay_debt,
                              spouse_discovery)
from miniville.events import emit
from miniville.rng import rng_for


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_db(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('P','public','Downtown',40)")
    pid = c.execute("SELECT id FROM places WHERE name='P'").fetchone()["id"]
    for i, (name, st) in enumerate(
            [("Ann", "married_present"), ("Bob", "never_married"),
             ("Cara", "married_present")], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,age,marital_status,occupation,
               hobbies_json,home_place_id,work_place_id) VALUES(?,?,?,?,?,?,?,?)""",
            (f"u{i}", name, 30, st, "clerk", '[]', pid, pid))
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,mood) VALUES(?,?,'content')",
            (i, pid))
    # Ann+Cara are spouses
    c.execute(
        "INSERT INTO relationships(a_id,b_id,label,romance,familiarity) VALUES(1,3,'spouse',95,80)")
    db.set_meta(c, "seed", "t")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_debt_lifecycle(conn):
    conn.execute(
        "INSERT INTO debts(debtor_id,creditor_id,kind,created_tick) VALUES(2,1,'borrowed a tool',0)")
    conn.commit()
    a = conn.execute("SELECT * FROM agents WHERE id=2").fetchone()
    open_debt = conn.execute(
        "SELECT id FROM debts WHERE debtor_id=2 AND repaid_tick IS NULL").fetchone()
    assert open_debt
    # force repayment: retry until a seed hits (bounded) — repayment prob 0.35
    repaid = False
    for t in range(1, 40):
        r = rng_for("t", 2, 1, t)
        maybe_repay_debt(conn, 2, 1, 1, t, "warm", r)
        if not conn.execute(
                "SELECT 1 FROM debts WHERE repaid_tick IS NULL").fetchall():
            repaid = True
            break
    assert repaid
    ev = conn.execute(
        "SELECT * FROM events WHERE kind='favor_repaid'").fetchall()
    assert ev


def test_drama_gate_off_means_no_discovery(conn):
    db.set_meta(conn, "drama_enabled", "0")
    emit(conn, 0, "affair", a=1, b=2, importance=3)
    conn.commit()
    n = spouse_discovery(conn, 48, "t")   # day-1 pass checks day-0 affairs
    assert n == 0
    assert not drama_enabled(conn)


def test_discovery_can_happen(conn):
    emit(conn, 0, "affair", a=1, b=2, importance=3)
    conn.commit()
    found = False
    # P_DISCOVERY=0.15 per spouse per day — simulate up to 200 days of chances
    for d in range(1, 200):
        # keep affair on yesterday's day index by shifting tick window:
        # spouse_discovery looks at day-1, so emit affair at day d-1
        conn.execute("UPDATE events SET day=?", (d - 1,))
        n = spouse_discovery(conn, d * 48, "t")
        if n:
            found = True
            break
    assert found
    betrayals = conn.execute(
        "SELECT * FROM events WHERE kind='betrayal'").fetchall()
    assert betrayals
