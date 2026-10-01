"""Calendar, holidays, seasonal schedules, and birthdays."""
import json
import sqlite3
from dataclasses import replace

import pytest

from miniville import db, encounters, engine, schedules, seasons, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute(
        "INSERT INTO places(name,kind,district,capacity) "
        "VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id) VALUES('t0','Ada Ashbrook','Female',30,
           'never_married','teacher','["Hiking"]',?)""",
        (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(1,?)", (home,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_calendar_dates_and_seasons():
    assert seasons.date_of(0) == (1, 1, 1)
    assert seasons.date_of(59) == (1, 3, 1)
    assert seasons.date_of(364) == (1, 12, 31)
    assert seasons.date_of(365) == (2, 1, 1)
    assert seasons.fmt_date(11) == "Jan 12, Year 1"
    assert [seasons.season_of(d) for d in (0, 59, 151, 243, 334)] == [
        "winter", "spring", "summer", "autumn", "winter",
    ]


def test_recurring_holidays():
    assert seasons.holiday_on(184).name == "Summer Fair"
    assert seasons.holiday_on(184 + 365).name == "Summer Fair"
    assert seasons.holiday_on(5) is None


def test_school_calendar_breaks():
    assert seasons.school_in_session(170) is False  # Jun 20
    assert seasons.school_in_session(257) is True   # Sep 15
    assert seasons.school_in_session(356) is False  # Dec 23
    assert seasons.school_in_session(1) is False    # Jan 2
    assert seasons.school_in_session(2) is True     # Jan 3


def test_summer_fair_plan_uses_holiday_venue(conn, monkeypatch):
    fair = next(h for h in seasons.HOLIDAYS if h.name == "Summer Fair")
    monkeypatch.setattr(
        seasons, "HOLIDAYS",
        tuple(replace(h, p_attend=1.0) if h == fair else h for h in seasons.HOLIDAYS))
    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    plan = schedules.build_plan(conn, agent, 184, "test")
    parks = {r["id"] for r in conn.execute(
        "SELECT id FROM places WHERE name='Lush Meadow Park'")}
    celebrated = [(tick, place) for tick, place, activity in plan
                  if activity == "celebrate"]
    assert celebrated == [(tick, next(iter(parks))) for tick in range(30, 44)]


def _add_job(conn, agent_id, place_name):
    place_id = conn.execute(
        "SELECT id FROM places WHERE name=?", (place_name,)).fetchone()["id"]
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,
           shift_end,work_days) VALUES(?,?,?,2500,16,34,62)""",
        (agent_id, place_id, "worker"))
    return place_id


def test_day_off_closes_town_hall_but_not_hospital(conn):
    _add_job(conn, 1, "Town Hall")
    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    fair_day = schedules.build_plan(conn, agent, 184, "test")
    weekday = schedules.build_plan(conn, agent, 186, "test")
    assert not any(activity == "work" for _, _, activity in fair_day)
    assert any(activity == "work" for _, _, activity in weekday)

    home = agent["home_place_id"]
    conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id) VALUES('hospital','Bea Bell','Female',30,
           'never_married','nurse','[]',?)""",
        (home,))
    conn.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(2,?)", (home,))
    _add_job(conn, 2, "Miniville General Hospital")
    hospital = conn.execute("SELECT * FROM agents WHERE id=2").fetchone()
    plan = schedules.build_plan(conn, hospital, 184, "test")
    hospital_id = conn.execute(
        "SELECT id FROM places WHERE name='Miniville General Hospital'").fetchone()["id"]
    assert any(place == hospital_id and activity == "work"
               for _, place, activity in plan)


def test_outdoor_appeal_is_higher_in_summer(conn):
    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    ctx = schedules._plan_ctx(conn)
    outdoor_ids = {
        v["id"] for v in ctx["venues"] if v["tags"] & seasons.OUTDOOR_TAGS
    }
    winter_visits = sum(
        place in outdoor_ids and activity == "leisure"
        for day in range(4, 34)
        for _, place, activity in schedules.build_plan(conn, agent, day, "test", ctx))
    summer_visits = sum(
        place in outdoor_ids and activity == "leisure"
        for day in range(185, 215)
        for _, place, activity in schedules.build_plan(conn, agent, day, "test", ctx))
    assert winter_visits < summer_visits


def test_birthdays_age_residents_and_children(conn):
    conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id,is_child)
           VALUES('child','Cora Ashbrook','Female',17,'never_married',
           'child','[]',?,1)""",
        (conn.execute("SELECT home_place_id FROM agents WHERE id=1").fetchone()[0],))
    conn.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(2,1)")
    conn.commit()

    adult_day = seasons.birthday_doy("test", 1) or 365
    child_day = seasons.birthday_doy("test", 2) or 365
    assert seasons.birthdays(conn, 0, "test") == 0
    assert conn.execute("SELECT age FROM agents WHERE id=1").fetchone()["age"] == 30

    assert seasons.birthdays(conn, adult_day * 48, "test") == 1
    assert conn.execute("SELECT age FROM agents WHERE id=1").fetchone()["age"] == 31
    assert seasons.birthdays(conn, child_day * 48, "test") == 1
    child = conn.execute("SELECT age,is_child FROM agents WHERE id=2").fetchone()
    assert tuple(child) == (18, 0)
    events = conn.execute(
        "SELECT data FROM events WHERE kind='life_event' AND a_id=2").fetchall()
    assert any(json.loads(row["data"]).get("tag") == "coming_of_age" for row in events)


def test_engine_announces_holiday_at_day_start(conn):
    db.set_meta(conn, "tick", str(184 * 48))
    engine.step(conn, "test")
    event = conn.execute(
        "SELECT data FROM events WHERE day=184 AND kind='town_event'").fetchall()
    assert any(json.loads(row["data"]).get("tag") == "holiday" for row in event)


def _put_adults_in_park(conn, count=60):
    park_id = conn.execute(
        "SELECT id FROM places WHERE name='Lush Meadow Park'").fetchone()["id"]
    home_id = conn.execute(
        "SELECT home_place_id FROM agents WHERE id=1").fetchone()["home_place_id"]
    conn.execute(
        "UPDATE agent_state SET place_id=?,activity='celebrate' WHERE agent_id=1",
        (park_id,))
    for index in range(2, 61):
        agent_id = conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               hobbies_json,home_place_id)
               VALUES(?,?,?,30,'never_married','resident','["Hiking"]',?)""",
            (f"crowd-{index}", f"Crowd Resident {index}", "Female", home_id)
        ).lastrowid
        conn.execute(
            """INSERT INTO agent_state(agent_id,place_id,activity)
               VALUES(?,?,'celebrate')""",
            (agent_id, park_id))
    return park_id


def test_holiday_venue_lifts_pair_cap(conn):
    park_id = _put_adults_in_park(conn)
    conn.executemany(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
           label,interactions,last_met_tick) VALUES(?,?,40,0,0,'friend',0,0)""",
        [(left, right) for left in range(1, 61) for right in range(left + 1, 61)])
    conn.commit()

    holiday_interactions = encounters.run_encounters(
        conn, 184 * 48 + 32, "test", max_pairs_per_place=2)
    weekday_interactions = encounters.run_encounters(
        conn, 186 * 48 + 32, "test", max_pairs_per_place=2)
    assert holiday_interactions > 2
    assert weekday_interactions <= 2


def test_holiday_fair_opens_stranger_interactions(conn):
    _put_adults_in_park(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM relationships").fetchone()[0] == 0

    holiday_interactions = encounters.run_encounters(
        conn, 184 * 48 + 32, "holiday-mingling", max_pairs_per_place=30)
    conn.execute("DELETE FROM relationships")
    weekday_interactions = encounters.run_encounters(
        conn, 186 * 48 + 32, "holiday-mingling", max_pairs_per_place=30)
    assert holiday_interactions > weekday_interactions, (
        holiday_interactions, weekday_interactions)
