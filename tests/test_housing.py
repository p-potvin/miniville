"""Housing: households move up, down, or stay, by what they can afford."""
from __future__ import annotations

import random

from miniville import db as mvdb
from miniville import economy, housing
from miniville.world import create_world


def _world():
    conn = mvdb.connect(":memory:")
    create_world(conn)
    for d in economy.RENT_BY_DISTRICT:
        for i in range(2):
            conn.execute(
                """INSERT INTO places(name,kind,district,capacity,open_tick,close_tick,tags)
                   VALUES(?, 'home', ?, 6, 0, 47, '[]')""", (f"{d} Home {i}", d))
    economy.ensure_businesses(conn)
    return conn


def _home(conn, district, i=0):
    return conn.execute("SELECT id FROM places WHERE name=?",
                        (f"{district} Home {i}",)).fetchone()["id"]


def _household(conn, hid, district, money, wage=None, age=40):
    home = _home(conn, district)
    conn.execute("INSERT INTO households(id,name,home_place_id) VALUES(?,?,?)",
                 (hid, f"H{hid}", home))
    conn.execute(
        """INSERT INTO agents(id,name,age,sex,marital_status,home_place_id,household_id,
               is_child,occupation) VALUES(?,?,?,?,?,?,?,0,'clerk')""",
        (hid, f"Resident {hid}", age, "Male", "never_married", home, hid))
    conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                 (hid, home, money))
    if wage:
        conn.execute(
            """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                   work_days) VALUES(?,?,'clerk',?,16,34,62)""",
            (hid, _home(conn, district), wage))
    return conn.execute("SELECT * FROM agents WHERE id=?", (hid,)).fetchone()


class _Roll(random.Random):
    """An rng whose first draw is fixed."""
    def __init__(self, first):
        super().__init__(0)
        self._first = [first]

    def random(self):
        return self._first.pop() if self._first else super().random()


def _district(conn, aid):
    return conn.execute("""SELECT p.district FROM agents a JOIN places p
                           ON p.id=a.home_place_id WHERE a.id=?""", (aid,)).fetchone()[0]


def test_a_comfortable_household_moves_up():
    conn = _world()
    a = _household(conn, 1, "The Flats", money=5_000_000, wage=25_000)
    assert housing.consider_move(conn, a, 0, _Roll(0.1))
    assert _district(conn, 1) == "Downtown"


def test_a_squeezed_household_moves_one_step_down():
    conn = _world()
    a = _household(conn, 1, "Downtown", money=10_000, wage=10_000)   # $500/wk vs $460 rent
    assert housing.consider_move(conn, a, 0, _Roll(0.7))
    assert _district(conn, 1) == "Lakeshore"


def test_most_households_stay_put():
    conn = _world()
    a = _household(conn, 1, "Greenhill", money=300_000, wage=15_000)
    assert not housing.consider_move(conn, a, 0, _Roll(0.6))
    assert _district(conn, 1) == "Greenhill"


def test_a_pension_counts_as_income():
    conn = _world()
    _household(conn, 1, "The Flats", money=0, age=70)
    income, _savings = housing.household_means(conn, 1)
    assert income == economy.pension_week(conn)


def test_children_move_with_the_household():
    conn = _world()
    a = _household(conn, 1, "The Flats", money=5_000_000, wage=25_000)
    conn.execute(
        """INSERT INTO agents(id,name,age,sex,marital_status,home_place_id,household_id,
               is_child) VALUES(2,'Kid',8,'Female','never_married',?,1,1)""",
        (_home(conn, "The Flats"),))
    housing.consider_move(conn, a, 0, _Roll(0.1))
    assert _district(conn, 2) == "Downtown"


def test_district_profile_reports_every_district():
    conn = _world()
    _household(conn, 1, "Lakeshore", money=1000)
    prof = {p["district"]: p for p in housing.district_profile(conn)}
    assert set(prof) == set(economy.RENT_BY_DISTRICT)
    assert prof["Lakeshore"]["households"] == 1 and prof["Lakeshore"]["residents"] == 1
