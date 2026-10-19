"""Tests for v0.22 personal life milestones: coming-of-age starter gifts,
decade birthdays, wedding anniversaries, and retirement send-offs."""
from __future__ import annotations

import json
import sqlite3

import pytest

from miniville import (
    db,
    economy,
    events,
    individual,
    keepsakes,
    milestones,
    seasons,
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
        "INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
        (home,),
    )
    diner = c.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    c.execute("UPDATE businesses SET balance_cents=200000 WHERE place_id=?", (diner,))

    # 1 & 2: Married parents (ages 40 and 66)
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,birth_day,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id)
           VALUES('m1','Ada Ashbrook','Female',40,10,'married_present','cook',
                  'Ada is a warm cook.', '["cooking"]', '["cooking"]', 1, ?, ?)""",
        (home, diner),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,birth_day,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id)
           VALUES('m2','Ben Ashbrook','Male',66,15,'married_present','baker',
                  'Ben is a veteran baker.', '["baking"]', '["baking"]', 1, ?, ?)""",
        (home, diner),
    )
    # 3: Child turning 18 on day 5
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,birth_day,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,is_child)
           VALUES('m3','Cora Ashbrook','Female',17,5,'never_married','student',
                  'Cora is turning 18.', '["reading"]', '[]', 1, ?, 1)""",
        (home,),
    )
    c.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,80,75,82,'spouse')"""
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,500000)", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(2,?,500000)", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(3,?,0)", (home,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    individual.ensure_profiles(c, "test")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_coming_of_age_gift_strictly_conserves_family_money(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 5 (tick = 5 * 48 = 240): Cora turns 18 via seasons.birthdays
    aged = seasons.birthdays(conn, 5 * 48, "test")
    assert aged == 1
    out = milestones.due(conn, 5 * 48, "test")
    assert out["coming_of_age_gifts"] == 1

    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
    cora_wallet = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=3").fetchone()["money_cents"]
    assert cora_wallet >= milestones.COMING_OF_AGE_GIFT_MIN


def test_decade_birthday_and_wedding_anniversary(conn):
    # Day 10 (tick = 10 * 48 = 480): Ada is 40 with birth_day=10
    out = milestones.due(conn, 10 * 48, "test")
    assert out["milestone_birthdays"] == 1

    # Find the exact anniversary day-of-year for pair (1, 2)
    from miniville.rng import seed_int
    anniv_doy = seed_int("test", "anniversary", 1, 2) % seasons.DAYS_PER_YEAR
    day = anniv_doy if anniv_doy > 0 else 365
    rom_before = conn.execute("SELECT romance FROM relationships WHERE a_id=1 AND b_id=2").fetchone()["romance"]
    res = milestones.due(conn, day * 48, "test")
    assert res["anniversaries"] == 1
    rom_after = conn.execute("SELECT romance FROM relationships WHERE a_id=1 AND b_id=2").fetchone()["romance"]
    assert rom_after > rom_before


def test_retirement_sendoff_conserves_bonus_and_awards_keepsake(conn):
    diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    before_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT SUM(balance_cents) s FROM businesses").fetchone()["s"]
    )
    events.emit(conn, 2 * 48, "life_event", place_id=diner, a=2,
                importance=3, text="retired from Riverside Diner at 66", tag="retired")
    out = milestones.due(conn, 2 * 48, "test")
    assert out["retirement_sendoffs"] == 1

    after_total = (
        conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
        + conn.execute("SELECT SUM(balance_cents) s FROM businesses").fetchone()["s"]
    )
    assert after_total == before_total
    ks = keepsakes.keepsakes_of(conn, 2)
    assert len(ks) == 1 and ks[0]["kind"] == "timepiece"
