"""Tests for v0.54 Town Festivals & Fairgrounds."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import db, economy, festivals, world
from miniville.timekeeper import TICKS_PER_DAY


def _build_town(seed: str = "fest_seed") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    db.init_db(conn)
    world.create_world(conn)
    economy.ensure_businesses(conn)
    # Give commercial businesses starting balances so they can afford booth fees
    conn.execute("UPDATE businesses SET balance_cents = 50000")
    economy.town_credit(conn, 25000)

    home_id = conn.execute("SELECT id FROM places LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = conn.execute("SELECT id FROM households LIMIT 1").fetchone()["id"]

    residents = [
        ("u1", "Alice Gardener", "Female", 34, "florist", '["gardening", "baking"]', '["botany"]', 8, 40000),
        ("u2", "Bob Mariner", "Male", 41, "boatwright", '["boating", "fishing"]', '["woodworking"]', 6, 35000),
        ("u3", "Clara Baker", "Female", 29, "baker", '["baking", "cooking"]', '["pastry"]', 5, 30000),
        ("u4", "Dan Craftsman", "Male", 45, "carpenter", '["woodworking", "crafts"]', '["carpentry"]', 4, 25000),
        ("u5", "Eve Singer", "Female", 26, "musician", '["music", "singing"]', '["theater"]', 7, 20000),
        ("u6", "Frank Storyteller", "Male", 52, "historian", '["history", "storytelling"]', '["writing"]', 3, 15000),
    ]
    for idx, (uid, name, sex, age, occ, hobbies, skills, standing, wallet) in enumerate(residents, start=1):
        conn.execute(
            """INSERT INTO agents(
                   uuid, name, sex, age, is_child, marital_status, occupation,
                   hobbies_json, skills_json, household_id, home_place_id, standing)
               VALUES(?, ?, ?, ?, 0, 'never_married', ?, ?, ?, ?, ?, ?)""",
            (uid, name, sex, age, occ, hobbies, skills, hh_id, home_id, standing),
        )
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents, fun, stress) VALUES(?, ?, ?, 50, 30)",
            (idx, home_id, wallet),
        )

    db.set_meta(conn, "seed", seed)
    db.set_meta(conn, "tick", "0")
    conn.commit()
    return conn


def _total_money(conn: sqlite3.Connection) -> int:
    agents = conn.execute("SELECT COALESCE(SUM(money_cents), 0) s FROM agent_state").fetchone()["s"]
    biz = conn.execute("SELECT COALESCE(SUM(balance_cents), 0) s FROM businesses").fetchone()["s"]
    town = economy.town_balance(conn)
    return int(agents) + int(biz) + int(town)


@pytest.fixture()
def conn():
    c = _build_town()
    yield c
    c.close()


def _find_festival_days(year: int = 1) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    start = (year - 1) * 365
    for d in range(start, start + 365):
        sched = festivals.festival_schedule_for_day(d)
        if sched and sched["is_start"]:
            out[sched["season"]] = (sched["start_day"], sched["end_day"])
    return out


def test_schema_and_empty_queries(conn):
    # Before schema exists, query helpers fail gracefully
    assert festivals.active_festival(conn, 10) is None
    assert festivals.list_festivals(conn) == []

    festivals.ensure_festivals_schema(conn)
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(festivals)").fetchall()
    }
    expected = {
        "id", "season", "year", "name", "venue_id", "start_day", "end_day",
        "status", "attendance_count", "revenue_cents", "winner_agent_id",
        "winner_business_id", "weather_note", "summary",
    }
    assert expected.issubset(cols)


def test_all_four_seasonal_two_day_festivals_and_conservation(conn):
    fest_days = _find_festival_days(year=1)
    assert set(fest_days.keys()) == {"winter", "spring", "summer", "autumn"}

    expected_names = {
        "winter": "Winter Lantern Market",
        "spring": "Spring Blossom Fair",
        "summer": "Lakeshore Summer Regatta",
        "autumn": "Autumn Harvest Jamboree",
    }

    initial_total = _total_money(conn)

    for season in ("winter", "spring", "summer", "autumn"):
        s_day, e_day = fest_days[season]
        open_res = festivals.due(conn, s_day * TICKS_PER_DAY, "fest_seed")
        assert open_res["active"] is True
        assert open_res["action"] == "opened"
        assert open_res["name"] == expected_names[season]
        assert _total_money(conn) == initial_total

        active = festivals.active_festival(conn, s_day)
        assert active is not None
        assert active["name"] == expected_names[season]
        assert active["status"] == "active"

        close_res = festivals.due(conn, e_day * TICKS_PER_DAY, "fest_seed")
        assert close_res["active"] is False
        assert close_res["action"] == "closed"
        assert close_res["winner_agent_id"] is not None
        assert close_res["prize_cents"] > 0
        assert _total_money(conn) == initial_total

        assert festivals.active_festival(conn, e_day) is None

    listed = festivals.list_festivals(conn, limit=12)
    assert len(listed) == 4
    assert all(f["status"] == "closed" for f in listed)
    assert [f["name"] for f in listed] == [
        "Autumn Harvest Jamboree",
        "Lakeshore Summer Regatta",
        "Spring Blossom Fair",
        "Winter Lantern Market",
    ]


def test_weather_indoor_shelter_fallback(conn):
    fest_days = _find_festival_days(year=1)
    spring_start, spring_end = fest_days["spring"]
    summer_start, summer_end = fest_days["summer"]

    # Force clear weather for Spring -> outdoor venue (Lush Meadow Park)
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(?, 'clear', 68, 0)",
        (spring_start,),
    )
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(?, 'clear', 70, 0)",
        (spring_end,),
    )
    res_clear = festivals.due(conn, spring_start * TICKS_PER_DAY, "fest_seed")
    assert res_clear["indoor_shelter"] is False
    park_id = conn.execute("SELECT id FROM places WHERE name='Lush Meadow Park'").fetchone()["id"]
    assert res_clear["venue_id"] == park_id

    # Force storm for Summer -> indoor shelter fallback (The Bijou Theater)
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(?, 'storm', 74, 2)",
        (summer_start,),
    )
    res_storm = festivals.due(conn, summer_start * TICKS_PER_DAY, "fest_seed")
    assert res_storm["indoor_shelter"] is True
    bijou_id = conn.execute("SELECT id FROM places WHERE name='The Bijou Theater'").fetchone()["id"]
    assert res_storm["venue_id"] == bijou_id

    row = festivals.active_festival(conn, summer_start)
    assert row is not None
    assert "Moved indoors" in row["weather_note"]
    assert "Thunderstorm" in row["weather_note"]

    # Even if Day 2 is blizzard on Autumn, closing day moves indoors
    autumn_start, autumn_end = fest_days["autumn"]
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(?, 'clear', 60, 0)",
        (autumn_start,),
    )
    conn.execute(
        "INSERT INTO weather_days(day, condition, temp_f, severity) VALUES(?, 'blizzard', 18, 2)",
        (autumn_end,),
    )
    festivals.due(conn, autumn_start * TICKS_PER_DAY, "fest_seed")
    close_autumn = festivals.due(conn, autumn_end * TICKS_PER_DAY, "fest_seed")
    assert close_autumn["indoor_shelter"] is True
    cc_id = conn.execute("SELECT id FROM places WHERE name='Miniville Community Center'").fetchone()["id"]
    assert close_autumn["venue_id"] == cc_id


def test_determinism_winner_selection_and_events(conn):
    fest_days = _find_festival_days(year=1)
    s_day, e_day = fest_days["summer"]

    c2 = _build_town("fest_seed")
    try:
        r1_open = festivals.due(conn, s_day * TICKS_PER_DAY, "fest_seed")
        r1_close = festivals.due(conn, e_day * TICKS_PER_DAY, "fest_seed")

        r2_open = festivals.due(c2, s_day * TICKS_PER_DAY, "fest_seed")
        r2_close = festivals.due(c2, e_day * TICKS_PER_DAY, "fest_seed")

        assert r1_open == r2_open
        assert r1_close == r2_close

        ev_kinds = [
            r["kind"]
            for r in conn.execute("SELECT kind FROM events ORDER BY id ASC").fetchall()
        ]
        assert ev_kinds == ["festival_opened", "festival_winner", "festival_closed"]
    finally:
        c2.close()


def test_idempotency_and_zero_purse_conservation(conn):
    fest_days = _find_festival_days(year=1)
    s_day, e_day = fest_days["winter"]

    # Drain town_account and business balances to 0 to verify zero net mint/burn
    conn.execute("UPDATE town_account SET balance_cents = 0 WHERE id = 1")
    conn.execute("UPDATE businesses SET balance_cents = 0")
    before = _total_money(conn)

    open1 = festivals.due(conn, s_day * TICKS_PER_DAY, "fest_seed")
    open2 = festivals.due(conn, s_day * TICKS_PER_DAY + 5, "fest_seed")
    assert open1["action"] == "opened"
    assert open2["action"] == "already_opened"
    assert _total_money(conn) == before

    close1 = festivals.due(conn, e_day * TICKS_PER_DAY, "fest_seed")
    close2 = festivals.due(conn, e_day * TICKS_PER_DAY + 3, "fest_seed")
    assert close1["action"] == "closed"
    assert close2["action"] == "already_closed"
    assert _total_money(conn) == before

    # Exactly 1 festival row and 3 events
    assert conn.execute("SELECT COUNT(*) n FROM festivals").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM events").fetchone()["n"] == 3
