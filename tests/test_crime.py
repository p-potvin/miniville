"""Petty crime: who steals, who is caught, and what it costs them."""
from __future__ import annotations

from miniville import crime, economy
from miniville import db as mvdb
from miniville.world import create_world

from miniville.timekeeper import TICKS_PER_DAY as DAY


def _world():
    conn = mvdb.connect(":memory:")
    create_world(conn)
    conn.execute("""INSERT INTO places(name,kind,district,capacity,open_tick,close_tick,tags)
                    VALUES('Home', 'home', 'Greenhill', 6, 0, 47, '[]')""")
    economy.ensure_businesses(conn)
    return conn


def _adult(conn, aid, money, job_at=None):
    home = conn.execute("SELECT id FROM places WHERE name='Home'").fetchone()["id"]
    conn.execute(
        """INSERT INTO agents(id,name,age,sex,marital_status,home_place_id,is_child)
           VALUES(?,?,30,'Male','never_married',?,0)""", (aid, f"Resident {aid}", home))
    conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                 (aid, home, money))
    if job_at:
        conn.execute(
            """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                   work_days) VALUES(?,?,'clerk',10000,16,34,62)""", (aid, job_at))


def _total(conn):
    w = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"] or 0
    b = conn.execute("SELECT SUM(balance_cents) s FROM businesses").fetchone()["s"] or 0
    return w + b + economy.town_balance(conn)


def _run(conn, monkeypatch, caught: bool, days=40):
    monkeypatch.setattr(crime, "P_OFFEND", 1.0)
    monkeypatch.setattr(crime, "CATCH_BASE", 1.0 if caught else 0.0)
    monkeypatch.setattr(crime, "CATCH_PER_OFFICER", 0.0)
    for d in range(1, days):
        out = crime.daily_crime(conn, d * DAY, "t")
        if out["thefts"]:
            return out
    raise AssertionError("no theft in the window")


def test_only_the_desperate_steal(monkeypatch):
    conn = _world()
    _adult(conn, 1, 10_000_000)                                   # comfortable
    _adult(conn, 2, 0, job_at=conn.execute(
        "SELECT id FROM places WHERE name='Town Hall'").fetchone()["id"])  # employed
    monkeypatch.setattr(crime, "P_OFFEND", 1.0)
    for d in range(1, 30):
        assert crime.daily_crime(conn, d * DAY, "t")["thefts"] == 0


def test_an_uncaught_theft_moves_money_and_mints_none(monkeypatch):
    conn = _world()
    _adult(conn, 1, 0)
    conn.execute("UPDATE businesses SET balance_cents=1000000")
    before = _total(conn)
    out = _run(conn, monkeypatch, caught=False)
    assert out["caught"] == 0
    assert _total(conn) == before
    thief = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                         ).fetchone()["money_cents"]
    assert thief > 0


def test_a_caught_thief_returns_it_pays_a_fine_and_sits_in_the_cells(monkeypatch):
    conn = _world()
    _adult(conn, 1, 5_000)                                        # a little to fine
    _adult(conn, 2, 1_000_000)
    conn.execute("""INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
                    VALUES(1,2,10,5,0,'acquaintance')""")
    conn.execute("UPDATE businesses SET balance_cents=1000000")
    before = _total(conn)
    out = _run(conn, monkeypatch, caught=True)
    assert out["caught"] == 1
    assert _total(conn) == before                                 # fine went to the purse
    assert economy.town_balance(conn) == 5_000
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 0
    assert conn.execute("SELECT 1 FROM conditions WHERE agent_id=1 AND kind='jailed'"
                        ).fetchone()
    crime.apply_jail(conn)
    hall = crime.town_hall_id(conn)
    st = conn.execute("SELECT place_id, activity FROM agent_state WHERE agent_id=1").fetchone()
    assert (st["place_id"], st["activity"]) == (hall, "jailed")
    ev = conn.execute("SELECT * FROM events WHERE data LIKE '%\"arrest\"%'").fetchone()
    assert ev is not None and ev["a_id"] == 1


def test_a_robbed_acquaintance_holds_a_grudge(monkeypatch):
    conn = _world()
    _adult(conn, 1, 0)
    _adult(conn, 2, 1_000_000)
    conn.execute("""INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
                    VALUES(1,2,10,0,0,'acquaintance')""")
    monkeypatch.setattr(crime, "P_SHOPLIFT", 0.0)                 # always a person
    _run(conn, monkeypatch, caught=True)
    rel = conn.execute("SELECT affinity, label FROM relationships WHERE a_id=1 AND b_id=2"
                       ).fetchone()
    assert rel["affinity"] == -crime.GRUDGE and rel["label"] == "rival"


def test_a_staffed_town_hall_catches_more():
    conn = _world()
    base = crime.catch_chance(conn)
    hall = crime.town_hall_id(conn)
    for i in range(5):
        _adult(conn, 10 + i, 0, job_at=hall)
    assert crime.catch_chance(conn) > base
