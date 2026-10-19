"""Tests for v0.44-v0.47 10-Foot Living TV & Interactive Ambient Director Mode."""
from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from miniville import db, economy, engine, individual, tv, world
from miniville.ui.server import create_app


def _make_world(tmp_path: Path):
    p = tmp_path / "tv.db"
    conn = db.connect(p)
    world.create_world(conn)
    economy.ensure_businesses(conn)
    conn.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',8)")
    conn.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H2','home','Lakeshore',8)")
    h1 = conn.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    h2 = conn.execute("SELECT id FROM places WHERE name='H2'").fetchone()["id"]
    conn.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)", (h1,))
    conn.execute("INSERT INTO households(name,home_place_id) VALUES('Briar household',?)", (h2,))
    diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
    park = conn.execute("SELECT id FROM places WHERE name='Lush Meadow Park'").fetchone()["id"]

    residents = [
        ("u1", "Ada Ashbrook", "Female", 32, "baker", '["baking","running"]', '["carpentry"]', 1, h1, diner),
        ("u2", "Ben Briar", "Male", 35, "cook", '["cooking","chess"]', '["cooking"]', 2, h2, diner),
        ("u3", "Clara Cove", "Female", 44, "teacher", '["reading","gardening"]', '["teaching"]', 1, h1, park),
        ("u4", "David Dale", "Male", 27, "musician", '["music","guitar"]', '["music"]', 2, h2, park),
    ]
    for idx, (uuid, name, sex, age, occ, hob, sk, hh, home, work) in enumerate(residents, start=1):
        conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               persona,hobbies_json,skills_json,household_id,home_place_id,work_place_id)
               VALUES(?,?,?,?,'never_married',?,?,?,?,?,?,?)""",
            (uuid, name, sex, age, occ, f"{name} loves {occ}.", hob, sk, hh, home, work),
        )
        conn.execute(
            "INSERT INTO agent_state(agent_id,place_id,activity,money_cents) VALUES(?,?,?,500000)",
            (idx, work if idx % 2 == 1 else home, "work" if idx % 2 == 1 else "home"),
        )
    db.set_meta(conn, "seed", "tv-test")
    db.set_meta(conn, "tick", "0")
    individual.ensure_profiles(conn, "tv-test")
    conn.commit()
    engine.run(conn, 18, "tv-test")
    return p, conn


def test_tv_director_snapshot_bounds_movers_and_builds_camera_targets(tmp_path):
    _, conn = _make_world(tmp_path)
    try:
        # somebody at the diner, so a venue is worth a scene (an empty one is not)
        diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
        conn.execute("UPDATE agent_state SET place_id=?, activity='work' WHERE agent_id=1", (diner,))
        snap = tv.tv_director_snapshot(conn, max_movers=3)
        assert snap["tick"] == 18
        assert snap["sky"]["phase"] in ("night", "dawn", "morning", "afternoon", "dusk")
        assert snap["vitality"]["population"] == 4
        assert len(snap["movers"]) <= 3
        for m in snap["movers"]:
            assert "from_x" in m and "to_x" in m
            assert m["to_label"]

        assert snap["spotlight_resident"] is not None
        assert snap["spotlight_venue"] is not None
        modes = [c["mode"] for c in snap["camera_targets"]]
        assert "resident" in modes and "venue" in modes and "overview" in modes
        assert len(snap["progression"]) >= 1
        # Even when everyone is asleep at midnight, TV mode provides calm night-watch movers
        conn.execute("UPDATE agent_state SET activity='sleep'")
        midnight_snap = tv.tv_director_snapshot(conn, max_movers=4)
        assert 1 <= len(midnight_snap["movers"]) <= 4
    finally:
        conn.close()


def test_tv_step_advances_simulation_and_updates_snapshot(tmp_path):
    _, conn = _make_world(tmp_path)
    try:
        before = int(db.get_meta(conn, "tick", "0"))
        snap = tv.tv_step(conn, ticks=2)
        after = int(db.get_meta(conn, "tick", "0"))
        assert after == before + 2
        assert snap["tick"] == after
    finally:
        conn.close()


def test_tv_and_all_v28_to_v47_fastapi_endpoints(tmp_path):
    db_path, conn = _make_world(tmp_path)
    conn.close()
    client = TestClient(create_app(str(db_path)))

    # 1. Verify index.html & tv.js & map.js serve TV Mode HUD and 60fps mover loop
    html = client.get("/").text
    assert 'id="tv-toggle-btn"' in html
    assert 'id="tv-hud"' in html
    assert 'src="/tv.js"' in html

    tv_js = client.get("/tv.js").text
    assert "class TVDirector" in tv_js
    assert "openResidentInspector" in tv_js
    # v0.48: breaking news, follow-cam and recap are wired into the HUD
    for el in ('id="tv-breaking"', 'id="tv-focus"', 'id="tv-recap"', 'id="tv-caption"',
               'id="tv-toasts"', 'id="tv-focus-select"'):
        assert el in html
    for fn in ("backToAuto", "followOnScreen", "_playBreaking", "showRecap"):
        assert fn in tv_js
    assert client.get("/api/tv?since=1&follow=1").json()["followed"]["id"] == 1

    map_js = client.get("/map.js").text
    assert "setDirectorState" in map_js
    assert "focusOn" in map_js

    # 2. Verify GET /api/tv and POST /api/tv/step
    r_tv = client.get("/api/tv?max_movers=3")
    assert r_tv.status_code == 200
    payload = r_tv.json()
    assert payload["vitality"]["active_movers"] <= 3
    assert payload["spotlight_resident"] is not None

    r_step = client.post("/api/tv/step?ticks=1")
    assert r_step.status_code == 200
    assert r_step.json()["tick"] == payload["tick"] + 1

    # 3. Verify all v0.28 - v0.43 FastAPI routes return 200 OK
    for ep in (
        "/api/pets", "/api/market", "/api/youth", "/api/history",
        "/api/radio", "/api/guilds", "/api/harvest", "/api/sports",
        "/api/brigade", "/api/bookclub", "/api/renovations", "/api/jubilee",
        "/api/theater", "/api/commons", "/api/plaques", "/api/sabbaticals",
    ):
        resp = client.get(ep)
        assert resp.status_code == 200, f"{ep} failed with {resp.status_code}"


# --- v0.48 story director -------------------------------------------------------

from miniville.events import MAJOR, NOTABLE, emit  # noqa: E402


def _ev(conn, eid):
    return conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()


def test_newsworthiness_ranks_a_wedding_over_relationship_drift(tmp_path):
    _, conn = _make_world(tmp_path)
    try:
        t = int(db.get_meta(conn, "tick", "0"))
        wedding = emit(conn, t, "life_event", a=1, b=2, importance=MAJOR,
                       text="got married", tag="marriage")
        drift = emit(conn, t, "relationship", a=1, b=3, importance=NOTABLE,
                     label="familiar", was="friend")
        romance = emit(conn, t, "relationship", a=1, b=4, importance=NOTABLE,
                       label="sweetheart", was="close_friend")
        w, d, r = (tv.newsworthiness(_ev(conn, i)) for i in (wedding, drift, romance))
        assert w >= tv.BREAKING_MIN
        assert d < tv.STORY_MIN                 # the noise stays off the air
        assert tv.STORY_MIN <= r < w
    finally:
        conn.close()


def test_story_scenes_and_breaking_respect_the_viewers_cursor(tmp_path):
    _, conn = _make_world(tmp_path)
    try:
        t = int(db.get_meta(conn, "tick", "0"))
        first = tv.tv_director_snapshot(conn)
        assert first["breaking"] == []          # no cursor: no backlog of cuts
        cursor = first["latest_event_id"]
        eid = emit(conn, t, "life_event", a=1, b=2, importance=MAJOR,
                   text="got married", tag="marriage")
        # the same couple's second line is one story, not two
        emit(conn, t, "life_event", a=1, b=2, importance=MAJOR,
             text="got married", tag="marriage")
        snap = tv.tv_director_snapshot(conn, since_event_id=cursor)
        assert [b["event_id"] for b in snap["breaking"]] == [eid + 1]
        assert snap["breaking"][0]["category"] == "Wedding"
        assert snap["stories"][0]["category"] == "Wedding"
        assert snap["camera_targets"][0]["mode"] == "story"
        assert sum(1 for s in snap["stories"] if s["category"] == "Wedding") == 1
        # once seen, it is not breaking again
        again = tv.tv_director_snapshot(conn, since_event_id=snap["latest_event_id"])
        assert again["breaking"] == []
        assert any(h["category"] == "Wedding" for h in again["hotspots"])
    finally:
        conn.close()


def test_follow_view_shows_where_they_are_going_and_their_day(tmp_path):
    _, conn = _make_world(tmp_path)
    try:
        t = int(db.get_meta(conn, "tick", "0"))
        emit(conn, t, "life_event", a=1, importance=MAJOR, text="was promoted", tag="promoted")
        snap = tv.tv_director_snapshot(conn, follow_agent_id=1, follow_place_id=None)
        f = snap["followed"]
        assert f["id"] == 1 and f["name"] == "Ada Ashbrook"
        assert "x" in f and "y" in f
        assert all(set(u) == {"time", "activity", "place"} for u in f["upcoming"])
        assert any("promoted" in line["text"] for line in f["today"])
        diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
        v = tv.tv_director_snapshot(conn, follow_place_id=diner)["followed_venue"]
        assert v["name"] == "Riverside Diner" and v["inside"] >= 0
    finally:
        conn.close()


def test_day_recap_lists_yesterdays_top_stories(tmp_path):
    _, conn = _make_world(tmp_path)
    try:
        conn.execute("UPDATE meta SET value='60' WHERE key='tick'")     # day 1
        emit(conn, 20, "life_event", a=3, importance=MAJOR, text="died at 88", tag="death")
        rc = tv.tv_director_snapshot(conn)["recap"]
        assert rc["stories"][0]["category"] == "Obituary"
        assert rc["date"].startswith("Jan 1")
    finally:
        conn.close()


def test_movers_read_plans_by_tick_of_day(tmp_path):
    """plans are keyed by tick-of-day: a resident who just changed venue is a
    mover even on day two, when absolute ticks are past 48."""
    _, conn = _make_world(tmp_path)
    try:
        engine.run(conn, 48, "tv-test")                  # into day two
        tick = int(db.get_meta(conn, "tick", "0"))
        tod = tick % 48
        diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
        home = conn.execute("SELECT home_place_id FROM agents WHERE id=1").fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO plans(agent_id,tick,place_id,activity) VALUES(1,?,?,'home')",
                     ((tod - 1) % 48, home))
        conn.execute("UPDATE agent_state SET place_id=?, activity='work' WHERE agent_id=1", (diner,))
        movers = tv.tv_director_snapshot(conn, max_movers=10)["movers"]
        m = next(m for m in movers if m["id"] == 1)
        assert m["to_label"] == "Riverside Diner"
    finally:
        conn.close()
