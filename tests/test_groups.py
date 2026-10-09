"""Affiliations: faith from the persona, congregations and clubs, gatherings."""
import json
import sqlite3

import pytest

from miniville import db, groups, schedules, world


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
    # eight Catholic neighbours, four readers, one child
    for i in range(1, 9):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,occupation,hobbies_json,
               traits_json,household_id,home_place_id)
               VALUES(?,?,'Female',40,0,'teacher',?,?,1,?)""",
            (f"t{i}", f"Catholic Neighbour {i}",
             json.dumps(["reading", "gardening"]),
             json.dumps({"background": "Raised in a Catholic family in Boston."}), home))
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                  (i, home, 500_000))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,occupation,hobbies_json,
           household_id,home_place_id) VALUES('kid','Kid Ashbrook','Female',7,1,'student',
           '[]',1,?)""", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(9,?,0)", (home,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_faith_is_read_from_the_personas_own_words():
    assert groups.faith_of("Raised in a Catholic family in Boston") == "catholic"
    assert groups.faith_of("devout Muslim household") == "muslim"
    assert groups.faith_of("grew up Baptist in Georgia") == "baptist"
    assert groups.faith_of("a secular household") == "unaffiliated"
    # most personas say nothing, and saying nothing is left as it is
    assert groups.faith_of("loves hiking and woodworking") is None
    assert groups.faith_of(None) is None


def test_assign_faith_and_children_inherit(conn):
    out = groups.assign_faith(conn)
    assert out["named"] == 8
    assert out["inherited"] == 1                    # the child took the household's
    assert conn.execute("SELECT faith FROM agents WHERE id=9").fetchone()["faith"] == "catholic"


def test_a_quorum_founds_a_congregation(conn):
    groups.assign_faith(conn)
    out = groups.form_groups(conn, tick=0, seed="test")
    names = [g["name"] for g in out["founded"]]
    assert "the catholic congregation" in names
    row = conn.execute(
        "SELECT * FROM groups WHERE name='the catholic congregation'").fetchone()
    assert row["kind"] == "congregation"
    assert row["meets_day"] == 6                    # Sunday
    assert conn.execute(
        "SELECT COUNT(*) n FROM memberships WHERE group_id=?", (row["id"],)
    ).fetchone()["n"] == 9                          # eight adults and the child


def _keep_only(conn, n):
    conn.execute("DELETE FROM agent_state WHERE agent_id > ?", (n,))
    conn.execute("DELETE FROM agents WHERE id > ?", (n,))
    conn.commit()


def test_a_tradition_below_the_quorum_gets_no_congregation(conn):
    _keep_only(conn, 3)                                 # only three left
    groups.assign_faith(conn)
    out = groups.form_groups(conn, tick=0, seed="test")
    assert "the catholic congregation" not in [g["name"] for g in out["founded"]]


def test_a_club_needs_a_quorum(conn):
    _keep_only(conn, 4)                                 # four neighbours
    groups.assign_faith(conn)
    out = groups.form_groups(conn, tick=0, seed="test")
    assert not [g for g in out["founded"] if g["kind"] == "club"]


def test_a_crowd_of_readers_splits_into_several_clubs(conn):
    for i in range(20, 50):                             # thirty more readers
        conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,occupation,hobbies_json,
               household_id,home_place_id) VALUES(?,?,'Male',35,0,'clerk',?,
               NULL,(SELECT id FROM places WHERE name='H1'))""",
            (f"r{i}", f"Reader {i}", json.dumps(["reading"])))
        conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) "
                     "VALUES((SELECT id FROM agents WHERE uuid=?),1,100000)", (f"r{i}",))
    conn.commit()
    groups.assign_faith(conn)
    out = groups.form_groups(conn, tick=0, seed="test")
    clubs = [g for g in out["founded"] if g["kind"] == "club"]
    assert len(clubs) > 1                               # the crowd was split
    assert all(g["members"] <= groups.CLUB_MAX for g in clubs)


def test_a_member_is_planned_at_the_meeting(conn):
    groups.assign_faith(conn)
    groups.form_groups(conn, tick=0, seed="test")
    row = conn.execute(
        """SELECT g.venue_id, g.meets_day, g.meets_tick FROM groups g
           JOIN memberships m ON m.group_id=g.id WHERE m.agent_id=1
             AND g.kind='congregation'""").fetchone()
    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    ctx = schedules._plan_ctx(conn)
    ctx["meetings"] = groups.meetings_today(conn, row["meets_day"])
    plan = dict((t, (p, a)) for t, p, a in
                schedules.build_plan(conn, agent, row["meets_day"], "test", ctx))
    assert plan[row["meets_tick"]] == (row["venue_id"], "gathering")


def test_a_gathering_does_not_pull_anyone_off_their_shift(conn):
    groups.assign_faith(conn)
    groups.form_groups(conn, tick=0, seed="test")
    # a club meets on a weekday, so a shift can genuinely collide with it
    conn.execute("UPDATE groups SET meets_day=2 WHERE kind='club'")   # Wednesday
    conn.commit()
    row = conn.execute(
        """SELECT g.id, g.venue_id, g.meets_day, g.meets_tick FROM groups g
           JOIN memberships m ON m.group_id=g.id WHERE m.agent_id=1
             AND g.kind='club'""").fetchone()
    assert row["meets_day"] == 2
    venue = conn.execute("SELECT id FROM places WHERE name='Miniville Grocer'").fetchone()["id"]
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
           work_days,started_tick,rank) VALUES(1,?,'clerk',10000,?,?,62,0,0)""",
        (venue, row["meets_tick"] - 2, row["meets_tick"] + 4))
    conn.commit()
    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    ctx = schedules._plan_ctx(conn)
    ctx["meetings"] = groups.meetings_today(conn, row["meets_day"])
    plan = dict((t, (p, a)) for t, p, a in
                schedules.build_plan(conn, agent, row["meets_day"], "test", ctx))
    assert plan[row["meets_tick"]][1] == "work"


def test_group_standing_is_its_members_average(conn):
    groups.assign_faith(conn)
    groups.form_groups(conn, tick=0, seed="test")
    conn.execute("UPDATE agents SET standing=40 WHERE id IN (1,2)")
    conn.commit()
    groups.refresh_standing(conn)
    row = conn.execute(
        "SELECT standing FROM groups WHERE name='the catholic congregation'").fetchone()
    assert row["standing"] == 8          # (40+40)/9 members, floored
