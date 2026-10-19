"""Tests for Inter-District Sports Leagues & Saturday Town Derby (v0.35)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, sports, world


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('HD','home','Downtown',6)")
    pid_d = c.execute("SELECT id FROM places WHERE name='HD'").fetchone()["id"]
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('HG','home','Greenhill',6)")
    pid_g = c.execute("SELECT id FROM places WHERE name='HG'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('D household',?)", (pid_d,))
    c.execute("INSERT INTO households(name,home_place_id) VALUES('G household',?)", (pid_g,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Dan Dynamo','Male',27,'never_married','carpenter',
                  'Athletic runner.','["running"]','["carpentry"]',1,?)""",
        (pid_d,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Gina Green','Female',26,'never_married','teacher',
                  'Soccer player.','["sports"]','["teaching"]',2,?)""",
        (pid_g,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,vitality) VALUES(1,?,250000,75)", (pid_d,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,vitality) VALUES(2,?,250000,70)", (pid_g,))
    c.execute("UPDATE town_account SET balance_cents = 500000 WHERE id = 1")
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_saturday_derby_conserves_money_and_updates_standings(conn):
    before_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    # Day 5 (5 * 48) is Saturday
    res = sports.run_saturday_derby(conn, 5 * 48, "test")
    assert res is not None
    assert res["purse_cents"] == sports.MVP_PURSE_CENTS
    after_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    assert after_total == before_total
    summary = sports.sports_summary(conn)
    assert summary["total_matches"] == 1
    assert sum(s["wins"] for s in summary["standings"]) == 1
