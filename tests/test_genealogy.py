"""Tests for Genealogy, Family Trees & Town Dynasties (v0.52)."""
import sqlite3

import pytest

from miniville import db, genealogy, growth, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        "INSERT INTO households(name,home_place_id,tradition) VALUES('Vance household',?,'Sunday potluck')",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
           hobbies_json,household_id,home_place_id,standing)
           VALUES('p1','Elena Vance','Female',32,0,'married_present','doctor','[]',1,?,15),
                 ('p2','Marcus Vance','Male',33,0,'married_present','baker','[]',1,?,12),
                 ('c0','Nora Vance','Female',8,1,'never_married','child','[]',1,?,0)""",
        (home, home, home),
    )
    for i in (1, 2, 3):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,250000)", (i, home))
    c.execute(
        "INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label) "
        "VALUES(1,2,90,90,95,'spouse')"
    )
    db.set_meta(c, "seed", "gen_test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_backfill_idempotency_birth_parent_links_and_family_tree(conn, monkeypatch):
    # Backfill assigns p1 & p2 to pre-existing child Nora Vance (id 3)
    updated = genealogy.backfill_lineage(conn)
    assert updated == 1
    assert genealogy.backfill_lineage(conn) == 0  # guarded by lineage_v1 meta

    # Simulate an in-sim birth and verify parent_a_id/parent_b_id are persisted
    monkeypatch.setattr(growth, "P_BIRTH", 1.0)
    born = growth.births(conn, tick=48, seed="gen_test")
    assert born == 1

    baby = conn.execute("SELECT * FROM agents WHERE id > 3").fetchone()
    assert baby["parent_a_id"] == 1 and baby["parent_b_id"] == 2

    tree = genealogy.family_tree(conn, baby["id"])
    assert tree["generation"] == 2
    assert len(tree["parents"]) == 2
    assert [s["name"] for s in tree["siblings"]] == ["Nora Vance"]
    assert tree["household_tradition"] == "Sunday potluck"

    dyns = genealogy.top_dynasties(conn, limit=5)
    assert dyns and dyns[0]["surname"] == "Vance"
    assert dyns[0]["living"] == 4 and dyns[0]["generations"] == 2


def test_genealogy_and_dynasties_api_and_cli(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import cli
    from miniville.ui.server import create_app

    db_file = tmp_path / "gen_ui.db"
    c = db.connect(db_file)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Vance household',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
           hobbies_json,household_id,home_place_id,standing)
           VALUES('p1','Elena Vance','Female',32,0,'married_present','doctor','[]',1,?,15),
                 ('c0','Nora Vance','Female',8,1,'never_married','child','[]',1,?,0)""",
        (home, home),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,100000)", (i, home))
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    r_tree = client.get("/api/genealogy/2")
    assert r_tree.status_code == 200
    assert r_tree.json()["parents"][0]["name"] == "Elena Vance"

    r_dyn = client.get("/api/dynasties")
    assert r_dyn.status_code == 200 and r_dyn.json()["dynasties"][0]["surname"] == "Vance"

    r_res = client.get("/api/resident/2")
    assert r_res.status_code == 200 and "family" in r_res.json()

    assert cli.main(["--db", str(db_file), "tree", "Nora"]) == 0
    assert cli.main(["--db", str(db_file), "dynasties"]) == 0
