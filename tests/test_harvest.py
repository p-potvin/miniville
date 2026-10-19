"""Tests for Seasonal Harvest Cellars & Farmers' Co-op (v0.34)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, harvest, individual, world


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
           VALUES('a1','Grace Grower','Female',44,'never_married','gardener',
                  'Avid gardener.','["gardening","baking"]','["botany"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Rich Buyer','Male',39,'never_married','accountant',
                  'Loves fresh food.','["reading"]','["accounting"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a3','Hungry Neighbour','Male',29,'never_married','clerk',
                  'Tight budget.','["walking"]','["retail"]',1,?)""",
        (pid,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,hunger) VALUES(1,?,200000,20)", (pid,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,hunger) VALUES(2,?,450000,30)", (pid,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,hunger) VALUES(3,?,110000,55)", (pid,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_coop_day_conserves_money_and_relieves_hunger(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 2 (2 * 48) triggers run_coop_day (day % 3 == 2)
    res = harvest.run_coop_day(conn, 2 * 48, "test")
    assert res["boxes_sold"] == 1
    assert res["baskets_shared"] == 1
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
    h3 = conn.execute("SELECT hunger FROM agent_state WHERE agent_id=3").fetchone()["hunger"]
    assert h3 < 55
