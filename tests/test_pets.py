"""Tests for household pet companions, dog-walking encounters, and reunions (v0.28)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, pets, world


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    for hid in range(1, 5):
        c.execute(
            "INSERT INTO places(name,kind,district,capacity) VALUES(?, 'home', 'Downtown', 6)",
            (f"H{hid}",),
        )
        pid = c.execute("SELECT id FROM places WHERE name=?", (f"H{hid}",)).fetchone()["id"]
        c.execute(
            "INSERT INTO households(name,home_place_id) VALUES(?,?)",
            (f"Fam{hid} household", pid),
        )
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               persona,hobbies_json,skills_json,household_id,home_place_id)
               VALUES(?,?,'Female',34,'never_married','baker','Warm resident.','["walking"]','["baking"]',?,?)""",
            (f"u{hid}", f"Res {hid}", hid, pid),
        )
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,money_cents,social,fun,stress) VALUES(?,?,300000,50,50,40)",
            (hid, pid),
        )
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_ensure_pets_and_daily_comfort(conn):
    adopted = pets.ensure_pets(conn, 0, "test")
    assert adopted >= 1
    p_info = pets.pet_for_resident(conn, 1)
    assert p_info is not None
    res = pets.due(conn, 48, "test")
    assert res["comforted"] >= 1
    summary = pets.pet_summary(conn)
    assert summary["total_pets"] >= 1


def test_pet_walks_and_reunions(conn):
    pets.ensure_pets(conn, 0, "test")
    walks = reunions = 0
    for d in range(1, 30):
        res = pets.due(conn, d * 48, "test")
        walks += res["walks"]
        reunions += res["reunions"]
    assert walks + reunions >= 1
