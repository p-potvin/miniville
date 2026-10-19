"""Tests for v0.26 civic projects, conserved philanthropic & municipal funding,
volunteer shifts, venue capacity upgrades, and champion_civic_project ambition."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import (
    ambitions,
    civic_projects,
    db,
    economy,
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
    neighborhood.ensure_districts(c)
    c.execute("UPDATE town_account SET balance_cents = 500000 WHERE id = 1")
    c.execute(
        "INSERT INTO places(name,kind,district,capacity) "
        "VALUES('H1','home','Lakeshore',6)"
    )
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        "INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id,standing)
           VALUES('cp1','Ada Patron','Female',46,'married_present','architect',
                  'Ada is an outgoing, generous civic leader in Lakeshore.',
                  '["volunteering"]','["carpentry"]',1,?,20)""",
        (home,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,800000)", (home,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    individual.ensure_profiles(c, "test")
    civic_projects.ensure_projects(c)
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_civic_project_strictly_conserves_money_and_upgrades_venue(conn):
    conn.execute(
        """INSERT OR REPLACE INTO ambitions(
               agent_id,kind,started_tick,baseline_value,target_value,progress,status)
           VALUES(1,'champion_civic_project',0,0,2,0.0,'active')"""
    )
    park_cap_before = conn.execute(
        "SELECT capacity FROM places WHERE name='Lush Meadow Park'"
    ).fetchone()["capacity"]

    def total_money():
        w = conn.execute("SELECT COALESCE(SUM(money_cents),0) FROM agent_state").fetchone()[0]
        b = conn.execute("SELECT COALESCE(SUM(balance_cents),0) FROM businesses").fetchone()[0]
        p = conn.execute("SELECT COALESCE(balance_cents,0) FROM town_account WHERE id=1").fetchone()[0]
        return w + b + p

    money_before = total_money()
    for d in range(1, 30):
        civic_projects.due(conn, d * 48, "test")
        assert total_money() == money_before

    proj = conn.execute(
        "SELECT * FROM civic_projects WHERE key='lakeshore_bandstand'"
    ).fetchone()
    assert proj["status"] == "completed"

    park_cap_after = conn.execute(
        "SELECT capacity FROM places WHERE name='Lush Meadow Park'"
    ).fetchone()["capacity"]
    assert park_cap_after == park_cap_before + 20

    done = ambitions.evaluate_ambitions(conn, 30 * 48, "test")
    assert done == 1
    assert ambitions.ambition_of(conn, 1)["status"] == "completed"
