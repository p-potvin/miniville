"""Tests for v0.23 Town Cultural Commons: curating exhibits, audience appreciation,
and publish_or_exhibit ambition completion."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import (
    ambitions,
    culture,
    db,
    economy,
    individual,
    keepsakes,
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
           VALUES('c1','Elena Poet','Female',34,'never_married','writer_or_author',
                  'Elena is a curious, outgoing poet and author.',
                  '["poetry","writing"]','["writing","poetry"]',1,?)""",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('c2','Marcus Reader','Male',41,'never_married','teacher',
                  'Marcus loves reading local books at the library.',
                  '["reading"]','["teaching"]',1,?)""",
        (home,),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,500000)", (i, home))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    individual.ensure_profiles(c, "test")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_curate_exhibit_and_earn_acclaim_and_ambition(conn):
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(1,'publish_or_exhibit',0,0,3,0.0,'active')"""
    )
    kid = keepsakes.craft_keepsake(conn, 1, 10, "test")
    assert kid is not None

    exhibited = culture.curate_exhibits(conn, 48, "test")
    assert exhibited == 1
    commons = culture.list_cultural_commons(conn)
    assert len(commons) == 1
    assert commons[0]["venue_name"] == "Miniville Public Library"

    # Run 3 days of audience appreciation so appreciations reaches 3 (ACCLAIM_THRESHOLDS[0])
    acclaimed_total = 0
    for d in range(1, 4):
        res = culture.experience_culture(conn, d * 48, "test")
        assert res["appreciated"] == 1
        acclaimed_total += res["acclaimed"]
    assert acclaimed_total == 1

    # Evaluating ambitions completes Elena's publish_or_exhibit ambition
    done = ambitions.evaluate_ambitions(conn, 4 * 48, "test")
    assert done == 1
    assert ambitions.ambition_of(conn, 1)["status"] == "completed"
