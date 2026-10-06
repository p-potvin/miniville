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


def test_newspaper_endpoint(client, tmp_path, monkeypatch):
    from miniville import newspaper
    from miniville.events import emit
    conn = db.connect(tmp_path / "ui.db")
    conn.execute("""INSERT INTO newspaper_profile(id,publisher_id,editor_id,founded_tick,
                    editorial_line,editorial_basis,credibility)
                    VALUES(1,1,1,0,'working','workers first',1.0)""")
    emit(conn, 48, "town_event", a=1, importance=3,
         text="the council rejected a motion to raise levy_rate", tag="motion_rejected",
         policy="levy_rate", direction=1, passed=False, value=0.06)
    conn.commit()
    monkeypatch.setattr(newspaper, "FALSE_CLAIM_BASE", 1.0)
    newspaper.publish_week(conn, 0, "t")
    conn.close()
    body = client.get("/api/newspaper").json()
    assert "editions" in body and "latest" in body
    latest = body["latest"]
    assert latest["publisher"] and latest["editor"]
    assert latest["editorial_line"] == "working"
    assert latest["observer_record"]
    assert "event ledger" in latest["source_note"]
    assert latest["claims"] and latest["claims"][0]["truth"] is False
    assert "rejected" in latest["claims"][0]["observer_record"]
    detail = client.get("/api/newspaper?week=1").json()
    assert detail["week"] == 1 and detail["observer_record"]
    assert client.get("/api/newspaper?week=99").status_code == 404


def test_shocks_endpoint(client, tmp_path):
    conn = db.connect(tmp_path / "ui.db")
    from miniville import shocks
    shocks.inject(conn, "festival", "Miniville Community Center", "t")
    conn.close()
    rows = client.get("/api/shocks").json()
    assert rows and rows[0]["kind"] == "festival"
    assert rows[0]["venue"] == "Miniville Community Center"


def test_map_endpoint(client):
    body = client.get("/api/map").json()
    assert body["districts"], "district tiles"
    assert {d["name"] for d in body["districts"]} >= {
        "Downtown", "Greenhill", "Lakeshore", "Old Mill Quarter", "The Flats"}
    assert body["places"], "non-home places with coords"
    for p in body["places"]:
        assert p["kind"] != "home"
        assert 0 <= p["x"] <= 1600 and 0 <= p["y"] <= 900
    assert len(body["agents"]) == 2
    assert {a["id"] for a in body["agents"]} == {1, 2}
    # positions are deterministic for a given venue set
    again = client.get("/api/map").json()
    assert [ (p["x"], p["y"]) for p in body["places"]] == \
           [ (p["x"], p["y"]) for p in again["places"]]


def test_graph_endpoint(client):
    rows = client.get("/api/residents").json()
    rid = rows[0]["id"]
    body = client.get(f"/api/graph/{rid}").json()
    assert body["ego"]["id"] == rid
    rings = {n["ring"] for n in body["nodes"]}
    assert 0 in rings
    assert body["nodes"][0]["id"] == rid
    for e in body["edges"]:
        ids = {n["id"] for n in body["nodes"]}
        assert e["a_id"] in ids and e["b_id"] in ids
    assert client.get("/api/graph/99999").json() == {"error": "no such agent"}


def test_economy_endpoint(client):
    body = client.get("/api/economy").json()
    assert "money_supply_cents" in body["stats"]
    assert body["businesses"], "the town should have businesses"
    assert any(b["name"] == "Town Hall" for b in body["businesses"])
    # the fixture runs exactly one day, so no day has closed its books yet and
    # the in-progress day must not be reported as the last completed one
    assert body["stats"]["last_day"] is None
    assert body["series"] == []
