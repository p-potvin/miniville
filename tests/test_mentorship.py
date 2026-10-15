"""Tests for v0.18 intergenerational mentorship, skill inheritance, and household traditions."""
from __future__ import annotations

import json
import sqlite3

import pytest

from miniville import (
    ambitions,
    db,
    economy,
    individual,
    mentorship,
    reputation,
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
        "INSERT INTO households(name,home_place_id) VALUES('Miller household',?)",
        (home,),
    )
    diner = c.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]

    # 1: Seasoned mentor (age 48, senior cook, knack='woodworking')
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id,standing)
           VALUES('m1','Martha Miller','Female',48,'married_present','cook',
                  'Martha is a seasoned master woodworker and senior cook.',
                  '["woodworking","baking"]','["woodworking","pastry"]',1,?,?,12)""",
        (home, diner),
    )
    c.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days,rank,base_wage_cents)
           VALUES(1,?,'cook',14000,14,30,62,1,12000)""",
        (diner,),
    )
    # 2: Young apprentice at the same workplace (age 21, rank 0, generic skills)
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id,standing)
           VALUES('m2','Leo Miller','Male',21,'never_married','cook',
                  'Leo is a curious young kitchen hand eager to learn a craft.',
                  '["running"]','[]',1,?,?,0)""",
        (home, diner),
    )
    c.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days,rank,base_wage_cents)
           VALUES(2,?,'cook',10000,14,30,62,0,10000)""",
        (diner,),
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


def test_household_traditions_derived_and_celebrated_on_sunday(conn):
    n = mentorship.ensure_traditions(conn, "test")
    assert n == 1
    hh = conn.execute("SELECT tradition FROM households WHERE id=1").fetchone()
    assert hh["tradition"] is not None and "baking" in hh["tradition"] or "workshop" in hh["tradition"]

    # Day 6 is Sunday (day % 7 == 6 -> tick = 6 * 48 = 288)
    conn.execute("UPDATE agent_state SET social=50, fun=50, stress=40")
    emitted = mentorship.sunday_traditions(conn, 6 * 48, "test")
    assert emitted == 1
    st = conn.execute("SELECT social, fun, stress FROM agent_state WHERE agent_id=1").fetchone()
    assert st["social"] == 54 and st["fun"] == 54 and st["stress"] == 37
    ev = conn.execute(
        "SELECT data FROM events WHERE kind='life_event' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(ev["data"])["tag"] == "family_tradition"


def test_mentorship_pairing_progression_and_skill_inheritance(conn, monkeypatch):
    monkeypatch.setattr(mentorship, "P_DAILY_LESSON", 1.0)
    paired = mentorship.pair_mentors(conn, 0, "test")
    assert paired == 1
    m = conn.execute("SELECT * FROM mentorships WHERE apprentice_id=2").fetchone()
    assert m is not None and m["mentor_id"] == 1 and m["craft"] == "woodworking"

    # Give Martha the mentor_a_protege ambition
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(1,'mentor_a_protege',0,0,1,0.0,'active')"""
    )

    wage_before = conn.execute("SELECT wage_cents FROM jobs WHERE agent_id=2").fetchone()["wage_cents"]
    for step_day in range(1, mentorship.TARGET_LESSONS + 1):
        res = mentorship.step_mentorships(conn, step_day * 48, "test")
        if step_day < mentorship.TARGET_LESSONS:
            assert res["graduated"] == 0
        else:
            assert res["graduated"] == 1

    # Apprentice should now hold 'woodworking' in skills_json and a bumped wage
    app_row = conn.execute("SELECT skills_json FROM agents WHERE id=2").fetchone()
    assert "woodworking" in json.loads(app_row["skills_json"])
    wage_after = conn.execute("SELECT wage_cents FROM jobs WHERE agent_id=2").fetchone()["wage_cents"]
    assert wage_after > wage_before

    # Evaluating ambitions completes Martha's mentor_a_protege ambition
    done = ambitions.evaluate_ambitions(conn, 6 * 48, "test")
    assert done == 1
    assert ambitions.ambition_of(conn, 1)["status"] == "completed"

    # Reputation accrues for both apprentice and mentor on graduation day
    reputation.accrue(conn, tick=6 * 48, seed="test")
    assert reputation.standing_of(conn, 2) >= 2
