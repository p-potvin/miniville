"""Public opinion, readership, and the paper's power over the council.

The Gazette could always lie; these tests are about the *consequence*: readers
give it reach, reach moves opinion, opinion moves councillors — and a caught
lie costs the paper its reach.
"""
import sqlite3

import pytest

from miniville import db, economy, politics, press, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    # 40 adults in one district: enough voters for an unemployment rate to mean
    # something, few enough that a motion is cheap to set up
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',60)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    for aid in range(1, 41):
        c.execute("INSERT INTO households(name,home_place_id) VALUES(?,?)",
                  (f"household {aid}", home))
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               hobbies_json,household_id,home_place_id,standing)
               VALUES(?,?,'Female',40,0,'married_present','clerk','[]',?,?,?)""",
            (f"t{aid}", f"Resident {aid}", aid, home, 12 + aid))
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
            (aid, home, 500_000))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def _profile(conn, line="business", credibility=1.0):
    conn.execute(
        """INSERT INTO newspaper_profile(id,publisher_id,editor_id,founded_tick,
               editorial_line,editorial_basis,credibility)
           VALUES(1,1,2,0,?,?,?)
           ON CONFLICT(id) DO UPDATE SET editorial_line=excluded.editorial_line,
               credibility=excluded.credibility""",
        (line, "test", credibility))
    conn.commit()


def _seat(conn, seat, wallet, unemployed, district="Downtown"):
    conn.execute(
        """INSERT INTO council(seat,agent_id,elected_tick,district,backers,
               backers_wallet,backers_unemployed) VALUES(?,?,0,?,10,?,?)""",
        (seat, seat, district, wallet, unemployed))
    conn.commit()


def _give_jobs(conn, n):
    place = conn.execute(
        "SELECT id FROM places WHERE name='Miniville Grocer'").fetchone()["id"]
    for i in range(1, n + 1):
        conn.execute(
            """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
               work_days) VALUES(?,?,'clerk',10000,16,30,62)""", (i, place))
    conn.commit()


# --- readership ----------------------------------------------------------------


def test_readers_scale_with_credibility(conn):
    _profile(conn, "business", 1.0)
    full = press.readers(conn)
    assert full == round(press.eligible_adults(conn) * press.PENETRATION)
    _profile(conn, "business", 0.5)
    assert press.readers(conn) == round(full / 2)
    _profile(conn, "business", 0.0)
    assert press.readers(conn) == 0
    assert press.reach(conn) == 0.0


def test_a_caught_lie_shrinks_the_papers_reach(conn):
    """The consequence the paper always lacked: lying costs it its audience."""
    _profile(conn, "business", 1.0)
    before = press.readers(conn)
    conn.execute("UPDATE newspaper_profile SET credibility=MAX(0, credibility-0.3) WHERE id=1")
    conn.commit()
    assert press.readers(conn) < before


# --- opinion -------------------------------------------------------------------


def test_opinion_tracks_the_towns_own_conditions(conn):
    # a town with almost nobody working wants the wage floor up
    _give_jobs(conn, 4)                       # 4 of 40 employed: 90% unemployment
    target = press.material_target(conn)
    assert target["min_wage"] > 0.5
    assert target["dividend_share"] > 0.5
    # an empty purse wants the levy up
    assert target["levy_rate"] > 0.5


def test_the_papers_line_pushes_opinion_its_way(conn):
    """Same town, same conditions; the only difference is who owns the paper."""
    _give_jobs(conn, 20)
    _profile(conn, "business", 1.0)
    for week in range(30):
        press.update(conn, tick=week * 7 * 48)
    business_levy = press.opinion(conn, "levy_rate")

    # reset and run the same weeks with a working paper
    conn.execute("DELETE FROM opinion")
    conn.commit()
    _profile(conn, "working", 1.0)
    for week in range(30):
        press.update(conn, tick=week * 7 * 48)
    working_levy = press.opinion(conn, "levy_rate")

    # the working paper wants the levy up, the business paper wants it down
    assert working_levy > business_levy


def test_a_paper_nobody_reads_moves_nothing(conn):
    _give_jobs(conn, 20)
    _profile(conn, "working", 0.0)            # discredited: zero reach
    assert set(press.press_push(conn, "working").values()) == {0.0}
    assert press.reach(conn) == 0.0


# --- opinion in the council ----------------------------------------------------


def test_a_paper_with_reach_can_swing_a_motion(conn):
    """Two dependent seats and three comfortable ones: a dividend rise loses.

    Put the town strongly behind it and the comfortable seats answer to the
    town, not to their own districts — which is the whole point of a paper.
    """
    _give_jobs(conn, 20)                      # unemployment 0.5
    for seat in (1, 2):                       # lean on the town: want it raised
        _seat(conn, seat, 5_000, 0.60)
    for seat in (3, 4, 5):                    # pay their own way: want it held
        _seat(conn, seat, 90_000, 0.01)

    from miniville.rng import rng_for
    day = next(d for d in range(1, 400)
               if (lambda r: (r.choice(sorted(politics.POLICIES)), r.choice([-1, 1])))(
                   rng_for("test", "motion", d)) == ("dividend_share", 1))

    # with the town indifferent the motion fails: the districts decide
    cold = politics.consider_motion(conn, tick=day * 48, seed="test")
    assert cold and cold["policy"] == "dividend_share" and not cold["passed"]
    assert cold["press_swing"] is False

    # the same motion on the same day, with the town persuaded
    conn.execute("DELETE FROM motions")
    conn.execute("DELETE FROM events")
    conn.commit()
    press._set(conn, "dividend_share", 0.9)
    conn.commit()
    hot = politics.consider_motion(conn, tick=day * 48, seed="test")
    assert hot and hot["passed"], "a persuaded town should carry it"
    assert hot["press_swing"] is True
    assert conn.execute(
        "SELECT 1 FROM events WHERE data LIKE '%press_influence%'").fetchone()


def test_opinion_swings_a_close_vote_but_not_a_landslide(conn):
    """A councillor's district still matters: opinion moves a member whose
    district is near the line, and leaves a firm one alone."""
    comfortable = {"backers_wallet": 90_000, "backers_unemployed": 0.01}
    # direction +1, support +0.9 -> -1 + 1.5*0.9 = +0.35: the district is overruled
    assert politics._votes_yes("levy_rate", +1, comfortable, 20_000, 0.5, 0.9)
    # a weaker opinion does not move them
    assert not politics._votes_yes("levy_rate", +1, comfortable, 20_000, 0.5, 0.3)
    # and the dependent district is unmoved by opinion pointing the other way
    dependent = {"backers_wallet": 5_000, "backers_unemployed": 0.60}
    assert politics._votes_yes("levy_rate", +1, dependent, 20_000, 0.5, 0.0)
    assert not politics._votes_yes("levy_rate", +1, dependent, 20_000, 0.5, -0.9)
