"""Tests for school youth skill discovery and conserved civic scholarships (v0.30)."""
from __future__ import annotations

import json
import sqlite3
import pytest

from miniville import db, economy, individual, youth, world
from miniville.events import emit


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
    school_id = c.execute("SELECT id FROM places WHERE name='Miniville School'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ash household',?)", (pid,))
    # Teacher
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id)
           VALUES('t1','Clara Teacher','Female',42,'never_married','teacher',
                  'Dedicated teacher.','["reading"]','["teaching"]',1,?,?)""",
        (pid, school_id),
    )
    c.execute(
        "INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days,rank) VALUES(1,?,'teacher',14000,14,30,62,1)",
        (school_id,),
    )
    # Youth student
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('s1','Leo Ash','Male',16,1,'never_married','student',
                  'Curious teen.','["running"]','["math"]',1,?)""",
        (pid,),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,200000)", (i, pid))
    c.execute("UPDATE town_account SET balance_cents = 500000 WHERE id = 1")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_school_discovery_and_conserved_civic_scholarship(conn):
    # Force discovery on Monday (day 1, tick 48)
    found = 0
    for s in ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10"):
        if youth.school_discoveries(conn, 2 * 48, s):
            found += 1
            break
    assert found == 1

    # Now graduate Leo at 18 and verify conserved scholarship from town_account
    conn.execute("UPDATE agents SET age=18, is_child=0 WHERE id=2")
    emit(conn, 96, "life_event", a=2, importance=2, text="came of age", tag="coming_of_age")
    before_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    awarded = youth.award_scholarships(conn, 96, "test")
    assert awarded == 1
    after_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT balance_cents s FROM town_account WHERE id=1").fetchone()["s"]
    )
    assert after_total == before_total
    edu = conn.execute("SELECT education_level FROM agents WHERE id=2").fetchone()["education_level"]
    assert edu == "bachelors"
