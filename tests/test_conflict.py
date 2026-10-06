"""Influence, and the three things a town does about a grudge."""
import json
import sqlite3

import pytest

from miniville import conflict, db, economy, groups, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)", (home,))
    for i, name in enumerate(["Ada Ashbrook", "Ben Ashbrook", "Cass Ashbrook"], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               hobbies_json,household_id,home_place_id,standing)
               VALUES(?,?,'Female',40,0,'married_present','clerk','[]',1,?,10)""",
            (f"t{i}", name, home))
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                  (i, home, 500_000))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def _rivals(conn, a, b, affinity=-40.0):
    lo, hi = min(a, b), max(a, b)
    conn.execute(
        """INSERT OR REPLACE INTO relationships(a_id,b_id,familiarity,affinity,
           romance,label) VALUES(?,?,20,?,0,'rival')""", (lo, hi, affinity))
    conn.commit()


def test_influence_counts_a_seat_and_a_flock(conn):
    plain = conflict.influence_of(conn, 1)
    conn.execute("INSERT INTO council(seat,agent_id,elected_tick) VALUES(1,1,0)")
    seated = conflict.influence_of(conn, 1)
    assert seated > plain                      # a seat is worth a lot
    conn.execute("INSERT INTO groups(name,kind) VALUES('the test circle','club')")
    conn.execute("INSERT INTO memberships(group_id,agent_id,role) VALUES(1,1,'officer')")
    conn.commit()
    leading = conflict.influence_of(conn, 1)
    assert leading > seated                    # and leading people is worth more


def test_children_have_no_influence(conn):
    conn.execute("INSERT INTO agents(uuid,name,sex,age,is_child,household_id,"
                 "home_place_id) VALUES('kid','Kid Ashbrook','Female',8,1,1,"
                 "(SELECT id FROM places WHERE name='H1'))")
    conn.commit()
    assert conflict.influence_of(conn, 4) == 0.0


def test_a_slander_costs_the_target_standing(conn, monkeypatch):
    monkeypatch.setattr(conflict, "SLANDER_P_DAY", 1.0)
    _rivals(conn, 1, 2)
    told = conflict.slanders(conn, tick=7 * 48, seed="test")
    assert told == 1
    standings = {r["id"]: r["standing"] for r in conn.execute(
        "SELECT id, standing FROM agents WHERE id IN (1,2)")}
    assert min(standings.values()) < 10         # one of them lost face
    row = conn.execute(
        "SELECT data FROM events WHERE data LIKE '%slander%'").fetchone()
    assert row is not None
    assert json.loads(row["data"])["bite"] >= conflict.SLANDER_BITE


def test_a_slander_carries_further_from_a_well_connected_gossip(conn, monkeypatch):
    monkeypatch.setattr(conflict, "SLANDER_P_DAY", 1.0)
    _rivals(conn, 1, 2)
    # give the speaker a wide acquaintance
    for i in range(10, 80):
        conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,household_id,
               home_place_id,standing) VALUES(?,?,'Male',40,0,1,?,10)""",
            (f"x{i}", f"Neighbour {i}",
             conn.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]))
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
               VALUES(?,?,20,10,0,'friend')""", (1, i))
    conn.commit()
    conflict.slanders(conn, tick=7 * 48, seed="test")
    data = json.loads(conn.execute(
        "SELECT data FROM events WHERE data LIKE '%slander%'").fetchone()["data"])
    assert data["reach"] > 10
    assert data["bite"] > conflict.SLANDER_BITE


def test_a_boycott_costs_the_venue_its_customers(conn, monkeypatch):
    tavern = conn.execute("SELECT id FROM places WHERE name='The Quaint Corner Tavern'").fetchone()["id"]
    # the club meets somewhere else — nobody boycotts their own meeting place
    hall = conn.execute("SELECT id FROM places WHERE name='Miniville Community Center'").fetchone()["id"]
    conn.execute(
        """INSERT INTO groups(name,kind,venue_id) VALUES('the test circle','club',?)""",
        (hall,))
    # a boycott needs a group with enough people in it to matter
    for aid in range(1, 9):
        if aid > 3:
            conn.execute(
                """INSERT INTO agents(uuid,name,sex,age,is_child,household_id,
                   home_place_id,standing) VALUES(?,?,'Male',40,0,1,?,10)""",
                (f"g{aid}", f"Club member {aid}",
                 conn.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]))
            conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) "
                         "VALUES(?,1,100000)", (aid,))
        conn.execute("INSERT INTO memberships(group_id,agent_id,role) "
                     "VALUES(1,?,'member')", (aid,))
    conn.commit()
    # member 1 has a grievance with somebody who works at the tavern
    conn.execute("INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,"
                 "shift_end,work_days) VALUES(2,?,'barkeep',10000,16,30,62)", (tavern,))
    _rivals(conn, 1, 2)
    monkeypatch.setattr(conflict, "SLANDER_P_DAY", 0.0)
    monkeypatch.setattr(conflict, "BOYCOTT_P_DAY", 1.0)
    started = None
    for day in range(1, 400):                   # sweep days for the draw
        started = conflict.boycotts(conn, tick=day * 48, seed="test")
        if started:
            break
    assert started, "no boycott was ever drawn"
    assert conflict.boycotting(conn, 1, tavern, day) is True
    assert conflict.boycotting(conn, 1, tavern, day + conflict.BOYCOTT_DAYS + 1) is False

    # and the consequence: the member's custom stops reaching the venue
    conn.execute("INSERT INTO businesses(place_id,balance_cents) VALUES(?,100000)",
                 (tavern,))
    conn.execute("UPDATE agent_state SET activity='leisure', place_id=? WHERE agent_id=1",
                 (tavern,))
    conn.commit()
    economy.charge_spending(conn, tick=day * 48 + 40)
    conn.commit()
    boycotted = conn.execute("SELECT revenue_today r FROM businesses WHERE place_id=?",
                             (tavern,)).fetchone()["r"] or 0
    assert boycotted == 0                    # boycotted: the custom never lands

    # ...whereas a resident who is not in the boycotting group does spend there
    home = conn.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,household_id,home_place_id,
           standing) VALUES('outsider','Outsider','Male',40,0,1,?,10)""", (home,))
    outsider = conn.execute("SELECT id FROM agents WHERE uuid='outsider'").fetchone()["id"]
    conn.execute("INSERT INTO agent_state(agent_id,place_id,activity,money_cents) "
                 "VALUES(?,?,'leisure',500000)", (outsider, tavern))
    conn.commit()
    economy.charge_spending(conn, tick=day * 48 + 40)
    conn.commit()
    assert (conn.execute("SELECT revenue_today r FROM businesses WHERE place_id=?",
                         (tavern,)).fetchone()["r"] or 0) > 0


def test_a_congregation_can_split(conn, monkeypatch):
    monkeypatch.setattr(conflict, "SCHISM_P_DAY", 1.0)
    church = conn.execute("SELECT id FROM places WHERE name='First Congregational Church'").fetchone()["id"]
    conn.execute(
        "INSERT INTO groups(name,kind,venue_id,meets_day,meets_tick) "
        "VALUES('the test congregation','congregation',?,6,20)", (church,))
    for aid in range(1, 21):
        if aid > 3:
            conn.execute(
                """INSERT INTO agents(uuid,name,sex,age,is_child,household_id,
                   home_place_id,standing) VALUES(?,?,'Female',40,0,1,?,5)""",
                (f"m{aid}", f"Member {aid}",
                 conn.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]))
            conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) "
                         "VALUES(?,1,100000)", (aid,))
        conn.execute("INSERT INTO memberships(group_id,agent_id,role) "
                     "VALUES(1,?,'member')", (aid,))
    conn.commit()
    out = conflict.schisms(conn, tick=30 * 48, seed="test")
    assert out and out[0]["left"] >= 4
    assert conn.execute("SELECT COUNT(*) n FROM groups").fetchone()["n"] == 2
    left = conn.execute(
        """SELECT COUNT(*) n FROM memberships m JOIN groups g ON g.id=m.group_id
           WHERE g.name LIKE '%breakaway%'""").fetchone()["n"]
    assert left == out[0]["left"]
    assert conn.execute("SELECT 1 FROM events WHERE data LIKE '%schism%'").fetchone()


def test_due_only_acts_on_its_own_days(conn):
    assert conflict.due(conn, tick=1 * 48, seed="test") == {}      # not day 7 or 30
