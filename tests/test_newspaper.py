"""The Miniville Gazette: weekly front page composition."""
import sqlite3

import pytest

from miniville import db, newspaper, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id) VALUES('t0','Ada Ashbrook','Female',30,
           'married_present','teacher','[]',?)""", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(1,?)", (home,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_week_of():
    assert newspaper.week_of(0) == 0
    assert newspaper.week_of(6) == 0
    assert newspaper.week_of(7) == 1


def test_publish_week_writes_edition(conn):
    from miniville.events import emit
    emit(conn, 48, "life_event", a=1, importance=5,
         text="got married!", tag="marriage")
    emit(conn, 50, "arrival", a=1, importance=3,
         text="moved to Miniville from Springfield", tag="immigration")
    conn.commit()

    text = newspaper.publish_week(conn, 0, "test")
    assert "THE MINIVILLE GAZETTE" in text
    assert "Week 1" in text
    assert "Weddings & Births" in text
    assert "Arrivals" in text
    assert "Population this week" in text
    row = conn.execute("SELECT * FROM newspapers WHERE week=0").fetchone()
    assert row and row["text"] == text


def test_publish_week_is_idempotent(conn):
    newspaper.publish_week(conn, 0, "test")
    newspaper.publish_week(conn, 0, "test")
    assert conn.execute("SELECT COUNT(*) c FROM newspapers").fetchone()["c"] == 1


def test_quiet_week_still_publishes(conn):
    text = newspaper.publish_week(conn, 0, "test")
    assert "quiet week" in text.lower()
