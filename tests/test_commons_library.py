"""Tests for Neighborhood Tool Library, Seed Bank & Community Orchard (v0.41)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import commons_library, db, economy, individual, world


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    pid = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ash household',?)", (pid,))
    for idx_a, (name, skill, money, hunger) in enumerate([
        ("Sam Steward", '["carpentry"]', 200000, 15),
        ("Ben Borrower", '["cooking"]', 140000, 45),
        ("Grace Patron", '["accounting"]', 400000, 10),
    ], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               persona,hobbies_json,skills_json,household_id,home_place_id)
               VALUES(?,?,'Male',36,'never_married','worker','Helpful.', '["gardening"]',?,1,?)""",
            (f"u{idx_a}", name, skill, pid),
        )
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,hunger) VALUES(?,?,?,?)", (idx_a, pid, money, hunger))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_commons_library_conserves_money_and_relieves_hunger(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 1 (1 * 48) triggers run_commons_library (day % 3 == 1)
    res = commons_library.run_commons_library(conn, 48, "test")
    assert res["loans"] == 1
    assert res["endowment_cents"] > 0
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
    h2 = conn.execute("SELECT hunger FROM agent_state WHERE agent_id=2").fetchone()["hunger"]
    assert h2 < 45
