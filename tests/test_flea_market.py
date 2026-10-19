"""Tests for weekend flea market, secondhand keepsake trade, and money conservation (v0.29)."""
from __future__ import annotations

import json
import sqlite3
import pytest

from miniville import db, economy, flea_market, individual, keepsakes, world


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
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Ada Seller','Female',40,'never_married','carpenter',
                  'Master woodworker.','["woodworking"]','["carpentry"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Ben Buyer','Male',38,'never_married','accountant',
                  'Collector.','["reading"]','["accounting"]',1,?)""",
        (pid,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,120000)", (pid,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(2,?,500000)", (pid,))
    # Give Ada 2 keepsakes
    keepsakes.create_keepsake(c, 1, 10, "test")
    keepsakes.create_keepsake(c, 1, 20, "test")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_weekend_flea_market_conserves_money_and_transfers_ownership(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 5 is Saturday (5 * 48 = 240)
    res = flea_market.run_weekend_market(conn, 5 * 48, "test")
    assert res["trades"] == 1
    assert res["volume_cents"] > 0
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total

    ben_ks = keepsakes.keepsakes_for_resident(conn, 2)
    assert len(ben_ks) == 1
    hist = ben_ks[0]["history"]
    assert any(h.get("event") == "purchased" for h in hist)
