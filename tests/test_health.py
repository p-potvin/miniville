"""Tests for v0.27 personal health, athletic vitality, hospital medical rounds,
and vitality-buffered Gompertz hazard."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import (
    db,
    economy,
    health,
    individual,
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
    hosp = c.execute("SELECT id FROM places WHERE name='Miniville General Hospital'").fetchone()["id"]
    c.execute(
        "INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
        (home,),
    )
    # 1: Physician at Miniville General Hospital
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id)
           VALUES('h1','Dr. Clara Chen','Female',44,'married_present','physician',
                  'Clara is a dedicated physician who runs marathons.',
                  '["running","swimming"]','["medicine"]',1,?,?)""",
        (home, hosp),
    )
    c.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days,rank)
           VALUES(1,?,'physician',22000,14,30,62,2)""",
        (hosp,),
    )
    # 2: Sick patient at home
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('h2','Ben Briar','Male',64,'married_present','baker',
                  'Ben is recovering from an illness.',
                  '["reading"]','["baking"]',1,?)""",
        (home,),
    )
    c.execute(
        "INSERT INTO agent_state(agent_id,place_id,money_cents,energy,stress,vitality) VALUES(1,?,500000,85,20,75)",
        (hosp,),
    )
    c.execute(
        "INSERT INTO agent_state(agent_id,place_id,money_cents,energy,stress,vitality) VALUES(2,?,500000,40,60,42)",
        (home,),
    )
    c.execute(
        "INSERT INTO conditions(agent_id,kind,until_tick) VALUES(2,'sick',120)"
    )
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    individual.ensure_profiles(c, "test")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_hospital_rounds_shorten_illness_and_boost_vitality(conn):
    res = health.due(conn, 48, "test")
    assert res["treated"] == 1

    until_after = conn.execute(
        "SELECT until_tick FROM conditions WHERE agent_id=2 AND kind='sick'"
    ).fetchone()["until_tick"]
    assert until_after < 120

    v1 = conn.execute("SELECT vitality FROM agent_state WHERE agent_id=1").fetchone()["vitality"]
    v2 = conn.execute("SELECT vitality FROM agent_state WHERE agent_id=2").fetchone()["vitality"]
    assert v1 > 75.0
    assert v2 > 42.0

    # High vitality lowers Gompertz hazard multiplier compared to low vitality
    assert health.vitality_hazard_multiplier(85.0) < 1.0 < health.vitality_hazard_multiplier(30.0)
