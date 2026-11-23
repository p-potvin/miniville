"""Tests for Real Estate Deeds, Mortgages & Dynasty Home Inheritance (v0.62)."""
from __future__ import annotations

import sqlite3

from miniville import db, economy, real_estate, world


def _make_town_conn(seed: str = "re_seed") -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    real_estate.ensure_real_estate_schema(c)
    db.set_meta(c, "seed", seed)
    db.set_meta(c, "tick", "0")

    # Create 3 homes with thriving adult residents (+ an heir in Home 1)
    for idx in range(1, 4):
        district = ["Downtown", "Lakeshore", "Greenhill"][idx - 1]
        c.execute(
            "INSERT INTO places(name,kind,district,capacity) VALUES(?,?,?,?)",
            (f"Home_{idx}", "home", district, 6),
        )
        home_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO households(name,home_place_id) VALUES(?,?)", (f"HH_{idx}", home_id))
        hh_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               household_id,home_place_id,alive)
               VALUES(?,?,?,?,0,'married_present','engineer',?,?,1)""",
            (f"owner-{idx}", f"Alice Vance{idx}", "Female", 45, hh_id, home_id),
        )
        owner_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            "INSERT INTO agent_state(agent_id,place_id,money_cents,fun,stress) VALUES(?,?,250000,50,30)",
            (owner_id, home_id),
        )
        if idx == 1:
            # Add an adult child heir for Home 1
            c.execute(
                """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
                   household_id,home_place_id,alive,parent_a_id)
                   VALUES('heir-1','Leo Vance1','Male',22,0,'never_married','clerk',?,?,1,?)""",
                (hh_id, home_id, owner_id),
            )
            heir_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute(
                "INSERT INTO agent_state(agent_id,place_id,money_cents,fun,stress) VALUES(?,?,40000,50,20)",
                (heir_id, home_id),
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
    assert real_estate.list_deeds(c) == []
    s = real_estate.real_estate_summary(c)
    assert s["total_deeds"] == 0
    assert s["deeds"] == []
    c.close()


def test_deed_purchase_mortgage_paydown_and_conservation():
    c = _make_town_conn("re_buy")
    try:
        before_total = _total_money(c)
        before_town = economy.town_balance(c)

        # Day 1: first deed purchase happens
        r1 = real_estate.daily_real_estate(c, 1, seed="re_buy")
        assert r1["purchased"] == 1
        assert r1["mortgage_paid_cents"] == 0
        assert _total_money(c) == before_total
        assert economy.town_balance(c) - before_town == real_estate.DOWN_PAYMENT_CENTS

        deeds = real_estate.list_deeds(c)
        assert len(deeds) == 1
        deed = deeds[0]
        assert deed["generation"] == 1
        assert deed["mortgage_cents"] == deed["price_cents"] - real_estate.DOWN_PAYMENT_CENTS

        # Event logged
        ev = c.execute("SELECT importance FROM events WHERE data LIKE '%home_purchased%'").fetchone()
        assert ev is not None and ev["importance"] == 3

        # Day 7: weekly mortgage installment collected
        town_before_m = economy.town_balance(c)
        m_before = deed["mortgage_cents"]
        r7 = real_estate.daily_real_estate(c, 7, seed="re_buy")
        assert r7["mortgage_paid_cents"] == real_estate.MORTGAGE_INSTALLMENT_CENTS
        assert _total_money(c) == before_total
        assert economy.town_balance(c) - town_before_m == real_estate.MORTGAGE_INSTALLMENT_CENTS

        updated = c.execute(
            "SELECT mortgage_cents FROM home_deeds WHERE place_id = ?", (deed["place_id"],)
        ).fetchone()
        assert updated["mortgage_cents"] == m_before - real_estate.MORTGAGE_INSTALLMENT_CENTS
    finally:
        c.close()


def test_dynasty_heir_inheritance_and_release():
    c = _make_town_conn("re_inherit")
    try:
        # Force a deed on Home 1 (which has heir Leo Vance1) and Home 2 (no heir)
        home1 = c.execute("SELECT id FROM places WHERE name='Home_1'").fetchone()["id"]
        owner1 = c.execute("SELECT id FROM agents WHERE uuid='owner-1'").fetchone()["id"]
        home2 = c.execute("SELECT id FROM places WHERE name='Home_2'").fetchone()["id"]
        owner2 = c.execute("SELECT id FROM agents WHERE uuid='owner-2'").fetchone()["id"]

        c.execute(
            """INSERT INTO home_deeds
               (place_id, owner_agent_id, surname, acquired_day, price_cents,
                mortgage_cents, renovations_count, generation, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (home1, owner1, "Vance1", 1, 100_000, 50_000, 0, 1, "Initial"),
        )
        c.execute(
            """INSERT INTO home_deeds
               (place_id, owner_agent_id, surname, acquired_day, price_cents,
                mortgage_cents, renovations_count, generation, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (home2, owner2, "Vance2", 1, 90_000, 40_000, 0, 1, "Initial"),
        )

        # Both owners pass away
        c.execute("UPDATE agents SET alive = 0 WHERE id IN (?, ?)", (owner1, owner2))

        res = real_estate.daily_real_estate(c, 5, seed="re_inherit")
        assert res["inherited"] == 1
        assert res["released"] == 1

        # Home 1 transferred to Leo Vance1 with generation=2
        heir1 = c.execute("SELECT id FROM agents WHERE uuid='heir-1'").fetchone()["id"]
        d1 = c.execute("SELECT owner_agent_id, generation FROM home_deeds WHERE place_id = ?", (home1,)).fetchone()
        assert d1["owner_agent_id"] == heir1
        assert d1["generation"] == 2

        # Home 2 released (deleted)
        d2 = c.execute("SELECT 1 FROM home_deeds WHERE place_id = ?", (home2,)).fetchone()
        assert d2 is None

        # home_inherited event logged
        ev = c.execute("SELECT importance FROM events WHERE data LIKE '%home_inherited%'").fetchone()
        assert ev is not None and ev["importance"] == 3
    finally:
        c.close()


def test_determinism_and_idempotency():
    c1 = _make_town_conn("det_re")
    c2 = _make_town_conn("det_re")
    try:
        for d in range(30):
            r1 = real_estate.daily_real_estate(c1, d, seed="det_re")
            r2 = real_estate.daily_real_estate(c2, d, seed="det_re")
            assert r1 == r2
        assert real_estate.real_estate_summary(c1) == real_estate.real_estate_summary(c2)

        # Idempotent on same day
        before = _total_money(c1)
        again = real_estate.daily_real_estate(c1, 29, seed="det_re")
        assert again["skipped"] is True
        assert _total_money(c1) == before
    finally:
        c1.close()
        c2.close()
