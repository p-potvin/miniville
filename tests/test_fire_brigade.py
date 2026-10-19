"""Tests for Volunteer Fire Brigade, Safety Drills, and Fire Rebuild Drives (v0.36)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, fire_brigade, individual, world


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
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Captain Frank','Male',38,'married_present','carpenter',12,
                  'Resilient volunteer.','["running"]','["carpentry"]',1,?)""",
        (pid,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,vitality) VALUES(1,?,300000,78)", (pid,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_brigade_recruitment_drills_and_fire_rebuild(conn):
    added = fire_brigade.ensure_brigade(conn, 0, "test")
    assert added == 1
    diner_id = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    conn.execute("UPDATE businesses SET status='closed', reopen_day=15 WHERE place_id=?", (diner_id,))
    # Day 2 (2 * 48) triggers run_brigade_drills (day % 4 == 2)
    res = fire_brigade.run_brigade_drills(conn, 2 * 48, "test")
    assert res["drills"] == 1
    assert res["rebuilds"] == 1
    new_reopen = conn.execute("SELECT reopen_day FROM businesses WHERE place_id=?", (diner_id,)).fetchone()["reopen_day"]
    assert new_reopen == 14
