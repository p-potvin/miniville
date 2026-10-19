"""Tests for Chartered Craft Guilds and Conserved Masterpiece Grants (v0.33)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, guilds, individual, keepsakes, world


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
           VALUES('a1','Master Baker','Female',50,'married_present','baker',15,
                  'Master pastry chef.','["baking"]','["pastry baking"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Junior Baker','Male',22,'never_married','baker',2,
                  'Young baker.','["baking"]','["baking"]',1,?)""",
        (pid,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,400000)", (pid,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(2,?,150000)", (pid,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_guild_enrollment_and_conserved_masterpiece_grant(conn):
    added = guilds.ensure_guilds(conn, 0, "test")
    assert added == 2
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 3 (tick 3 * 48) is guild workshop day
    res = guilds.run_guild_workshops(conn, 3 * 48, "test")
    assert res["masterpieces"] == 1
    assert res["patronage_cents"] == guilds.GUILD_STIPEND_CENTS
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
    ks = keepsakes.keepsakes_of(conn, 2)
    assert len(ks) == 1
