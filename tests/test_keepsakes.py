"""Tests for v0.20 personal keepsakes, craftsman creations, gifting, and family heirlooms."""
from __future__ import annotations

import json
import sqlite3

import pytest

from miniville import (
    ambitions,
    db,
    economy,
    individual,
    keepsakes,
    mortality,
    world,
)


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute(
        "INSERT INTO places(name,kind,district,capacity) "
        "VALUES('H1','home','Downtown',6)"
    )
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        "INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('k1','Clara Ashbrook','Female',68,'married_present','carpenter',
                  'Clara is a master woodworker and curious artisan.',
                  '["woodworking","painting"]','["carpentry","woodworking"]',1,?)""",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('k2','David Ashbrook','Male',67,'married_present','baker',
                  'David is a warm baker and devoted husband.',
                  '["baking"]','["baking"]',1,?)""",
        (home,),
    )
    c.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,90,85,90,'spouse')"""
    )
    for i in (1, 2):
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,500000)",
            (i, home),
        )
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    individual.ensure_profiles(c, "test")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_craft_keepsake_and_complete_masterwork_ambition(conn):
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(1,'create_masterwork',0,0,1,0.0,'active')"""
    )
    kid = keepsakes.craft_keepsake(conn, 1, 24, "test")
    assert kid is not None
    items = keepsakes.keepsakes_of(conn, 1)
    assert len(items) == 1
    assert items[0]["kind"] == "woodwork"
    assert items[0]["creator_id"] == 1 and items[0]["owner_id"] == 1
    assert items[0]["history"][0]["action"] == "crafted"

    # Evaluating ambitions completes Clara's create_masterwork ambition
    done = ambitions.evaluate_ambitions(conn, 48, "test")
    assert done == 1
    assert ambitions.ambition_of(conn, 1)["status"] == "completed"


def test_gift_keepsake_and_estate_heirloom_inheritance(conn):
    kid = keepsakes.craft_keepsake(conn, 1, 10, "test")
    assert kid is not None

    # Gift from Clara (1) to David (2)
    assert keepsakes.gift_keepsake(conn, 1, 2, 20, "for their anniversary")
    david_items = keepsakes.keepsakes_of(conn, 2)
    assert len(david_items) == 1
    assert david_items[0]["owner_id"] == 2
    assert david_items[0]["history"][-1]["action"] == "gifted"

    # Now craft a second item owned by Clara (1), and simulate Clara's death
    kid2 = keepsakes.craft_keepsake(conn, 1, 30, "test")
    assert kid2 is not None
    clara_row = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    from miniville.rng import rng_for
    survivor = mortality._die(conn, clara_row, 96, rng_for("test", "die"))
    assert survivor == 2

    # David (2) should now own both items, and kid2 should be a Generation 1 Heirloom!
    david_after = keepsakes.keepsakes_of(conn, 2)
    assert len(david_after) == 2
    heirlooms = [x for x in david_after if x["id"] == kid2]
    assert len(heirlooms) == 1
    assert heirlooms[0]["generations"] == 1
    assert heirlooms[0]["history"][-1]["action"] == "inherited"
