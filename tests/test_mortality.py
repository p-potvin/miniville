"""Mortality: the hazard curve, and what a death does to the town."""
import sqlite3

import pytest

from miniville import db, mortality, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    for name, district in (("H1", "Downtown"), ("H2", "Lakeshore")):
        c.execute("INSERT INTO places(name,kind,district,capacity) VALUES(?,'home',?,6)",
                  (name, district))
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    other = c.execute("SELECT id FROM places WHERE name='H2'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
              (home,))
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Bell household',?)",
              (other,))
    # 1 elderly, 2 their spouse, 3 their child, 4 a close friend next door
    people = [("Ada Ashbrook", "Female", 90, 0, 1, home),
              ("Ben Ashbrook", "Male", 60, 0, 1, home),
              ("Cora Ashbrook", "Female", 8, 1, 1, home),
              ("Dee Bell", "Female", 55, 0, 2, other)]
    for i, (name, sex, age, child, hid, h) in enumerate(people):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               hobbies_json,household_id,home_place_id,is_child) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (f"t{i}", name, sex, age, "married_present", "teacher", "[]", hid, h, child))
        c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(?,?)", (i + 1, h))
    c.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,95,80,90,'spouse')""")
    c.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,4,70,60,0,'friend')""")
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_hazard_rises_with_age():
    assert mortality.daily_hazard(30) < mortality.daily_hazard(60)
    assert mortality.daily_hazard(60) < mortality.daily_hazard(85)


def test_illness_raises_hazard():
    assert mortality.daily_hazard(70, sick=True) > mortality.daily_hazard(70, sick=False)


def test_realistic_scale_is_rare():
    # a 40-year-old should not be dying at anything like a noticeable rate
    assert mortality.daily_hazard(40) < 1e-5


def test_death_settles_affairs(conn, monkeypatch):
    monkeypatch.setattr(mortality, "SCALE", 1e9)   # force every eligible adult to die
    deaths = mortality.daily_mortality(conn, 0, "test")

    assert deaths >= 1
    # the elderly resident is gone
    assert conn.execute("SELECT alive FROM agents WHERE id=1").fetchone()["alive"] == 0
    # their job and plans are released
    assert conn.execute("SELECT 1 FROM plans WHERE agent_id=1").fetchone() is None
    # the spouse is widowed, on both the agent row and the relationship
    assert conn.execute(
        "SELECT marital_status FROM agents WHERE id=2").fetchone()["marital_status"] == "widowed"
    assert conn.execute(
        "SELECT label FROM relationships WHERE a_id=1 AND b_id=2").fetchone()["label"] == "widowed"
    # the death is a historic event
    ev = conn.execute(
        "SELECT * FROM events WHERE kind='life_event' AND data LIKE '%\"death\"%'").fetchone()
    assert ev is not None and ev["importance"] == 5


def test_orphaned_children_are_taken_in(conn, monkeypatch):
    monkeypatch.setattr(mortality, "SCALE", 1e9)
    mortality.daily_mortality(conn, 0, "test")

    # both adults in the household died, so the child needs a new household
    child = conn.execute("SELECT * FROM agents WHERE id=3").fetchone()
    assert child["alive"] == 1
    assert child["household_id"] != 1
    assert child["home_place_id"] is not None


def test_children_are_not_subject_to_mortality(conn, monkeypatch):
    monkeypatch.setattr(mortality, "SCALE", 1e9)
    mortality.daily_mortality(conn, 0, "test")
    assert conn.execute("SELECT alive FROM agents WHERE id=3").fetchone()["alive"] == 1


def test_close_contacts_remember_the_dead(conn, monkeypatch):
    monkeypatch.setattr(mortality, "SCALE", 1e9)
    mortality.daily_mortality(conn, 0, "test")

    mem = conn.execute(
        "SELECT * FROM memories WHERE agent_id=4 AND kind='death'").fetchone()
    assert mem is not None
    assert "Ada Ashbrook" in mem["text"]


def test_dead_residents_get_no_plans(conn, monkeypatch):
    monkeypatch.setattr(mortality, "SCALE", 1e9)
    mortality.daily_mortality(conn, 0, "test")
    from miniville import schedules
    schedules.rebuild_day_plans(conn, 1, "test")
    assert conn.execute("SELECT 1 FROM plans WHERE agent_id=1").fetchone() is None
