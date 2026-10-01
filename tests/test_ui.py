"""Observer UI API tests against a temp-file DB (TestClient needs real file)."""
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from miniville import db, world, engine  # noqa: E402
from miniville.ui.server import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    p = tmp_path / "ui.db"
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    for i, name in enumerate(["Ada Ashbrook", "Ben Briar"], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               hobbies_json,home_place_id) VALUES(?,?,?,?,?,?,?,?)""",
            (f"t{i}", name, "Female", 30, "never_married", "teacher", '[]', home))
        c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(?,?)", (i, home))
    db.set_meta(c, "seed", "t")
    db.set_meta(c, "tick", "0")
    c.commit(); c.close()
    c2 = db.connect(p)
    engine.run(c2, 48, "t")
    c2.close()
    with TestClient(create_app(str(p))) as tc:
        yield tc


def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["day"] >= 1 and body["population"] == 2


def test_feed_and_venues(client):
    assert client.get("/api/feed").status_code == 200
    v = client.get("/api/venues").json()
    assert any(x["kind"] == "home" for x in v)


def test_resident_roundtrip(client):
    rows = client.get("/api/residents?q=Ada").json()
    assert rows and rows[0]["name"] == "Ada Ashbrook"
    rid = rows[0]["id"]
    detail = client.get(f"/api/resident/{rid}")
    assert detail.status_code == 200
    assert "relationships" in detail.json()


def test_chronicle_404_then_ok(client):
    assert client.get("/api/chronicle/99").status_code == 404
    r = client.get("/api/chronicle/1")
    assert r.status_code == 200 and "Day 1" in r.json()["chronicle"]


def test_resident_detail_includes_memories(client):
    rid = client.get("/api/residents?q=Ada").json()[0]["id"]
    detail = client.get(f"/api/resident/{rid}").json()
    assert "memories" in detail
    mems = client.get(f"/api/memories/{rid}").json()
    assert isinstance(mems, list)


def test_newspaper_endpoint(client):
    body = client.get("/api/newspaper").json()
    assert "editions" in body and "latest" in body
    assert client.get("/api/newspaper?week=99").status_code == 404
