"""Smoke tests: deterministic engine, no dataset needed (synthetic agents)."""
import sqlite3

import pytest

from miniville import db, engine, world, schedules, rng


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    # two synthetic agents + homes
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    for i, (name, age) in enumerate([("Ada Ashbrook", 30), ("Ben Briar", 32)]):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               hobbies_json,home_place_id) VALUES(?,?,?,?,?,?,?,?)""",
            (f"t{i}", name, "Female", age, "never_married", "teacher", '["Reading"]', home))
    for i in (1, 2):
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id) VALUES(?,?)", (i, home))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_deterministic_seed(conn):
    a = rng.rng_for("s", 1).random()
    b = rng.rng_for("s", 1).random()
    assert a == b


def test_world_has_venues(conn):
    n = conn.execute("SELECT COUNT(*) c FROM places WHERE kind!='home'").fetchone()["c"]
    assert n == len(world.VENUES)


def test_plans_cover_day(conn):
    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    plan = schedules.build_plan(conn, agent, 0, "test")
    assert len(plan) == 48
    assert all(1 <= row[0] <= len(plan) for row in enumerate(plan)) is not None


def test_tick_advances(conn):
    engine.step(conn, "test")
    assert db.get_meta(conn, "tick") == "1"


def test_full_day(conn):
    engine.run(conn, 48, "test")
    assert db.get_meta(conn, "tick") == "48"
    row = conn.execute("SELECT * FROM chronicle WHERE day=0").fetchone()
    assert row and "Day 1" in row["text"]
