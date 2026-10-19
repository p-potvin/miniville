"""Tests for v0.25 seasonal town competitions, conserved Blue-Ribbon prize purse,
and win_blue_ribbon ambition completion."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import (
    ambitions,
    competitions,
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
    c.execute("UPDATE town_account SET balance_cents = 500000 WHERE id = 1")
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
           VALUES('cp1','Ada Baker','Female',34,'never_married','baker',
                  'Ada is an ambitious, industrious master baker.',
                  '["baking","cooking"]','["baking","pastry"]',1,?)""",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('cp2','Ben Clerk','Male',38,'never_married','clerk',
                  'Ben is an easygoing office clerk.',
                  '["reading"]','["bookkeeping"]',1,?)""",
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


def test_hold_competition_conserves_money_and_completes_blue_ribbon_ambition(conn):
    keepsakes.craft_keepsake(conn, 1, 10, "test")
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(1,'win_blue_ribbon',0,0,1,0.0,'active')"""
    )

    before_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents FROM town_account WHERE id=1").fetchone()[0]
    )
    res = competitions.hold_competition(conn, 12 * 48, "test", category_override="baking")
    assert res is not None
    assert res["winner_id"] == 1
    assert res["prize_cents"] == competitions.BLUE_RIBBON_PRIZE_CENTS

    after_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents FROM town_account WHERE id=1").fetchone()[0]
    )
    assert after_total == before_total

    done = ambitions.evaluate_ambitions(conn, 12 * 48, "test")
    assert done == 1
    assert ambitions.ambition_of(conn, 1)["status"] == "completed"
    assert len(competitions.ribbons_of(conn, 1)) == 1
