"""Tests for Town Theater Troupe & Playbill Productions (v0.40)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, theater, world


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
    for idx_a, (name, hobby, money) in enumerate([
        ("Clara Playwright", '["writing"]', 250000),
        ("Julian Actor", '["theater","music"]', 250000),
        ("Rich Patron", '["reading"]', 450000),
    ], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               persona,hobbies_json,skills_json,household_id,home_place_id)
               VALUES(?,?,'Female',35,'never_married','artist','Creative resident.',?,'["storytelling"]',1,?)""",
            (f"u{idx_a}", name, hobby, pid),
        )
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)", (idx_a, pid, money))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_stage_production_conserves_money_and_records_playbill(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 4 (4 * 48) triggers stage_production (day % 6 == 4)
    res = theater.stage_production(conn, 4 * 48, "test")
    assert res is not None
    assert res["box_office_cents"] > 0
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
    summary = theater.theater_summary(conn)
    assert summary["total_productions"] == 1
