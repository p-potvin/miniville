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
    assert {d["district"] for d in body["districts"]} >= {"Downtown", "The Flats"}
    assert "owner_name" in body["businesses"][0]


def test_council_endpoint(client, tmp_path):
    conn = db.connect(tmp_path / "ui.db")
    conn.execute(
        """INSERT INTO council(seat, agent_id, elected_tick, district, backers, backers_wallet, backers_unemployed)
           VALUES(1, 1, 48, 'Downtown', 50, 1000000, 0.10)"""
    )
    conn.execute(
        """INSERT INTO motions(tick, day, policy, direction, value, passed, votes_for, votes_against)
           VALUES(48, 1, 'levy_rate', 1, 0.06, 1, 3, 2)"""
    )
    c_group = conn.execute("INSERT INTO groups(name, kind, founded_tick) VALUES('Art Guild', 'club', 48)")
    gid = c_group.lastrowid
    p_id = conn.execute("SELECT id FROM places WHERE kind != 'home' LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO boycotts(group_id, place_id, started_day, until_day, reason) VALUES(?,?,1,30,'high prices')",
                 (gid, p_id))
    conn.commit()
    conn.close()

    body = client.get("/api/council").json()
    assert "seats" in body
    assert len(body["seats"]) == 1
    assert body["seats"][0]["district"] == "Downtown"
    assert "policies" in body
    assert "levy_rate" in body["policies"]
    assert "dividend_share" in body["policies"]
    assert "rent_multiplier" in body["policies"]
    assert "min_wage" in body["policies"]
    assert "next_election_day" in body
    assert "motions" in body
    assert len(body["motions"]) == 1
    assert body["motions"][0]["policy"] == "levy_rate"
    assert "boycotts" in body
    assert len(body["boycotts"]) == 1
    assert body["boycotts"][0]["group_name"] == "Art Guild"
    assert "influential" in body
    assert len(body["influential"]) > 0




def test_individual_and_ambitions_endpoints(client):
    ind = client.get("/api/individual").json()
    assert "chronotypes" in ind
    assert "top_archetypes" in ind
    assert "active_ambitions" in ind
    ambs = client.get("/api/ambitions").json()
    assert isinstance(ambs, list)
    rid = client.get("/api/residents?q=Ada").json()[0]["id"]
    detail = client.get(f"/api/resident/{rid}").json()
    assert "individual" in detail
    assert "archetype" in detail["individual"]
    assert "axes" in detail["individual"]


def test_mentorships_and_network_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import db, economy, individual, mentorship, world
    from miniville.ui.server import create_app

    db_file = tmp_path / "ui_ment.db"
    c = db.connect(db_file)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Miller household',?)", (home,))
    diner = c.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,hobbies_json,skills_json,household_id,home_place_id,work_place_id,standing)
           VALUES('u1','Martha','Female',48,'married_present','cook','["baking"]','["woodworking"]',1,?,?,12),
                 ('u2','Leo','Male',21,'never_married','cook','["running"]','[]',1,?,?,0)""",
        (home, diner, home, diner),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,500000)", (i, home))
    individual.ensure_profiles(c, "test")
    mentorship.ensure_traditions(c, "test")
    mentorship.pair_mentors(c, 0, "test")
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    r_ment = client.get("/api/mentorships")
    assert r_ment.status_code == 200
    data_m = r_ment.json()
    assert len(data_m["mentorships"]) == 1
    assert len(data_m["traditions"]) == 1

    r_net = client.get("/api/network?filter_kind=mentorship")
    assert r_net.status_code == 200
    data_n = r_net.json()
    assert len(data_n["edges"]) == 1
    assert data_n["edges"][0]["label"] == "mentorship"


def test_keepsakes_and_neighborhoods_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import db, economy, individual, keepsakes, neighborhood, world
    from miniville.ui.server import create_app

    db_file = tmp_path / "ui_kn.db"
    c = db.connect(db_file)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('u1','Clara','Female',48,'married_present','carpenter','["woodworking"]','["carpentry"]',1,?)""",
        (home,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,500000)", (home,))
    individual.ensure_profiles(c, "test")
    neighborhood.ensure_districts(c)
    keepsakes.craft_keepsake(c, 1, 10, "test")
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    rk = client.get("/api/keepsakes")
    assert rk.status_code == 200
    assert len(rk.json()) == 1

    rn = client.get("/api/neighborhoods")
    assert rn.status_code == 200
    assert len(rn.json()) == 5


def test_milestones_and_culture_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import culture, db, economy, individual, keepsakes, milestones, world
    from miniville.ui.server import create_app

    db_file = tmp_path / "ui_mc.db"
    c = db.connect(db_file)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,birth_day,marital_status,occupation,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('u1','Elena','Female',40,1,'never_married','writer_or_author','["poetry"]','["writing"]',1,?)""",
        (home,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,500000)", (home,))
    individual.ensure_profiles(c, "test")
    keepsakes.craft_keepsake(c, 1, 10, "test")
    culture.curate_exhibits(c, 48, "test")
    milestones.due(c, 48, "test")
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    rc = client.get("/api/culture")
    assert rc.status_code == 200
    assert len(rc.json()) == 1

    rm = client.get("/api/milestones")
    assert rm.status_code == 200
    assert len(rm.json()) == 1


def test_diary_and_competitions_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import competitions, correspondence, db, economy, events, individual, world
    from miniville.ui.server import create_app

    db_file = tmp_path / "ui_dc.db"
    c = db.connect(db_file)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,origin_city,origin_state,marital_status,occupation,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('u1','Ada','Female',34,'Austin','Texas','never_married','baker','["baking"]','["baking"]',1,?),
                 ('u2','Ben','Male',36,'Dallas','Texas','never_married','cook','["cooking"]','["cooking"]',1,?)""",
        (home, home),
    )
    for i in (1, 2):
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,social,mood) VALUES(?,?,500000,30,'lonely')", (i, home))
    individual.ensure_profiles(c, "test")
    events.emit(c, 48, "life_event", place_id=home, a=1, importance=3, text="was promoted", tag="promoted")
    correspondence.write_milestone_diaries(c, 48, "test")
    competitions.hold_competition(c, 48, "test", category_override="baking")
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    rd = client.get("/api/diary")
    assert rd.status_code == 200
    assert len(rd.json()) >= 1

    rcp = client.get("/api/competitions")
    assert rcp.status_code == 200
    assert len(rcp.json()) == 1


def test_projects_and_health_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import civic_projects, db, economy, health, individual, world
    from miniville.ui.server import create_app

    db_file = tmp_path / "ui_ph.db"
    c = db.connect(db_file)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)", (home,))
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('u1','Clara','Female',62,'married_present','physician','["running"]','["medicine"]',1,?)""",
        (home,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,vitality) VALUES(1,?,600000,82)", (home,))
    individual.ensure_profiles(c, "test")
    civic_projects.ensure_projects(c)
    health.due(c, 48, "test")
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    rp = client.get("/api/projects")
    assert rp.status_code == 200
    assert len(rp.json()) == 6

    rh = client.get("/api/health")
    assert rh.status_code == 200
    assert rh.json()["avg_vitality"] >= 80.0
