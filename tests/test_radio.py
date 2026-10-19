"""Tests for WMNV 94.3 FM Town Radio and Oral History Broadcasts (v0.32)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import db, economy, individual, radio, world


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    pid = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id,tradition) VALUES('Ash household',?,'Sunday potluck dinners')", (pid,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Clara Host','Female',36,'never_married','reporter',14,
                  'Gregarious radio host.','["music","singing"]','["broadcasting"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,standing,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Walter Elder','Male',68,'married_present','carpenter',18,
                  'Seasoned woodworker.','["woodworking","guitar"]','["carpentry","music"]',1,?)""",
        (pid,),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,300000)", (i, pid))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_air_broadcast_records_show_and_summary(conn):
    # Day 1 (tick 48) is an odd day -> airs a broadcast
    res = radio.air_broadcast(conn, 48, "test")
    assert res is not None
    assert res["show_kind"] in ("oral_history", "live_music", "main_street_spotlight")
    summary = radio.radio_summary(conn)
    assert summary["total_broadcasts"] == 1
    assert len(summary["recent"]) == 1
