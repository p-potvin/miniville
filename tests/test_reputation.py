"""Standing: what the town thinks of a resident, read off the ledger."""
import json
import sqlite3

import pytest

from miniville import db, encounters, reputation, world


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
    for i, name in enumerate(["Ada Ashbrook", "Ben Ashbrook"], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               hobbies_json,household_id,home_place_id) VALUES(?,?,?,30,0,'married_present',
               'clerk','[]',1,?)""", (f"t{i}", name, "Female" if i == 1 else "Male", home))
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                  (i, home, 500_000))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def ledger(conn, tag, a=None, b=None, day=1):
    conn.execute(
        """INSERT INTO events(tick,day,kind,place_id,a_id,b_id,importance,data)
           VALUES(?,?,?,NULL,?,?,1,?)""",
        (day * 48, day, "life_event", a, b, json.dumps({"tag": tag})))
    conn.commit()


def test_favors_raise_standing(conn):
    ledger(conn, "favor", a=1)
    reputation.accrue(conn, tick=2 * 48, seed="test")
    assert reputation.standing_of(conn, 1) == reputation.WEIGHTS["favor"]


def test_a_betrayal_sinks_the_cheater_and_lifts_the_wronged(conn):
    ledger(conn, "betrayal", a=1, b=2)
    reputation.accrue(conn, tick=2 * 48, seed="test")
    assert reputation.standing_of(conn, 1) < 0
    assert reputation.standing_of(conn, 2) > 0


def test_standing_fades_when_nothing_more_happens(conn):
    conn.execute("UPDATE agents SET standing=40 WHERE id=1")
    conn.commit()
    for day in range(2, 100):                 # three monthly decay steps
        reputation.accrue(conn, tick=day * 48, seed="test")
    assert 35 <= reputation.standing_of(conn, 1) < 40
    # a resident with nothing to their name is left alone
    conn.execute("UPDATE agents SET standing=5 WHERE id=2")
    conn.commit()
    for day in range(100, 160):
        reputation.accrue(conn, tick=day * 48, seed="test")
    assert reputation.standing_of(conn, 2) == 5


def test_standing_is_clamped(conn):
    for _ in range(40):
        ledger(conn, "betrayal", a=1)
    reputation.accrue(conn, tick=2 * 48, seed="test")
    assert reputation.standing_of(conn, 1) == reputation.STANDING_MIN


def test_obituary_records_regard_and_reach(conn):
    conn.execute("UPDATE agents SET standing=60 WHERE id=1")
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,50,10,0,'friend')""")
    conn.commit()
    deceased = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    reputation.obituary(conn, deceased, tick=48 * 400, seed="test")
    row = conn.execute(
        "SELECT data FROM events WHERE kind='town_event' AND data LIKE '%obituary%'"
    ).fetchone()
    data = json.loads(row["data"])
    assert data["standing"] == 60
    assert data["known"] == 1
    assert "well thought of" in data["text"]


class _FixedRoll:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def test_a_good_name_warms_an_encounter():
    """Same roll, same affinity: standing changes how kind the tone is."""
    roll = 0.9                      # lands on the table's last row
    low, _ = encounters._interaction_tone(_FixedRoll(roll), 0.0, standing=-100)
    mid, _ = encounters._interaction_tone(_FixedRoll(roll), 0.0, standing=0)
    high, _ = encounters._interaction_tone(_FixedRoll(roll), 0.0, standing=100)
    assert low == mid == high       # the tone itself is the same...
    _, d_low = encounters._interaction_tone(_FixedRoll(roll), 0.0, standing=-100)
    _, d_mid = encounters._interaction_tone(_FixedRoll(roll), 0.0, standing=0)
    _, d_high = encounters._interaction_tone(_FixedRoll(roll), 0.0, standing=100)
    assert d_low < d_mid < d_high   # ...but the warmth is not
