"""Tests for Town Book Club, Serialized Reading Circles & Author Signings (v0.37)."""
from __future__ import annotations

import sqlite3
import pytest

from miniville import book_club, db, economy, individual, keepsakes, world


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
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ash household',?)", (pid,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a1','Nora Author','Female',35,'never_married','writer',
                  'Poet and novelist.','["reading","writing"]','["writing"]',1,?)""",
        (pid,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('a2','Leo Reader','Male',40,'never_married','accountant',
                  'Book lover.','["reading"]','["accounting"]',1,?)""",
        (pid,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,200000)", (pid,))
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(2,?,400000)", (pid,))
    keepsakes.create_keepsake(c, 1, 10, "test")
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_book_club_author_signing_conserves_money(conn):
    before_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    # Day 4 (4 * 48) triggers run_book_club (day % 5 == 4)
    res = book_club.run_book_club(conn, 4 * 48, "test")
    assert res is not None
    assert res["royalty_cents"] > 0
    after_total = conn.execute("SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert after_total == before_total
