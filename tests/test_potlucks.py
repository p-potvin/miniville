"""Tests for Community Potlucks & Block Parties (v0.59)."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import db, economy, potlucks, world
from miniville.seasons import season_of
from miniville.timekeeper import TICKS_PER_DAY, is_weekend


def _make_town_conn(seed: str = "potluck_seed") -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    potlucks.ensure_potlucks_schema(c)
    db.set_meta(c, "seed", seed)
    db.set_meta(c, "tick", "0")

    # Create homes and 10 adult residents across two districts
    for d_idx, district in enumerate(["Downtown", "Lakeshore"]):
        c.execute(
            "INSERT INTO places(name,kind,district,capacity) VALUES(?,?,?,?)",
            (f"Home_{district}", "home", district, 8),
        )
        home_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO households(name,home_place_id) VALUES(?,?)", (f"HH_{district}", home_id))
        hh_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        for i in range(5):
            idx = d_idx * 5 + i + 1
            c.execute(
                """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
                   household_id,home_place_id,alive)
                   VALUES(?,?,?,?,0,'never_married','clerk',?,?,1)""",
                (f"u{idx}", f"Resident {idx}", "Female" if idx % 2 else "Male", 28 + idx, hh_id, home_id),
            )
            aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute(
                """INSERT INTO agent_state(agent_id,place_id,money_cents,social,fun,stress)
                   VALUES(?,?,5000,50,50,30)""",
                (aid, home_id),
            )
    c.commit()
    return c


def _total_money(c: sqlite3.Connection) -> int:
    a = c.execute("SELECT COALESCE(SUM(money_cents), 0) FROM agent_state").fetchone()[0]
    b = c.execute("SELECT COALESCE(SUM(balance_cents), 0) FROM businesses").fetchone()[0]
    t = c.execute("SELECT COALESCE(balance_cents, 0) FROM town_account WHERE id=1").fetchone()[0]
    return int(a + b + t)


def test_query_helpers_safe_before_schema():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    assert potlucks.list_potlucks(c) == []
    s = potlucks.potlucks_summary(c)
    assert s["total_potlucks"] == 0
    assert s["latest"] is None
    c.close()


def test_cadence_and_weekend_only():
    c = _make_town_conn("cadence_seed")
    try:
        hosted_days: list[int] = []
        for d in range(120):
            res = potlucks.daily_potlucks(c, d, seed="cadence_seed")
            if res is not None:
                assert is_weekend(d * TICKS_PER_DAY)
                hosted_days.append(d)

        assert 5 <= len(hosted_days) <= 9
        for prev, nxt in zip(hosted_days, hosted_days[1:]):
            assert nxt - prev >= potlucks.MIN_CADENCE_DAYS
            assert nxt - prev <= potlucks.MAX_CADENCE_DAYS + 6  # next weekend after 21d
    finally:
        c.close()


def test_weather_outdoor_vs_indoor_mode():
    c_out = _make_town_conn("w_mode")
    c_in = _make_town_conn("w_mode")
    try:
        # Pick a spring/summer weekend day
        spring_wknd = next(
            d for d in range(80, 160)
            if is_weekend(d * TICKS_PER_DAY) and season_of(d) in ("spring", "summer")
        )
        c_out.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (spring_wknd, "clear", 72, 0),
        )
        c_in.execute(
            "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES(?,?,?,?)",
            (spring_wknd, "storm", 64, 2),
        )
        r_out = potlucks.daily_potlucks(c_out, spring_wknd, seed="w_mode")
        r_in = potlucks.daily_potlucks(c_in, spring_wknd, seed="w_mode")

        assert r_out is not None and r_out["weather_mode"] == "outdoor_block_party"
        assert r_in is not None and r_in["weather_mode"] == "indoor_potluck"
    finally:
        c_out.close()
        c_in.close()


def test_exact_money_conservation_and_mood_affinity_updates():
    c = _make_town_conn("conserve_seed")
    try:
        wknd = next(d for d in range(10) if is_weekend(d * TICKS_PER_DAY))
        before_total = _total_money(c)
        before_agents = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]
        before_biz = c.execute("SELECT SUM(balance_cents) FROM businesses").fetchone()[0]

        res = potlucks.daily_potlucks(c, wknd, seed="conserve_seed")
        assert res is not None
        assert res["supply_cents"] > 0

        after_total = _total_money(c)
        after_agents = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]
        after_biz = c.execute("SELECT SUM(balance_cents) FROM businesses").fetchone()[0]

        # Exact 1:1 conservation (zero net mint/burn)
        assert after_total == before_total
        assert before_agents - after_agents == res["supply_cents"]
        assert after_biz - before_biz == res["supply_cents"]

        # Mood/needs improved for attendees
        boosted = c.execute(
            "SELECT COUNT(*) FROM agent_state WHERE social > 50 AND fun > 50 AND stress < 30"
        ).fetchone()[0]
        assert boosted == res["attendees_count"]

        # Neighbor relationships warmed
        warmed = c.execute(
            "SELECT COUNT(*) FROM relationships WHERE affinity > 0 AND interactions >= 1"
        ).fetchone()[0]
        assert warmed == res["attendees_count"] - 1

        # Event logged with importance 3 and tag potluck_hosted
        ev = c.execute(
            "SELECT importance, data FROM events WHERE data LIKE '%potluck_hosted%'"
        ).fetchone()
        assert ev is not None
        assert ev["importance"] == 3
    finally:
        c.close()


def test_determinism_and_idempotency():
    c1 = _make_town_conn("det_potluck")
    c2 = _make_town_conn("det_potluck")
    try:
        wknd = next(d for d in range(10) if is_weekend(d * TICKS_PER_DAY))
        r1 = potlucks.daily_potlucks(c1, wknd, seed="det_potluck")
        r2 = potlucks.daily_potlucks(c2, wknd, seed="det_potluck")
        assert r1 == r2

        before_money = _total_money(c1)
        before_agents = c1.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]
        again = potlucks.daily_potlucks(c1, wknd, seed="det_potluck")
        assert again == r1
        assert _total_money(c1) == before_money
        assert c1.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0] == before_agents
        assert c1.execute("SELECT COUNT(*) FROM potlucks WHERE day = ?", (wknd,)).fetchone()[0] == 1

        summary = potlucks.potlucks_summary(c1)
        assert summary["total_potlucks"] == 1
        assert summary["latest"] == r1
    finally:
        c1.close()
        c2.close()
