"""Tests for town historical eras and Hall of Fame record-holders (v0.31)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import competitions, correspondence, db, economy, history, individual, keepsakes, mentorship, world


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
           VALUES('a1','Ada Ash','Female',48,'married_present','baker',25,
                  'Pillar of town.','["baking"]','["pastry"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Ben Ash','Male',24,'married_present','baker',10,
                  'Apprentice baker.','["baking"]','["cooking"]',1,?)""",
        (pid,),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,400000)", (i, pid))
    c.execute("INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label) VALUES(1,2,80,75,90,'spouse')")
    keepsakes.create_keepsake(c, 1, 10, "test")
    correspondence.write_diary_entry(c, 1, 10, "promoted", "Earned promotion today.")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_ensure_eras_and_hall_of_fame(conn):
    n = history.ensure_eras(conn, 35 * 48)
    assert n >= 1
    summary = history.history_summary(conn)
    assert len(summary["eras"]) >= 1
    cats = {h["category"] for h in summary["hall_of_fame"]}
    assert "Pillar of Miniville" in cats
    assert "Master Artisan" in cats
    assert "Enduring Sweethearts" in cats
    assert "Town Diarist" in cats


def test_history_due_records_30_day_era(conn):
    res = history.due(conn, 30 * 48, "test")
    assert res["eras_recorded"] >= 1
