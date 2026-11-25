"""Tests for Town Ensembles, Choir & Street Buskers (v0.69)."""
from __future__ import annotations

import sqlite3

from miniville import db, economy, music, world
from miniville.seasons import season_of
from miniville.timekeeper import TICKS_PER_DAY, is_weekend


def _make_town_conn(seed: str = "music_seed") -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    music.ensure_music_schema(c)
    db.set_meta(c, "seed", seed)
    db.set_meta(c, "tick", "0")

    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('Home1','home','Downtown',12)")
    home_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('HH1',?)", (home_id,))
    hh_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    for i in range(1, 11):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               household_id,home_place_id,alive)
               VALUES(?,?,?,?,0,'never_married','musician',?,?,1)""",
            (f"m{i}", f"Musician {i}", "Female" if i % 2 else "Male", 24 + i, hh_id, home_id),
        )
        aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,money_cents,fun,social,stress) VALUES(?,?,10000,50,50,30)",
            (aid, home_id),
        )
    c.commit()
    return c


def test_query_helpers_safe_before_schema():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    assert music.list_performances(c) == []
    s = music.music_summary(c)
    assert s["total_performances"] == 0
    assert s["latest"] is None
    c.close()


def test_cadence_over_90_days():
    c = _make_town_conn("music_cadence")
    try:
        days: list[int] = []
        for d in range(90):
            res = music.daily_music(c, d, seed="music_cadence")
            if res is not None:
                days.append(d)
        assert 6 <= len(days) <= 11
        for prev, nxt in zip(days, days[1:]):
            assert music.MIN_CADENCE_DAYS <= (nxt - prev) <= music.MAX_CADENCE_DAYS
    finally:
        c.close()


def test_weather_indoor_fallback():
    c_out = _make_town_conn("music_w")
    c_in = _make_town_conn("music_w")
    try:
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
            (spring_wknd, "storm", 62, 2),
        )
        r_out = music.daily_music(c_out, spring_wknd, seed="music_w")
        r_in = music.daily_music(c_in, spring_wknd, seed="music_w")

        assert r_out is not None and r_out["weather_mode"] == "outdoor_stage"
        assert r_in is not None and r_in["weather_mode"] == "indoor_shelter"
    finally:
        c_out.close()
        c_in.close()


def test_exact_tip_conservation_and_mood_boost():
    c = _make_town_conn("music_tips")
    try:
        wknd = next(d for d in range(10) if is_weekend(d * TICKS_PER_DAY))
        before_agents_sum = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]

        res = music.daily_music(c, wknd, seed="music_tips")
        assert res is not None
        assert res["tips_cents"] > 0
        assert res["audience_count"] == 9

        after_agents_sum = c.execute("SELECT SUM(money_cents) FROM agent_state").fetchone()[0]
        # Exact 1:1 conservation: listener -> leader
        assert after_agents_sum == before_agents_sum

        leader_bal = c.execute(
            "SELECT money_cents FROM agent_state WHERE agent_id = ?", (res["leader_agent_id"],)
        ).fetchone()[0]
        assert leader_bal == 10000 + res["tips_cents"]

        boosted = c.execute(
            "SELECT COUNT(*) FROM agent_state WHERE fun > 50 AND social > 50 AND stress < 30"
        ).fetchone()[0]
        assert boosted == res["audience_count"]

        ev = c.execute("SELECT importance FROM events WHERE data LIKE '%live_music%'").fetchone()
        assert ev is not None and ev["importance"] == 3
    finally:
        c.close()


def test_determinism_and_idempotency():
    c1 = _make_town_conn("det_music")
    c2 = _make_town_conn("det_music")
    try:
        wknd = next(d for d in range(10) if is_weekend(d * TICKS_PER_DAY))
        r1 = music.daily_music(c1, wknd, seed="det_music")
        r2 = music.daily_music(c2, wknd, seed="det_music")
        assert r1 == r2

        before_leader = c1.execute(
            "SELECT money_cents FROM agent_state WHERE agent_id = ?", (r1["leader_agent_id"],)
        ).fetchone()[0]
        again = music.daily_music(c1, wknd, seed="det_music")
        assert again == r1
        after_leader = c1.execute(
            "SELECT money_cents FROM agent_state WHERE agent_id = ?", (r1["leader_agent_id"],)
        ).fetchone()[0]
        assert after_leader == before_leader
        assert music.music_summary(c1)["total_performances"] == 1
    finally:
        c1.close()
        c2.close()
