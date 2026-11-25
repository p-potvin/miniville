"""Tests for Community Chest & Mutual Weather Relief (v0.70)."""
from __future__ import annotations

import sqlite3

from miniville import db, economy, relief, utilities, world


def _make_town_conn(seed: str = "relief_seed") -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    utilities.ensure_utilities_schema(c)
    relief.ensure_relief_schema(c)
    db.set_meta(c, "seed", seed)
    db.set_meta(c, "tick", "0")

    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('Home1','home','Lakeshore',12)")
    home_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('HH1',?)", (home_id,))
    hh_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 3 wealthy donors (>= 120_000) and 4 low-wallet recipients (< 25_000)
    for i in range(1, 8):
        is_donor = i <= 3
        money = 200_000 if is_donor else 8_000
        standing = 20 if i == 1 else 5
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               household_id,home_place_id,alive,standing)
               VALUES(?,?,?,?,0,'never_married','clerk',?,?,1,?)""",
            (f"r{i}", f"Citizen {i}", "Female" if i % 2 else "Male", 30 + i, hh_id, home_id, standing),
        )
        aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,money_cents,fun,stress) VALUES(?,?,?,50,40)",
            (aid, home_id, money),
        )
    c.commit()
    return c


def test_query_helpers_safe_before_schema():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    assert relief.list_drives(c) == []
    s = relief.relief_summary(c)
    assert s["total_drives"] == 0
    assert s["latest"] is None
    c.close()


def test_severe_weather_trigger_and_exact_conservation():
    c = _make_town_conn("relief_storm")
    try:
        day = 2
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (day, "storm", 64, 2),
        )
        before_sum = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]
        before_town = economy.town_balance(c)

        res = relief.daily_relief(c, day, seed="relief_storm")
        assert res is not None
        assert res["trigger_kind"] == "severe_weather"
        assert res["raised_cents"] > 0
        assert res["raised_cents"] == res["distributed_cents"]
        assert res["donors_count"] == 3
        assert res["recipients_count"] == 4

        after_sum = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]
        assert after_sum == before_sum
        assert economy.town_balance(c) == before_town

        # Recipient stress relieved, donor standing boosted
        relieved = c.execute("SELECT COUNT(*) FROM agent_state WHERE stress < 40").fetchone()[0]
        assert relieved == 4
        boosted_standing = c.execute("SELECT COUNT(*) FROM agents WHERE standing > 5").fetchone()[0]
        assert boosted_standing >= 3

        ev = c.execute("SELECT importance FROM events WHERE data LIKE '%relief_drive%'").fetchone()
        assert ev is not None and ev["importance"] == 4
    finally:
        c.close()


def test_brownout_trigger_and_conservation():
    c = _make_town_conn("relief_brownout")
    try:
        day = 4
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (day, "overcast", 60, 0),
        )
        c.execute(
            """INSERT OR REPLACE INTO utility_days
               (day, demand_mw, supply_mw, renewable_pct, grid_status, outage_district,
                maintenance_cents, rebate_cents, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (day, 12.0, 8.0, 50.0, "brownout", "The Flats", 0, 0, "brownout"),
        )
        before_sum = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]

        res = relief.daily_relief(c, day, seed="relief_brownout")
        assert res is not None
        assert res["trigger_kind"] == "grid_brownout"
        assert res["district"] == "The Flats"
        assert res["raised_cents"] == res["distributed_cents"] > 0
        assert c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0] == before_sum
    finally:
        c.close()


def test_mutual_aid_cadence_determinism_and_idempotency():
    c1 = _make_town_conn("det_relief")
    c2 = _make_town_conn("det_relief")
    try:
        for d in range(45):
            # Keep weather calm so mutual_aid cadence is tested cleanly
            c1.execute(
                "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
                (d, "clear", 70, 0),
            )
            c2.execute(
                "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
                (d, "clear", 70, 0),
            )
            r1 = relief.daily_relief(c1, d, seed="det_relief")
            r2 = relief.daily_relief(c2, d, seed="det_relief")
            assert r1 == r2

        s1 = relief.relief_summary(c1)
        assert s1["total_drives"] >= 2
        assert s1 == relief.relief_summary(c2)

        # Idempotent on already-processed day
        first_day = s1["drives"][0]["day"]
        before_sum = c1.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]
        again = relief.daily_relief(c1, first_day, seed="det_relief")
        assert again == s1["drives"][0]
        assert c1.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0] == before_sum
    finally:
        c1.close()
        c2.close()
