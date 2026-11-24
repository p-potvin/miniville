"""Tests for Town Utilities, Power Grid & Renewable Micro-Grid (v0.65)."""
from __future__ import annotations

import sqlite3

from miniville import db, economy, utilities, world


def _make_town_conn(seed: str = "util_seed", town_cents: int = 1_000_000) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    utilities.ensure_utilities_schema(c)
    db.set_meta(c, "seed", seed)
    db.set_meta(c, "tick", "0")
    c.execute("UPDATE town_account SET balance_cents = ? WHERE id = 1", (town_cents,))

    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('Home1','home','Downtown',8)")
    home_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('HH1',?)", (home_id,))
    hh_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    hw_id = c.execute("SELECT id FROM places WHERE name='Flats Auto & Hardware'").fetchone()["id"]

    for i in range(1, 7):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               household_id,home_place_id,work_place_id,alive)
               VALUES(?,?,?,?,0,'never_married',?,?,?,?,1)""",
            (
                f"u{i}",
                f"Worker {i}",
                "Male" if i % 2 else "Female",
                30 + i,
                "electrician" if i <= 2 else "clerk",
                hh_id,
                home_id,
                hw_id,
            ),
        )
        aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,5000)", (aid, home_id))
        if i <= 2:
            c.execute(
                """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end)
                   VALUES(?,?,'technician',10000,16,32)""",
                (aid, hw_id),
            )
    c.commit()
    return c


def _total_money(c: sqlite3.Connection) -> int:
    a = c.execute("SELECT COALESCE(SUM(money_cents), 0) FROM agent_state").fetchone()[0]
    b = c.execute("SELECT COALESCE(SUM(balance_cents), 0) FROM businesses").fetchone()[0]
    t = c.execute("SELECT COALESCE(balance_cents, 0) FROM town_account WHERE id=1").fetchone()[0]
    return int(a + b + t)


def test_summary_safe_before_schema():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    s = utilities.utilities_summary(c)
    assert s["today"] is None
    assert s["history"] == []
    c.close()


def test_weather_coupling_brownout_and_maintenance_conservation():
    c = _make_town_conn("util_storm", town_cents=1_000_000)
    try:
        day = 3
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (day, "blizzard", 8, 2),
        )
        before_total = _total_money(c)
        before_town = economy.town_balance(c)
        before_agents = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]

        res = utilities.daily_utilities(c, day, seed="util_storm")
        assert res["grid_status"] in ("strained", "brownout")
        assert res["maintenance_cents"] > 0
        if res["grid_status"] == "brownout":
            assert res["outage_district"] in world.DISTRICTS
            assert c.execute("SELECT 1 FROM events WHERE data LIKE '%grid_alert%'").fetchone() is not None
        else:
            assert res["outage_district"] is None

        # Exact 1:1 conservation: town_account -> agent_state
        assert _total_money(c) == before_total
        assert before_town - economy.town_balance(c) == res["maintenance_cents"]
        assert (
            c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0] - before_agents
            == res["maintenance_cents"]
        )
    finally:
        c.close()


def test_weekly_green_rebate_conservation():
    c = _make_town_conn("util_green", town_cents=1_000_000)
    try:
        day = 7  # day % 7 == 0
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (day, "clear", 74, 0),
        )
        before_total = _total_money(c)
        before_town = economy.town_balance(c)
        before_agents = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]

        res = utilities.daily_utilities(c, day, seed="util_green")
        assert res["grid_status"] in ("surplus", "nominal")
        assert res["outage_district"] is None
        assert res["renewable_pct"] >= 65.0
        assert res["rebate_cents"] > 0

        assert _total_money(c) == before_total
        assert before_town - economy.town_balance(c) == res["rebate_cents"]
        assert (
            c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0] - before_agents
            == res["rebate_cents"]
        )
        assert c.execute("SELECT 1 FROM events WHERE data LIKE '%green_rebate%'").fetchone() is not None
    finally:
        c.close()


def test_deficit_safe_gating():
    c = _make_town_conn("util_poor", town_cents=100_000)
    try:
        # Brownout day with low purse: no maintenance payout
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (3, "blizzard", 8, 2),
        )
        r_storm = utilities.daily_utilities(c, 3, seed="util_poor")
        assert r_storm["maintenance_cents"] == 0
        assert economy.town_balance(c) == 100_000

        # Weekly clear day with purse < 700_000: no rebate payout
        c.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (7, "clear", 74, 0),
        )
        r_clear = utilities.daily_utilities(c, 7, seed="util_poor")
        assert r_clear["rebate_cents"] == 0
        assert economy.town_balance(c) == 100_000
    finally:
        c.close()


def test_determinism_and_idempotency():
    c1 = _make_town_conn("det_util", town_cents=1_000_000)
    c2 = _make_town_conn("det_util", town_cents=1_000_000)
    try:
        for d in range(15):
            r1 = utilities.daily_utilities(c1, d, seed="det_util")
            r2 = utilities.daily_utilities(c2, d, seed="det_util")
            assert r1 == r2
        assert utilities.utilities_summary(c1) == utilities.utilities_summary(c2)

        before_total = _total_money(c1)
        again = utilities.daily_utilities(c1, 7, seed="det_util")
        assert again == utilities.utilities_summary(c1)["history"][7]
        assert _total_money(c1) == before_total
    finally:
        c1.close()
        c2.close()
