"""Tests for Town Weather & Micro-Climate System (v0.50)."""
import sqlite3

import pytest

from miniville import db, engine, schedules, weather, world
from miniville.seasons import OUTDOOR_TAGS, season_of
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
           VALUES('u1','Maya Lin','Female',32,0,'never_married','clerk',
                  '["hiking","gardening","fishing"]',1,?,5)""",
        (home,),
    )
    c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents,stress,fun) VALUES(1,?,50000,20,60)", (home,))
    db.set_meta(c, "seed", "weather_test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_weather_is_deterministic_and_season_appropriate():
    winter_day = next(d for d in range(365) if season_of(d) == "winter")
    summer_day = next(d for d in range(365) if season_of(d) == "summer")
    w1 = weather.generate_day(winter_day, "seedA", None)
    w2 = weather.generate_day(winter_day, "seedA", None)
    assert w1 == w2
    assert w1["condition"] in ("clear", "overcast", "snow", "fog", "blizzard")
    assert w1["temp_f"] <= 36
    s1 = weather.generate_day(summer_day, "seedA", None)
    assert s1["condition"] in ("clear", "breezy", "rain", "storm", "heatwave")
    assert s1["temp_f"] >= 66


def test_severe_weather_emits_alert_and_drives_outdoor_leisure_indoors(conn):
    # Find a seed+day that rolls a storm/blizzard/heatwave (severity >= 2)
    storm_day = next(
        d for d in range(0, 365)
        if weather.generate_day(d, "weather_test")["severity"] >= 2
    )
    out = weather.due(conn, storm_day * TICKS_PER_DAY, "weather_test")
    assert out["severity"] >= 2
    assert out["outdoor_penalty"] >= 0.70
    assert conn.execute(
        "SELECT 1 FROM events WHERE data LIKE '%weather_alert%'"
    ).fetchone() is not None

    # Compare outdoor plan slots on that severe storm day vs a forced clear day
    schedules.rebuild_day_plans(conn, storm_day, "weather_test")
    storm_outdoor = conn.execute(
        "SELECT COUNT(*) n FROM plans p JOIN places pl ON pl.id=p.place_id "
        "WHERE p.activity='leisure' AND (pl.tags LIKE '%outdoors%' OR pl.tags LIKE '%water%')"
    ).fetchone()["n"]

    # Force same day to be clear and rebuild plans
    conn.execute("UPDATE weather_days SET condition='clear', severity=0 WHERE day=?", (storm_day,))
    conn.commit()
    schedules.rebuild_day_plans(conn, storm_day, "weather_test")
    clear_outdoor = conn.execute(
        "SELECT COUNT(*) n FROM plans p JOIN places pl ON pl.id=p.place_id "
        "WHERE p.activity='leisure' AND (pl.tags LIKE '%outdoors%' OR pl.tags LIKE '%water%')"
    ).fetchone()["n"]
    assert storm_outdoor <= clear_outdoor


def test_weather_api_and_cli_summary(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from miniville import cli
    from miniville.ui.server import create_app

    db_file = tmp_path / "w_ui.db"
    c = db.connect(db_file)
    world.create_world(c)
    weather.due(c, 0, "seed1")
    c.commit()
    c.close()

    client = TestClient(create_app(str(db_file)))
    r_w = client.get("/api/weather")
    assert r_w.status_code == 200
    body = r_w.json()
    assert body["today"]["day"] == 0
    assert len(body["recent"]) == 1

    r_st = client.get("/api/status")
    assert r_st.status_code == 200 and "weather" in r_st.json()
    assert cli.main(["--db", str(db_file), "weather"]) == 0
