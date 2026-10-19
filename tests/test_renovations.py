"""Tests for Home Renovations, Front-Porch Additions & Heirloom Gardens (v0.38)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, renovations, world


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Greenhill',6)")
    pid = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ash household',?)", (pid,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Evelyn Homeowner','Female',45,'married_present','physician',
                  'Thriving homeowner.','["gardening"]','["medicine"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Hank Carpenter','Male',38,'never_married','carpenter',
                  'Skilled builder.','["woodworking"]','["carpentry"]',1,?)""",
        (pid,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,500000)", (pid,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(2,?,200000)", (pid,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_home_renovation_conserves_money_and_expands_capacity(conn):
    home_pid = conn.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    cap_before = conn.execute("SELECT capacity FROM places WHERE id=?", (home_pid,)).fetchone()["capacity"]
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 3 (3 * 48) triggers run_renovations (day % 5 == 3)
    res = renovations.run_renovations(conn, 3 * 48, "test")
    assert res["renovations"] == 1
    assert res["spent_cents"] > 0
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
    cap_after = conn.execute("SELECT capacity FROM places WHERE id=?", (home_pid,)).fetchone()["capacity"]
    assert cap_after == cap_before + 1
