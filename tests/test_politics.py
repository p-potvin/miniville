"""The council: seats, motions, and policies the economy actually reads."""
import sqlite3

import pytest

from miniville import db, economy, politics, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    # one resident per district, so every seat has somebody to fill it
    for i, district in enumerate(world.DISTRICTS, start=1):
        c.execute("INSERT INTO places(name,kind,district,capacity) VALUES(?,?,?,6)",
                  (f"H{i}", "home", district))
        home = c.execute("SELECT id FROM places WHERE name=?", (f"H{i}",)).fetchone()["id"]
        c.execute("INSERT INTO households(name,home_place_id) VALUES(?,?)",
                  (f"household {i}", home))
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               hobbies_json,household_id,home_place_id,standing)
               VALUES(?,?,'Female',40,0,'married_present','clerk','[]',?,?,?)""",
            (f"t{i}", f"Resident {i}", i, home, 20 + i))
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                  (i, home, 500_000))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def _seat(conn, seat, wallet, unemployed, district="The Flats"):
    conn.execute(
        """INSERT INTO council(seat,agent_id,elected_tick,district,backers,
               backers_wallet,backers_unemployed) VALUES(?,?,0,?,10,?,?)""",
        (seat, seat, district, wallet, unemployed))
    conn.commit()


def test_policies_default_to_the_towns_own_constants(conn):
    assert politics.policy(conn, "levy_rate") == economy.BUSINESS_TAX_RATE
    assert politics.policy(conn, "dividend_share") == economy.LEVY_DIVIDEND_SHARE
    assert politics.policy(conn, "rent_multiplier") == 1.0


def test_a_dependent_district_votes_for_redistribution():
    """One axis: who leans on the town, and who pays for it."""
    dependent = {"backers_wallet": 5_000, "backers_unemployed": 0.5}
    independent = {"backers_wallet": 50_000, "backers_unemployed": 0.02}
    for policy in ("rent_multiplier", "dividend_share", "levy_rate"):
        assert politics._votes_yes(policy, +1, dependent, 20_000, 0.2)
        assert not politics._votes_yes(policy, +1, independent, 20_000, 0.2)
        assert politics._votes_yes(policy, -1, independent, 20_000, 0.2)
    # the wage floor is the other half of the axis: the poorer district wants it
    assert politics._votes_yes("min_wage", +1, dependent, 20_000, 0.2)
    assert not politics._votes_yes("min_wage", +1, independent, 20_000, 0.2)


def test_an_election_seats_one_councillor_per_district(conn):
    out = politics.hold_election(conn, tick=0, seed="test")
    assert out["seated"]
    districts = [r["district"] for r in conn.execute(
        "SELECT district FROM council ORDER BY seat")]
    assert len(districts) == len(set(districts))       # nobody represents two
    assert conn.execute("SELECT COUNT(*) n FROM elections").fetchone()["n"] == 1
    assert politics.next_election_day(conn) == politics.TERM_DAYS


def _give_jobs(conn, n):
    """Employ some residents so the town has an unemployment rate to compare to."""
    place = conn.execute("SELECT id FROM places WHERE name='Miniville Grocer'").fetchone()["id"]
    for i in range(1, n + 1):
        conn.execute(
            """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
               work_days) VALUES(?,?,'clerk',10000,16,30,62)""", (i, place))
    conn.commit()


def test_a_motion_that_carries_moves_the_policy(conn):
    # three districts leaning on the town, two paying their own way
    _give_jobs(conn, 4)                     # a town with work in it
    for seat in (1, 2, 3):
        _seat(conn, seat, 5_000, 0.60)
    for seat in (4, 5):
        _seat(conn, seat, 90_000, 0.01)
    conn.commit()
    before = politics.policy(conn, "dividend_share")
    out = None
    for day in range(1, 400):                          # sweep days for a motion
        out = politics.consider_motion(conn, tick=day * 48, seed="test")
        if out and out["policy"] == "dividend_share" and out["direction"] > 0:
            break
    assert out is not None and out["passed"], "the dependent majority should win"
    assert politics.policy(conn, "dividend_share") > before
    assert conn.execute("SELECT COUNT(*) n FROM motions").fetchone()["n"] >= 1


def test_a_motion_at_the_rail_is_not_proposed(conn):
    from miniville.rng import rng_for
    _seat(conn, 1, 5_000, 0.6)
    db.set_meta(conn, "policy_rent_multiplier", "1.40")   # already at the top
    assert politics.policy(conn, "rent_multiplier") == 1.40
    assert politics._clamp("rent_multiplier", 1.45) == 1.40
    # find a day whose draw is a rent rise, and check the council refuses it
    found = False
    for day in range(1, 400):
        r = rng_for("test", "motion", day)
        name = r.choice(sorted(politics.POLICIES))
        direction = r.choice([-1, 1])
        if name == "rent_multiplier" and direction > 0:
            assert politics.consider_motion(conn, tick=day * 48, seed="test") is None
            found = True
            break
    assert found, "no rent-rise motion was drawn in 400 days"


def test_rent_follows_the_councils_policy(conn):
    home = conn.execute("SELECT home_place_id FROM agents WHERE id=1").fetchone()[0]
    base = economy.rent_for(conn, home)
    db.set_meta(conn, "policy_rent_multiplier", "0.6")
    assert economy.rent_for(conn, home) == int(base * 0.6)


def test_the_levy_follows_the_councils_policy(conn):
    place = conn.execute("SELECT id FROM places WHERE name='Miniville Grocer'").fetchone()["id"]
    conn.execute("INSERT INTO businesses(place_id,balance_cents) VALUES(?,1000000)", (place,))
    conn.commit()
    default = economy.weekly_levy(conn, 7 * 48, "test")["levied"]
    conn.execute("UPDATE businesses SET balance_cents=1000000")
    db.set_meta(conn, "policy_levy_rate", "0.10")
    doubled = economy.weekly_levy(conn, 14 * 48, "test")["levied"]
    assert doubled == default * 2


def test_a_wage_floor_lifts_the_lowest_paid(conn):
    home = conn.execute("SELECT home_place_id FROM agents WHERE id=1").fetchone()[0]
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
           work_days) VALUES(1,?,'clerk',1000,16,30,62)""", (home,))
    conn.execute(
        """INSERT INTO plans(agent_id,tick,place_id,activity)
           VALUES(1,30,?,'work')""", (home,))
    conn.commit()
    assert economy.pay_wages(conn, 30) == 1000          # no floor: as paid
    conn.execute("UPDATE agent_state SET money_cents=0 WHERE agent_id=1")
    db.set_meta(conn, "policy_min_wage", "0.5")
    assert economy.pay_wages(conn, 30) == int(0.5 * economy.WAGE_MAX_CENTS)
