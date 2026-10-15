"""Tests for inhabitant individualism: temperaments, chronotypes, situated
personal events, caregiving, habits, and operator fate."""
from __future__ import annotations

import json
import sqlite3

import pytest

from miniville import (
    db,
    deviations,
    economy,
    engine,
    individual,
    narrator,
    needs,
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
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,professional_persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('t1','Ada Ashbrook','Female',32,'married_present','baker',
                  'Ada Ashbrook is an outgoing, ambitious baker who rises at dawn for morning runs and volunteering.',
                  'Dedicated, meticulous baker known for carpentry and repair work.',
                  '["baking", "running", "volunteering"]',
                  '["carpentry", "repair work", "organization"]',
                  1, ?)""",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,professional_persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('t2','Ben Ashbrook','Male',34,'married_present','writer_or_author',
                  'Ben Ashbrook is a quiet, reserved introvert who loves late night poetry and reading in solitude.',
                  'Independent writer known for storytelling.',
                  '["reading", "poetry", "chess"]',
                  '["writing", "storytelling"]',
                  1, ?)""",
        (home,),
    )
    for i in (1, 2):
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,500000)",
            (i, home),
        )
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_derive_profile_uses_persona_and_skills(conn):
    individual.ensure_profiles(conn, "test")
    ada = individual.profile_of(conn, 1, "test")
    ben = individual.profile_of(conn, 2, "test")

    assert ada["knack"] == "carpentry"
    assert ben["knack"] == "writing"
    assert ada["sociability"] > ben["sociability"]
    assert ada["industriousness"] > 0
    assert ada["wake_offset"] < 0
    assert ben["wake_offset"] > 0


def test_chronotype_shifts_wake_and_sleep_without_breaking_meals(conn):
    individual.ensure_profiles(conn, "test")
    ada_row = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    ben_row = conn.execute("SELECT * FROM agents WHERE id=2").fetchone()

    ada_plan = dict((t, a) for t, _, a in schedules.build_plan(conn, ada_row, 5, "test"))
    ben_plan = dict((t, a) for t, _, a in schedules.build_plan(conn, ben_row, 5, "test"))

    assert ada_plan[12] == "home"
    assert ben_plan[45] == "home"
    assert ada_plan[14] in ("eat", "eat_out")
    assert ben_plan[14] in ("eat", "eat_out")


def test_temperament_modulates_needs_decay_and_solitude_recovery(conn):
    individual.ensure_profiles(conn, "test")
    conn.execute("UPDATE agent_state SET activity='home', energy=60, stress=50, social=60")
    needs.apply_all_needs(conn)

    ada = conn.execute("SELECT * FROM agent_state WHERE agent_id=1").fetchone()
    ben = conn.execute("SELECT * FROM agent_state WHERE agent_id=2").fetchone()
    assert ben["social"] > ada["social"]
    assert ben["energy"] > ada["energy"]


def test_work_breakthrough_transfers_conserved_spot_bonus(conn):
    individual.ensure_profiles(conn, "test")
    diner = conn.execute(
        "SELECT id FROM places WHERE name='Riverside Diner'"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE businesses SET balance_cents=200000 WHERE place_id=?", (diner,)
    )
    conn.execute(
        "UPDATE agent_state SET place_id=?, activity='work', energy=85, stress=20 "
        "WHERE agent_id=1",
        (diner,),
    )
    before_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT SUM(balance_cents) s FROM businesses").fetchone()["s"]
    )
    row = conn.execute(
        """SELECT s.*, a.name, a.hobbies_json, a.skills_json
           FROM agent_state s JOIN agents a ON a.id=s.agent_id WHERE s.agent_id=1"""
    ).fetchone()
    prof = individual.profile_of(conn, 1, "test")
    from miniville.rng import rng_for
    assert individual._trigger_work_event(conn, row, prof, 20, rng_for("test", 1))

    after_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT SUM(balance_cents) s FROM businesses").fetchone()["s"]
    )
    assert after_total == before_total
    ev = conn.execute(
        "SELECT * FROM events WHERE a_id=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(ev["data"])["tag"] == "craft_breakthrough"


def test_home_incident_diy_vs_conserved_repair_bill(conn):
    individual.ensure_profiles(conn, "test")
    from miniville.rng import rng_for

    ada_row = conn.execute(
        """SELECT s.*, a.name, a.hobbies_json, a.skills_json
           FROM agent_state s JOIN agents a ON a.id=s.agent_id WHERE s.agent_id=1"""
    ).fetchone()
    assert individual._trigger_home_event(
        conn, ada_row, individual.profile_of(conn, 1, "test"), 22, rng_for("test", "diy")
    )
    ev1 = conn.execute(
        "SELECT data FROM events WHERE a_id=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(ev1["data"])["tag"] == "diy_fix"

    ben_before = conn.execute(
        "SELECT money_cents FROM agent_state WHERE agent_id=2"
    ).fetchone()["money_cents"]
    shop_before = conn.execute(
        "SELECT revenue_today FROM businesses b JOIN places p ON p.id=b.place_id "
        "WHERE p.name='Flats Auto & Hardware'"
    ).fetchone()["revenue_today"]
    ben_row = conn.execute(
        """SELECT s.*, a.name, a.hobbies_json, a.skills_json
           FROM agent_state s JOIN agents a ON a.id=s.agent_id WHERE s.agent_id=2"""
    ).fetchone()
    assert individual._trigger_home_event(
        conn, ben_row, individual.profile_of(conn, 2, "test"), 22, rng_for("test", "bill")
    )
    ben_after = conn.execute(
        "SELECT money_cents FROM agent_state WHERE agent_id=2"
    ).fetchone()["money_cents"]
    shop_after = conn.execute(
        "SELECT revenue_today FROM businesses b JOIN places p ON p.id=b.place_id "
        "WHERE p.name='Flats Auto & Hardware'"
    ).fetchone()["revenue_today"]
    assert ben_before - ben_after == shop_after - shop_before > 0


def test_caregiving_shortens_illness_and_warms_bond(conn, monkeypatch):
    monkeypatch.setattr(individual, "P_CAREGIVE", 1.0)
    monkeypatch.setattr(individual, "P_CONTAGION_HOME", 0.0)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,80,60,80,'spouse')"""
    )
    conn.execute(
        "INSERT INTO conditions(agent_id,kind,until_tick) VALUES(1,'sick',96)"
    )
    stats = individual.daily_happenings(conn, 48, "test")
    assert stats["caregiven"] == 1
    until = conn.execute(
        "SELECT until_tick FROM conditions WHERE agent_id=1 AND kind='sick'"
    ).fetchone()["until_tick"]
    assert until < 96
    ev = conn.execute(
        "SELECT data FROM events WHERE a_id=2 AND b_id=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(ev["data"])["tag"] == "caregiven"


def test_favorite_place_adopted_and_rivals_avoided(conn):
    individual.ensure_profiles(conn, "test")
    bean = conn.execute("SELECT id FROM places WHERE name='The Scenic Bean'").fetchone()["id"]
    tavern = conn.execute("SELECT id FROM places WHERE name='The Quaint Corner Tavern'").fetchone()["id"]
    emit(conn, 20, "encounter", place_id=bean, a=1, b=2, importance=2, tone="delightful")
    assert individual.update_favorite_places(conn, 0) == 2
    fav = conn.execute("SELECT favorite_place_id FROM agents WHERE id=1").fetchone()[0]
    assert fav == bean

    conn.execute(
        """INSERT OR REPLACE INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,30,-35,0,'rival')"""
    )
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days)
           VALUES(2,?,'bartender',10000,32,47,62)""",
        (tavern,),
    )
    ctx = schedules._plan_ctx(conn)
    assert tavern in ctx["rival_places"].get(1, set())


def test_lonely_deviation_seeks_out_friend_and_commits_next_tick(conn, monkeypatch):
    monkeypatch.setattr(deviations, "P_LONELY_OUT", 1.0)
    park = conn.execute("SELECT id FROM places WHERE name='Lush Meadow Park'").fetchone()["id"]
    home = conn.execute("SELECT home_place_id FROM agents WHERE id=1").fetchone()[0]
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,40,35,0,'friend')"""
    )
    conn.execute(
        "UPDATE agent_state SET place_id=?, activity='leisure' WHERE agent_id=2",
        (park,),
    )
    conn.execute(
        "UPDATE agent_state SET place_id=?, activity='home', mood='lonely' WHERE agent_id=1",
        (home,),
    )
    conn.execute(
        "INSERT INTO plans(agent_id,tick,place_id,activity) VALUES(1,21,?,'home')",
        (home,),
    )
    assert deviations.apply_deviations(conn, 20, "test") == 1
    st = conn.execute("SELECT place_id, activity FROM agent_state WHERE agent_id=1").fetchone()
    assert st["place_id"] == park and st["activity"] == "social_call"
    next_plan = conn.execute(
        "SELECT place_id, activity FROM plans WHERE agent_id=1 AND tick=21"
    ).fetchone()
    assert next_plan["place_id"] == park and next_plan["activity"] == "social_call"


def test_inject_fate_and_standing_accrual(conn):
    db.set_meta(conn, "tick", "48")
    for kind in ("breakthrough", "triumph", "epiphany", "care", "mishap"):
        res = individual.inject_fate(conn, "Ada", kind, seed="test")
        assert res["ok"], res
    reputation.accrue(conn, tick=2 * 48, seed="test")
    assert reputation.standing_of(conn, 1) > 0


def test_narrate_resident_raw_fallback(conn):
    individual.ensure_profiles(conn, "test")
    line = narrator.narrate_resident(conn, 1, provider="raw")
    assert line.startswith("[raw] Ada Ashbrook")
    assert "carpentry" in line
