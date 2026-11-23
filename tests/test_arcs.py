"""Tests for Emergent Multi-Day Story Arcs (v0.53)."""
import sqlite3

import pytest

from miniville import arcs, db, world
from miniville.events import MAJOR, NOTABLE, emit
from miniville.timekeeper import TICKS_PER_DAY


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('House 1',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
           hobbies_json,household_id,home_place_id,standing)
           VALUES('a1','Alice Reed','Female',30,0,'never_married','clerk','[]',1,?,10),
                 ('a2','Bob Shaw','Male',32,0,'never_married','baker','[]',1,?,10)""",
        (home, home),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,100000)", (i, home))
    db.set_meta(c, "seed", "arc_test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_arc_creation_progression_resolution_and_stale_cleanup(conn):
    # Day 1: courtship starts between Alice & Bob
    emit(conn, 1 * TICKS_PER_DAY + 10, "relationship", a=1, b=2, importance=NOTABLE, label="sweetheart")
    out1 = arcs.due(conn, 2 * TICKS_PER_DAY, "arc_test")
    assert out1["opened"] == 1
    active = arcs.list_arcs(conn, status="active")
    assert len(active) == 1 and active[0]["kind"] == "romance"
    assert len(active[0]["chapters"]) == 1

    # Day 3: cohabitation extends the SAME romance arc
    emit(conn, 3 * TICKS_PER_DAY + 12, "life_event", a=1, b=2, importance=MAJOR, text="moved in together", tag="cohabitation")
    out2 = arcs.due(conn, 4 * TICKS_PER_DAY, "arc_test")
    assert out2["progressed"] == 1
    active = arcs.list_arcs(conn, status="active")
    assert len(active) == 1 and len(active[0]["chapters"]) == 2 and active[0]["heat"] >= 4

    # Day 5: marriage resolves the romance arc
    emit(conn, 5 * TICKS_PER_DAY + 20, "life_event", a=1, b=2, importance=MAJOR, text="got married!", tag="marriage")
    out3 = arcs.due(conn, 6 * TICKS_PER_DAY, "arc_test")
    assert out3["resolved"] == 1
    assert len(arcs.list_arcs(conn, status="active")) == 0
    resolved = arcs.list_arcs(conn, status="resolved")
    assert len(resolved) == 1 and len(resolved[0]["chapters"]) == 3

    # Open a feud arc on Day 7 and confirm stale retirement after 21 quiet days
    emit(conn, 7 * TICKS_PER_DAY + 5, "life_event", a=1, b=2, importance=MAJOR, text="spread slander", tag="slander")
    arcs.due(conn, 8 * TICKS_PER_DAY, "arc_test")
    assert len(arcs.list_arcs(conn, status="active")) == 1

    out_stale = arcs.due(conn, (7 + arcs.STALE_AFTER_DAYS + 2) * TICKS_PER_DAY, "arc_test")
    assert out_stale["resolved"] >= 1
    assert len(arcs.list_arcs(conn, status="active")) == 0


def test_arcs_cap_and_api_and_cli(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import cli
    from miniville.ui.server import create_app

    db_file = tmp_path / "arcs_ui.db"
    c = db.connect(db_file)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('House 1',?)", (home,))
    for idx in range(1, 14):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               hobbies_json,household_id,home_place_id,standing)
               VALUES(?,?,'Female',30,0,'never_married','clerk','[]',1,?,5)""",
            (f"u{idx}", f"Resident {idx}", home),
        )
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,100000)", (idx, home))
        emit(c, 5, "relationship", a=idx, importance=NOTABLE, label="rival")
    out = arcs.due(c, TICKS_PER_DAY, "s")
    assert len(arcs.list_arcs(c, status="active", limit=20)) <= arcs.MAX_ACTIVE_ARCS
    assert out["resolved"] >= 1
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    r = client.get("/api/arcs?status=all")
    assert r.status_code == 200 and len(r.json()["arcs"]) >= arcs.MAX_ACTIVE_ARCS
    assert cli.main(["--db", str(db_file), "arcs"]) == 0
