"""Tests for Peer-Backed Credit Union & Loans (v0.51)."""
import sqlite3

import pytest

from miniville import banking, db, economy, world
from miniville.timekeeper import TICKS_PER_DAY


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('House 1',?)", (home,))
    c.execute("INSERT INTO households(name,home_place_id) VALUES('House 2',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
           hobbies_json,household_id,home_place_id,standing)
           VALUES('l1','Clara Rich','Female',45,0,'never_married','manager','[]',1,?,10),
                 ('b1','Sam Struggler','Male',28,0,'never_married','baker','[]',2,?,2)""",
        (home, home),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,800000)", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(2,?,10000)", (home,))
    diner = c.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    c.execute(
        "INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days) "
        "VALUES(2,?,'baker',12000,16,30,62)", (diner,),
    )
    db.set_meta(c, "seed", "bank_test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def _total_money(conn):
    w = conn.execute("SELECT COALESCE(SUM(money_cents),0) FROM agent_state").fetchone()[0]
    t = conn.execute("SELECT COALESCE(SUM(balance_cents),0) FROM businesses").fetchone()[0]
    return w + t + economy.town_balance(conn)


def test_loan_origination_repayment_and_default_conserve_money(conn):
    start_total = _total_money(conn)

    # Monday day 7: originate personal loan from Clara (id 1) to Sam (id 2)
    out = banking.due(conn, 7 * TICKS_PER_DAY, "bank_test")
    assert out["issued"] == 1
    assert _total_money(conn) == start_total

    loans = banking.list_loans(conn, status="active")
    assert len(loans) == 1
    l = loans[0]
    assert l["lender_id"] == 1 and l["borrower_id"] == 2
    principal = l["principal_cents"]

    # Give Sam enough cash and step weekly Mondays until repaid
    conn.execute("UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id=2", (principal,))
    start_total += principal
    for wk in range(2, 14):
        banking.due(conn, wk * 7 * TICKS_PER_DAY, "bank_test")
        assert _total_money(conn) == start_total
        if not banking.list_loans(conn, status="active"):
            break
    repaid = banking.list_loans(conn, status="repaid")
    assert len(repaid) == 1 and repaid[0]["remaining_cents"] == 0
    assert conn.execute("SELECT 1 FROM events WHERE data LIKE '%loan_repaid%'").fetchone() is not None


def test_business_loan_capitalizes_negative_till_and_defaults_on_six_misses(conn):
    diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    conn.execute("UPDATE businesses SET owner_id=2, balance_cents=-100000 WHERE place_id=?", (diner,))
    conn.execute("UPDATE agent_state SET money_cents=0 WHERE agent_id=2")
    conn.commit()
    start_total = _total_money(conn)

    out = banking.due(conn, 7 * TICKS_PER_DAY, "bank_test")
    assert out["issued"] == 1
    assert _total_money(conn) == start_total
    biz_bal = conn.execute("SELECT balance_cents FROM businesses WHERE place_id=?", (diner,)).fetchone()["balance_cents"]
    assert biz_bal > -100000  # business till got capitalized

    # Drain borrower cash & business till below 0 to force 6 consecutive missed payments -> default
    conn.execute("UPDATE businesses SET balance_cents=-50000 WHERE place_id=?", (diner,))
    conn.execute("UPDATE agent_state SET money_cents=0 WHERE agent_id=2")
    for wk in range(2, 9):
        banking.due(conn, wk * 7 * TICKS_PER_DAY, "bank_test")
    assert len(banking.list_loans(conn, status="defaulted")) == 1
    assert conn.execute("SELECT 1 FROM events WHERE data LIKE '%loan_default%'").fetchone() is not None


def test_loans_api_and_cli(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import cli
    from miniville.ui.server import create_app

    db_file = tmp_path / "bank_ui.db"
    c = db.connect(db_file)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('House 1',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
           hobbies_json,household_id,home_place_id,standing)
           VALUES('l1','Clara Rich','Female',45,0,'never_married','manager','[]',1,?,10),
                 ('b1','Sam Poor','Male',28,0,'never_married','baker','[]',1,?,2)""",
        (home, home),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,800000)", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(2,?,5000)", (home,))
    c.execute(
        "INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,work_days) "
        "VALUES(2,?,'baker',12000,16,30,62)", (home,),
    )
    banking.due(c, 7 * TICKS_PER_DAY, "s")
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    r = client.get("/api/loans")
    assert r.status_code == 200
    assert len(r.json()["loans"]) == 1
    assert cli.main(["--db", str(db_file), "loans"]) == 0
