"""Enterprise: owners, draws, buyers, founders, inheritance."""
from __future__ import annotations

import sqlite3

from miniville import db as mvdb
from miniville import economy, enterprise, mortality
from miniville.timekeeper import TICKS_PER_DAY
from miniville.world import create_world

DAY = TICKS_PER_DAY


def _world() -> sqlite3.Connection:
    conn = mvdb.connect(":memory:")
    create_world(conn)
    for i in range(3):
        conn.execute(
            """INSERT INTO places(name,kind,district,capacity,open_tick,close_tick,tags)
               VALUES(?, 'home', 'Greenhill', 6, 0, 47, '[]')""", (f"Home {i}",))
    economy.ensure_businesses(conn)
    mvdb.set_meta(conn, "seed", "t")
    return conn


def _place(conn, name):
    return conn.execute("SELECT id FROM places WHERE name=?", (name,)).fetchone()["id"]


def _home(conn):
    return _place(conn, "Home 0")


def _adult(conn, aid, money, age=40, occupation="clerk", household=None):
    conn.execute(
        """INSERT INTO agents(id,name,age,sex,marital_status,home_place_id,
               household_id,is_child,occupation,hobbies_json)
           VALUES(?,?,?,?,?,?,?,0,?,'[]')""",
        (aid, f"Pat Owner{aid}", age, "Female", "never_married", _home(conn),
         household, occupation))
    conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                 (aid, _home(conn), money))


def _job(conn, aid, place, rank=0, started=0, wage=10000):
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
               work_days,started_tick,rank,base_wage_cents)
           VALUES(?,?,'worker',?,16,34,62,?,?,?)""",
        (aid, place, wage, started, rank, wage))


def _total(conn):
    wallets = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    tills = conn.execute("SELECT SUM(balance_cents) s FROM businesses").fetchone()["s"]
    return (wallets or 0) + (tills or 0) + economy.town_balance(conn)


def test_the_senior_hand_becomes_proprietor_and_public_venues_stay_unowned():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    hall = _place(conn, "Town Hall")
    _adult(conn, 1, 0); _job(conn, 1, diner, rank=0, started=0)
    _adult(conn, 2, 0); _job(conn, 2, diner, rank=1, started=500)
    _adult(conn, 3, 0); _job(conn, 3, hall, rank=2)
    assert enterprise.assign_proprietors(conn) >= 1
    owner = lambda pid: conn.execute(
        "SELECT owner_id FROM businesses WHERE place_id=?", (pid,)).fetchone()["owner_id"]
    assert owner(diner) == 2
    assert owner(hall) is None


def test_owners_draw_profit_above_a_payroll_cushion():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    _adult(conn, 1, 0); _job(conn, 1, diner, wage=10000)
    conn.execute("UPDATE businesses SET owner_id=1, balance_cents=1000000 WHERE place_id=?",
                 (diner,))
    before = _total(conn)
    assert enterprise.owner_draws(conn, 3 * DAY)["drawn"] == 0     # not a levy day
    out = enterprise.owner_draws(conn, 7 * DAY)
    cushion = enterprise.CUSHION_WEEKS * 10000 * 5
    assert out["drawn"] == int((1000000 - cushion) * enterprise.DRAW_SHARE)
    assert _total(conn) == before                                   # moved, not minted
    # a business at its cushion pays nothing
    conn.execute("UPDATE businesses SET balance_cents=? WHERE place_id=?", (cushion, diner))
    assert enterprise.owner_draws(conn, 14 * DAY)["drawn"] == 0


def test_a_crowded_trade_and_a_saver_found_a_new_venue(monkeypatch):
    conn = _world()
    monkeypatch.setattr(enterprise, "FOUND_P_WEEKLY", 1.0)
    monkeypatch.setattr(enterprise, "ADULTS_PER_COMMERCIAL", 0.01)
    # every food venue is packed
    conn.execute("""UPDATE businesses SET ema_traffic=1000 WHERE place_id IN
                    (SELECT id FROM places WHERE tags LIKE '%food%')""")
    rich = enterprise.CAPITAL_CENTS + enterprise.CUSHION_AFTER_CENTS + 50000
    _adult(conn, 1, rich, occupation="cook")
    _adult(conn, 2, 100)                                   # cannot afford it
    _job(conn, 1, _place(conn, "Riverside Diner"))
    before = _total(conn)
    assert enterprise.founding_pass(conn, 2 * DAY, "t")["founded"] == 0   # wrong weekday
    out = enterprise.founding_pass(conn, 3 * DAY, "t")
    assert out["founded"] == 1
    b = conn.execute("SELECT * FROM businesses WHERE place_id=?",
                     (out["place_id"],)).fetchone()
    assert b["owner_id"] == 1 and b["founded_tick"] == 3 * DAY
    assert b["balance_cents"] == enterprise.CAPITAL_CENTS
    assert _total(conn) == before
    job = conn.execute("SELECT * FROM jobs WHERE agent_id=1").fetchone()
    assert job["place_id"] == out["place_id"] and job["rank"] == 2
    place = conn.execute("SELECT * FROM places WHERE id=?", (out["place_id"],)).fetchone()
    assert place["district"] == "Greenhill"
    assert "Owner1" in place["name"] or "Greenhill" in place["name"]
    # and it is a destination like any other venue
    from miniville.schedules import _plan_ctx
    assert out["place_id"] in {v["id"] for v in _plan_ctx(conn)["venues"]}


def test_nothing_is_founded_when_nobody_is_crowded(monkeypatch):
    conn = _world()
    monkeypatch.setattr(enterprise, "FOUND_P_WEEKLY", 1.0)
    monkeypatch.setattr(enterprise, "ADULTS_PER_COMMERCIAL", 0.01)
    _adult(conn, 1, 10 * enterprise.CAPITAL_CENTS)
    assert enterprise.founding_pass(conn, 3 * DAY, "t")["founded"] == 0


def test_a_small_town_cannot_carry_another_venue(monkeypatch):
    conn = _world()
    monkeypatch.setattr(enterprise, "FOUND_P_WEEKLY", 1.0)
    conn.execute("""UPDATE businesses SET ema_traffic=1000 WHERE place_id IN
                    (SELECT id FROM places WHERE tags LIKE '%food%')""")
    _adult(conn, 1, 10 * enterprise.CAPITAL_CENTS, occupation="cook")
    # one adult cannot support the town's existing shops, let alone a new one
    assert enterprise.founding_pass(conn, 3 * DAY, "t")["founded"] == 0


def test_a_failed_founding_stays_dark_until_someone_buys_it(monkeypatch):
    conn = _world()
    monkeypatch.setattr(enterprise, "FOUND_P_WEEKLY", 1.0)
    monkeypatch.setattr(enterprise, "ADULTS_PER_COMMERCIAL", 0.01)
    conn.execute("""UPDATE businesses SET ema_traffic=1000 WHERE place_id IN
                    (SELECT id FROM places WHERE tags LIKE '%food%')""")
    _adult(conn, 1, enterprise.CAPITAL_CENTS + enterprise.CUSHION_AFTER_CENTS, occupation="cook")
    pid = enterprise.founding_pass(conn, 3 * DAY, "t")["place_id"]
    _adult(conn, 2, 0); _job(conn, 2, pid)
    conn.execute("UPDATE businesses SET balance_cents=? WHERE place_id=?",
                 (economy.FAIL_THRESHOLD_CENTS - 1, pid))
    economy.settle_businesses(conn, 5 * DAY, "t")
    b = conn.execute("SELECT * FROM businesses WHERE place_id=?", (pid,)).fetchone()
    assert b["status"] == "closed" and b["owner_id"] is None
    # past the cooldown, nobody can afford it: it stays dark
    later = 5 * DAY + (economy.REOPEN_AFTER_DAYS + 1) * DAY
    economy.settle_businesses(conn, later, "t")
    assert conn.execute("SELECT status FROM businesses WHERE place_id=?",
                        (pid,)).fetchone()["status"] == "closed"
    # a buyer turns up and takes it on with their capital
    _adult(conn, 3, enterprise.BUY_CENTS + enterprise.CUSHION_AFTER_CENTS)
    before = _total(conn)
    economy.settle_businesses(conn, later + DAY, "t")
    b = conn.execute("SELECT * FROM businesses WHERE place_id=?", (pid,)).fetchone()
    assert b["status"] == "open" and b["owner_id"] == 3
    assert b["balance_cents"] == enterprise.BUY_CENTS
    assert _total(conn) == before


def test_an_original_venue_with_no_buyer_reopens_town_run():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    _adult(conn, 1, 0); _job(conn, 1, diner)
    conn.execute("UPDATE businesses SET owner_id=1, balance_cents=? WHERE place_id=?",
                 (economy.FAIL_THRESHOLD_CENTS - 1, diner))
    economy.settle_businesses(conn, 2 * DAY, "t")
    later = 2 * DAY + (economy.REOPEN_AFTER_DAYS + 1) * DAY
    economy.settle_businesses(conn, later, "t")
    b = conn.execute("SELECT * FROM businesses WHERE place_id=?", (diner,)).fetchone()
    assert b["status"] == "open" and b["owner_id"] is None and b["balance_cents"] == 0


def test_a_business_passes_to_the_widow():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    _adult(conn, 1, 0, age=80); _adult(conn, 2, 0, age=78)
    conn.execute("""INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
                    VALUES(1,2,90,80,90,'spouse')""")
    conn.execute("UPDATE businesses SET owner_id=1 WHERE place_id=?", (diner,))
    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    mortality._die(conn, agent, 10 * DAY, mortality.rng_for("t", 1))
    assert conn.execute("SELECT owner_id FROM businesses WHERE place_id=?",
                        (diner,)).fetchone()["owner_id"] == 2


def test_owning_a_business_is_influence():
    from miniville.conflict import influence_of
    conn = _world()
    _adult(conn, 1, 1000); _adult(conn, 2, 1000)
    base = influence_of(conn, 1)
    conn.execute("UPDATE businesses SET owner_id=1 WHERE place_id=?",
                 (_place(conn, "Riverside Diner"),))
    assert influence_of(conn, 1) > base
