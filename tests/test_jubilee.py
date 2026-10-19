"""Tests for Annual Town Founders' Jubilee, Time Capsules & Key to Miniville (v0.39)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, jubilee, world


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
    c.execute("INSERT INTO households(name,home_place_id,tradition) VALUES('Ash household',?,'Sunday potluck dinners')", (pid,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Mayor Clara','Female',52,'married_present','teacher',22,
                  'Beloved pillar.','["reading"]','["teaching"]',1,?)""",
        (pid,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,300000)", (pid,))
    c.execute("UPDATE town_account SET balance_cents = 500000 WHERE id = 1")
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_celebrate_jubilee_seals_capsule_and_conserves_money(conn):
    before_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    # Day 20 (20 * 48) triggers celebrate_jubilee
    res = jubilee.celebrate_jubilee(conn, 20 * 48, "test")
    assert res is not None
    assert res["honorarium_cents"] == jubilee.HONORARIUM_CENTS
    after_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    assert after_total == before_total
    summary = jubilee.jubilee_summary(conn)
    assert summary["total_capsules"] == 1
