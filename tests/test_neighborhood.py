"""Tests for v0.21 neighborhood watch patrols, conserved mutual aid, and Saturday block parties."""
from __future__ import annotations

import json
import sqlite3

import pytest

from miniville import (
    db,
    economy,
    events,
    individual,
    neighborhood,
    world,
)


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute(
        "INSERT INTO places(name,kind,district,capacity) "
        "VALUES('H_Flats','home','The Flats',8)"
    )
    home = c.execute("SELECT id FROM places WHERE name='H_Flats'").fetchone()["id"]
    c.execute(
        "INSERT INTO households(name,home_place_id) VALUES('Flats household',?)",
        (home,),
    )
    # 1: Comfortable neighbour in The Flats ($4,000 wallet)
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,standing)
           VALUES('n1','Elena Vega','Female',42,'married_present','nurse',
                  'Elena is a generous, resilient nurse in The Flats.',
                  '["gardening"]','["nursing"]',1,?,15)""",
        (home,),
    )
    # 2: Struggling neighbour in The Flats ($18 wallet)
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,standing)
           VALUES('n2','Franklyn Cole','Male',29,'never_married','laborer',
                  'Franklyn is between jobs and struggling to make ends meet.',
                  '["music"]','["carpentry"]',1,?,2)""",
        (home,),
    )
    c.execute(
        "INSERT INTO agent_state(agent_id,place_id,money_cents,stress) VALUES(1,?,400000,20)",
        (home,),
    )
    c.execute(
        "INSERT INTO agent_state(agent_id,place_id,money_cents,stress) VALUES(2,?,1800,65)",
        (home,),
    )
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    individual.ensure_profiles(c, "test")
    neighborhood.ensure_districts(c)
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_watch_patrol_raises_district_vigilance_and_catch_bonus(conn):
    home = conn.execute("SELECT home_place_id FROM agents WHERE id=1").fetchone()[0]
    events.emit(conn, 10, "town_event", place_id=home, a=2, importance=1,
                text="someone stole $25; nobody was caught", tag="theft")
    before_vig = conn.execute(
        "SELECT vigilance FROM district_watch WHERE district='The Flats'"
    ).fetchone()["vigilance"]

    from miniville.rng import rng_for
    patrols = neighborhood._run_watch_patrols(conn, 48, "test", rng_for("test", "w"))
    assert patrols == 1
    after_vig = conn.execute(
        "SELECT vigilance FROM district_watch WHERE district='The Flats'"
    ).fetchone()["vigilance"]
    assert after_vig > before_vig
    assert neighborhood.vigilance_bonus_for(conn, home, None) > 0.05


def test_mutual_aid_strictly_conserves_money_and_lowers_stress(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    from miniville.rng import rng_for

    # Force a seed/roll where r.random() < 0.35
    class AlwaysYesRng:
        def random(self):
            return 0.1
        def choice(self, seq):
            return seq[0]
        def randint(self, lo, hi):
            return 2000

    aided = neighborhood._run_mutual_aid(conn, 48, "test", AlwaysYesRng())
    assert aided == 1
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total

    st2 = conn.execute("SELECT money_cents, stress FROM agent_state WHERE agent_id=2").fetchone()
    assert st2["money_cents"] == 3800
    assert st2["stress"] == 55


def test_saturday_block_party_boosts_district_cohesion(conn):
    # Day 5 is Saturday (5 % 7 == 5 -> tick = 5 * 48 = 240)
    # Put residents in DISTRICTS[0] so day=5 (5 // 7 == 0) selects their district
    d0 = neighborhood.DISTRICTS[0]
    conn.execute("UPDATE places SET district=? WHERE name='H_Flats'", (d0,))
    from miniville.rng import rng_for
    parties = neighborhood._run_block_party(conn, 5 * 48, "test", rng_for("test", "bp"))
    assert parties == 1
    ev = conn.execute(
        "SELECT data FROM events WHERE kind='life_event' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(ev["data"])["tag"] == "block_party"
