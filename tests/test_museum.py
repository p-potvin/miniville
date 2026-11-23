"""Tests for Town Museum, Exhibits & Artifact Curation (v0.55)."""
import sqlite3

import pytest

from miniville import db, museum, world
from miniville.events import HISTORIC, emit
from miniville.timekeeper import TICKS_PER_DAY


def _build_town(seed: str = "museum_seed", n_agents: int = 24) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    places = world.create_world(c)
    home_id = next(iter(places.values()))
    c.execute("INSERT INTO households(name, home_place_id) VALUES('Test House', ?)", (home_id,))
    hh_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])

    surnames = ["Vance", "Lin", "Mercer", "Hale", "Vance", "Lin"]
    for i in range(1, n_agents + 1):
        surname = surnames[(i - 1) % len(surnames)]
        age = 72 if i <= 3 else 30 + (i % 25)
        curious = i % 2 == 0
        traits = '["curious", "reflective"]' if curious else '["practical"]'
        hobbies = '["history", "painting"]' if curious else '["fishing"]'
        c.execute(
            """INSERT INTO agents(
                   id, uuid, name, sex, age, is_child, marital_status, occupation,
                   hobbies_json, traits_json, household_id, home_place_id,
                   work_place_id, alive, standing
               ) VALUES (?, ?, ?, 'Female', ?, 0, 'married', 'artisan', ?, ?, ?, ?, ?, 1, ?)""",
            (
                i,
                f"u-{i}",
                f"Resident{i} {surname}",
                age,
                hobbies,
                traits,
                hh_id,
                home_id,
                places["Miniville Public Library"],
                20 + i,
            ),
        )
        c.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents, stress, fun) VALUES (?, ?, ?, 20, 60)",
            (i, home_id, 120_000),
        )

    # Seed diverse curation sources: keepsakes, plaques, and historic events
    c.execute(
        """INSERT INTO keepsakes(title, kind, craft, creator_id, owner_id, venue_id, created_tick, generations, appreciations)
           VALUES ('Carved Oak Clock', 'heirloom', 'woodwork', 1, 2, ?, 10, 2, 5)""",
        (places["Old Mill Shops"],),
    )
    c.execute(
        """INSERT INTO keepsakes(title, kind, craft, creator_id, owner_id, venue_id, created_tick, generations, appreciations)
           VALUES ('Lakeshore Watercolour', 'craft', 'artwork', 3, 4, ?, 20, 1, 3)""",
        (places["Lakeshore Marina"],),
    )
    c.execute(
        """INSERT INTO historical_plaques(day, tick, place_id, honouree_id, title, inscription, tours_count, tip_cents)
           VALUES (2, 96, ?, 1, 'The Vance Heritage Plaque', 'In honour of lasting civic service.', 1, 1500)""",
        (places["Town Hall"],),
    )
    emit(c, 5 * TICKS_PER_DAY, "town_event", place_id=places["Lush Meadow Park"], a=2, importance=HISTORIC,
         text="The town restored the old bandstand")
    emit(c, 12 * TICKS_PER_DAY, "town_event", place_id=places["Town Hall"], a=4, importance=HISTORIC,
         text="Miniville signed its founding charter renewal")

    db.set_meta(c, "seed", seed)
    db.set_meta(c, "tick", "0")
    c.commit()
    return c


def _total_money(conn: sqlite3.Connection) -> int:
    wallets = conn.execute("SELECT COALESCE(SUM(money_cents), 0) AS s FROM agent_state").fetchone()["s"]
    purse = conn.execute("SELECT balance_cents FROM town_account WHERE id=1").fetchone()["balance_cents"]
    return int(wallets) + int(purse)


def test_schema_and_curation_cadence_and_events():
    conn = _build_town("curation_test", n_agents=20)
    try:
        museum.ensure_museum_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(museum_exhibits)")}
        assert {
            "id", "day_curated", "year_curated", "category", "title",
            "description", "donor_agent_id", "subject_agent_id",
            "place_id", "visitors_count", "featured",
        }.issubset(cols)

        curated_days: list[int] = []
        for day in range(60):
            res = museum.daily_museum(conn, day, "curation_test")
            if res["curated"] is not None:
                curated_days.append(day)

        # Bounded rate: ~1 exhibit every 10-20 days
        assert 3 <= len(curated_days) <= 6
        for a, b in zip(curated_days, curated_days[1:]):
            gap = b - a
            assert museum.MIN_CURATION_GAP_DAYS <= gap <= museum.MAX_CURATION_GAP_DAYS

        exhibits = museum.list_exhibits(conn, limit=24)
        assert len(exhibits) == len(curated_days)
        # Exactly one featured exhibit (the most recently curated)
        featured = [e for e in exhibits if e["featured"] == 1]
        assert len(featured) == 1
        assert featured[0]["day_curated"] == curated_days[-1]

        # museum_exhibit events logged for each curated exhibit
        ev_count = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='museum_exhibit'"
        ).fetchone()["n"]
        assert ev_count == len(curated_days)

        summary = museum.museum_summary(conn)
        assert summary["total_exhibits"] == len(curated_days)
        assert summary["featured"] is not None
        assert summary["total_visitors"] > 0
    finally:
        conn.close()


def test_visitor_tracking_weather_and_curiosity():
    # Compare clear weather vs storm weather across 20 days on identical towns
    c_clear = _build_town("weather_cmp", n_agents=40)
    c_storm = _build_town("weather_cmp", n_agents=40)
    try:
        clear_visitors = 0
        storm_visitors = 0
        for day in range(20):
            c_clear.execute(
                "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES (?, 'clear', 70, 0)",
                (day,),
            )
            c_storm.execute(
                "INSERT OR REPLACE INTO weather_days(day, condition, temp_f, severity) VALUES (?, 'storm', 62, 2)",
                (day,),
            )
            clear_visitors += museum.daily_museum(c_clear, day, "weather_cmp")["visitors"]
            storm_visitors += museum.daily_museum(c_storm, day, "weather_cmp")["visitors"]

        assert storm_visitors > clear_visitors
    finally:
        c_clear.close()
        c_storm.close()

    # Curious residents should visit more often than non-curious residents
    c_cur = _build_town("curiosity_cmp", n_agents=60)
    try:
        # Track fun gains or donations? Better: run 30 days and compare average fun of curious vs non-curious
        # since every museum visit gives +3 fun (starting from 60, capped at 100)
        for day in range(30):
            museum.daily_museum(c_cur, day, "curiosity_cmp")
        curious_fun = c_cur.execute(
            """SELECT AVG(s.fun) AS f FROM agent_state s
               JOIN agents a ON a.id = s.agent_id
               WHERE a.traits_json LIKE '%curious%'"""
        ).fetchone()["f"]
        plain_fun = c_cur.execute(
            """SELECT AVG(s.fun) AS f FROM agent_state s
               JOIN agents a ON a.id = s.agent_id
               WHERE a.traits_json NOT LIKE '%curious%'"""
        ).fetchone()["f"]
        assert float(curious_fun) > float(plain_fun)
    finally:
        c_cur.close()


def test_exact_money_conservation():
    conn = _build_town("money_test", n_agents=30)
    try:
        initial_total = _total_money(conn)
        total_donated = 0
        for day in range(45):
            res = museum.daily_museum(conn, day, "money_test")
            total_donated += res["donations_cents"]
            assert _total_money(conn) == initial_total

        purse = conn.execute("SELECT balance_cents FROM town_account WHERE id=1").fetchone()["balance_cents"]
        assert purse == total_donated
        assert total_donated > 0
        summary = museum.museum_summary(conn)
        assert summary["total_donations_cents"] == total_donated
    finally:
        conn.close()


def test_determinism_and_idempotency():
    c1 = _build_town("det_seed", n_agents=20)
    c2 = _build_town("det_seed", n_agents=20)
    try:
        for day in range(35):
            r1 = museum.daily_museum(c1, day, "det_seed")
            r2 = museum.daily_museum(c2, day, "det_seed")
            assert r1 == r2

        assert museum.list_exhibits(c1) == museum.list_exhibits(c2)
        assert museum.museum_summary(c1) == museum.museum_summary(c2)

        # Idempotency: calling daily_museum again on an already-run day changes nothing
        before_money = _total_money(c1)
        before_summary = museum.museum_summary(c1)
        again = museum.daily_museum(c1, 14, "det_seed")
        assert again["already_ran"] is True
        assert _total_money(c1) == before_money
        assert museum.museum_summary(c1) == before_summary
    finally:
        c1.close()
        c2.close()
