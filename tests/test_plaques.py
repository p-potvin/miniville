"""Tests for Municipal Historical Plaques & Heritage Walking Tours (v0.42)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, plaques, world


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
    for idx_a, (name, standing, money) in enumerate([
        ("Elder Clara", 20, 220000),
        ("Rich Tourist", 4, 420000),
        ("Docent Guide", 6, 160000),
    ], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
               persona,hobbies_json,skills_json,household_id,home_place_id)
               VALUES(?,?,'Female',55,'never_married','teacher',?,'Historian.','["history"]','["teaching"]',1,?)""",
            (f"u{idx_a}", name, standing, pid),
        )
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)", (idx_a, pid, money))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_dedicate_plaque_and_tour_conserves_money(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 6 (6 * 48) triggers dedicate_plaque_and_tour (day % 10 == 6)
    res = plaques.dedicate_plaque_and_tour(conn, 6 * 48, "test")
    assert res is not None
    assert res["tip_cents"] > 0
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
    summary = plaques.plaques_summary(conn)
    assert summary["total_plaques"] == 1
