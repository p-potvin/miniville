"""Tests for Resident Sabbaticals, Personal Memoirs & Town Masterclasses (v0.43)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, sabbaticals, world


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
           VALUES('a1','Professor Walter','Male',58,'married_present','teacher',15,
                  'Seasoned scholar.','["woodworking"]','["carpentry"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Young Student','Female',22,'never_married','clerk',2,
                  'Curious learner.','["reading"]','["retail"]',1,?)""",
        (pid,),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,250000)", (i, pid))
    c.execute("UPDATE town_account SET balance_cents = 500000 WHERE id = 1")
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_sabbatical_lyceum_conserves_money_and_records_session(conn):
    before_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    # Day 4 (4 * 48) triggers run_sabbatical_lyceum (day % 7 == 4)
    res = sabbaticals.run_sabbatical_lyceum(conn, 4 * 48, "test")
    assert res is not None
    assert res["stipend_cents"] == sabbaticals.LYCEUM_STIPEND_CENTS
    after_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    assert after_total == before_total
    summary = sabbaticals.sabbaticals_summary(conn)
    assert summary["total_sabbaticals"] == 1
