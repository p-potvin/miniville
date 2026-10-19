"""Tests for personal ambitions: assignment, behavioral nudges, and completion."""
from __future__ import annotations

import json
import sqlite3

import pytest

from miniville import (
    ambitions,
    db,
    economy,
    encounters,
    individual,
    reputation,
    schedules,
    world,
)
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
    c.execute(
        "INSERT INTO places(name,kind,district,capacity) "
        "VALUES('H1','home','Downtown',6)"
    )
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        "INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
        (home,),
    )
    # 1: Unemployed adult -> should adopt find_a_job
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Ada Ashbrook','Female',29,'never_married','baker',
                  'Ada is an ambitious baker looking for work.',
                  '["baking"]','["baking"]',1,?)""",
        (home,),
    )
    # 2: Employed frugal adult -> save_nest_egg or climb_the_ladder
    diner = c.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id)
           VALUES('a2','Ben Briar','Male',35,'married_present','cook',
                  'Ben is a frugal, practical cook who budgets carefully.',
                  '["cooking"]','["budgeting"]',1,?,?)""",
        (home, diner),
    )
    c.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days,rank)
           VALUES(2,?,'cook',12000,14,30,62,2)""",
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


def test_unemployed_adult_adopts_and_completes_find_a_job(conn):
    n = ambitions.assign_ambitions(conn, 0, "test")
    assert n == 2
    am1 = ambitions.ambition_of(conn, 1)
    assert am1 is not None and am1["kind"] == "find_a_job"

    # Hire Ada at the diner -> next evaluation completes her ambition
    diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    conn.execute("UPDATE agents SET work_place_id=? WHERE id=1", (diner,))
    done = ambitions.evaluate_ambitions(conn, 48, "test")
    assert done == 1
    am1_after = ambitions.ambition_of(conn, 1)
    assert am1_after["status"] == "completed" and am1_after["progress"] == 1.0
    ev = conn.execute(
        "SELECT data FROM events WHERE a_id=1 AND kind='life_event' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(ev["data"])["tag"] == "ambition_achieved"


def test_save_nest_egg_completes_when_wallet_grows(conn):
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(2,'save_nest_egg',0,500000,620000,0.0,'active')"""
    )
    conn.execute("UPDATE agent_state SET money_cents=560000 WHERE agent_id=2")
    assert ambitions.evaluate_ambitions(conn, 48, "test") == 0
    assert ambitions.ambition_of(conn, 2)["progress"] == pytest.approx(0.5)

    conn.execute("UPDATE agent_state SET money_cents=625000 WHERE agent_id=2")
    assert ambitions.evaluate_ambitions(conn, 96, "test") == 1
    assert ambitions.ambition_of(conn, 2)["status"] == "completed"


def test_climb_the_ladder_completes_on_breakthroughs_or_promotion(conn):
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(2,'climb_the_ladder',0,0,1,0.0,'active')"""
    )
    conn.execute("UPDATE jobs SET rank=0 WHERE agent_id=2")
    emit(conn, 10, "life_event", a=2, importance=2, text="bt1", tag="craft_breakthrough")
    assert ambitions.evaluate_ambitions(conn, 20, "test") == 0
    emit(conn, 30, "life_event", a=2, importance=2, text="bt2", tag="craft_breakthrough")
    assert ambitions.evaluate_ambitions(conn, 48, "test") == 1
    assert ambitions.ambition_of(conn, 2)["status"] == "completed"


def test_find_true_love_boosts_pair_score_and_completes_on_sweetheart(conn):
    for aid in (1, 2):
        conn.execute(
            """INSERT OR REPLACE INTO ambitions(
                   agent_id,kind,started_tick,baseline_value,target_value,progress,status)
               VALUES(?, 'find_true_love', 0, 0, 1, 0.0, 'active')""",
            (aid,),
        )
    ctx = encounters._affinity_ctx(conn)
    assert {1, 2} <= ctx["romantics"]
    score_with = encounters._pair_score(ctx, set(), 1, 2, 0.0)
    ctx_no_rom = {**ctx, "romantics": set()}
    score_without = encounters._pair_score(ctx_no_rom, set(), 1, 2, 0.0)
    assert score_with > score_without

    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,40,50,85,'sweetheart')"""
    )
    assert ambitions.evaluate_ambitions(conn, 48, "test") == 2


def test_make_peace_completes_when_grudge_clears(conn):
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,25,-25,0,'rival')"""
    )
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(1,'make_peace',0,2,1,0.0,'active')"""
    )
    assert ambitions.evaluate_ambitions(conn, 48, "test") == 0
    conn.execute("UPDATE relationships SET affinity=2.0, label='acquaintance' WHERE a_id=1 AND b_id=2")
    assert ambitions.evaluate_ambitions(conn, 96, "test") == 1
    assert ambitions.ambition_of(conn, 1)["status"] == "completed"
