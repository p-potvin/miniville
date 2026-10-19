"""Tests for v0.24 out-of-town roots, hometown correspondence, care packages,
shared-state bonding, and milestone diaries."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import (
    correspondence,
    db,
    economy,
    encounters,
    events,
    individual,
    world,
)


@pytest.fixture()
def conn():
    individual.clear_cache()
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    economy.ensure_businesses(c)
    c.execute(
        "INSERT INTO places(name,kind,district,capacity) "
        "VALUES('H1','home','Downtown',6)"
    )
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        "INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           origin_city,origin_state,persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('r1','Ada Ashbrook','Female',32,'never_married','baker',
                  'Austin','Texas','Ada is a baker from Austin, Texas.',
                  '["baking"]','["baking"]',1,?)""",
        (home,),
    )
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           origin_city,origin_state,persona,hobbies_json,skills_json,household_id,home_place_id)
           VALUES('r2','Ben Briar','Male',35,'never_married','cook',
                  'Houston','Texas','Ben is a cook from Houston, Texas.',
                  '["cooking"]','["cooking"]',1,?)""",
        (home,),
    )
    c.execute(
        "INSERT INTO agent_state(agent_id,place_id,money_cents,social,mood) VALUES(1,?,500000,30,'lonely')",
        (home,),
    )
    c.execute(
        "INSERT INTO agent_state(agent_id,place_id,money_cents,social,mood) VALUES(2,?,500000,70,'content')",
        (home,),
    )
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    individual.ensure_profiles(c, "test")
    c.commit()
    yield c
    individual.clear_cache(c)
    c.close()


def test_letters_care_packages_and_shared_roots(conn, monkeypatch):
    monkeypatch.setattr(correspondence, "P_CARE_PACKAGE", 1.0)
    monkeypatch.setattr(correspondence, "P_SHARED_ROOTS", 1.0)

    # Shared state ('Texas') boosts encounter pairing score
    ctx = encounters._affinity_ctx(conn)
    assert ctx["origin_state"][1] == "Texas" and ctx["origin_state"][2] == "Texas"
    score_same = encounters._pair_score(ctx, set(), 1, 2, 0.0)
    ctx_diff = {**ctx, "origin_state": {1: "Texas", 2: "Ohio"}}
    score_diff = encounters._pair_score(ctx_diff, set(), 1, 2, 0.0)
    assert score_same > score_diff

    # Emit a notable milestone so write_milestone_diaries records it
    events.emit(conn, 48, "life_event", place_id=1, a=1, importance=3,
                text="was promoted to head baker", tag="promoted")
    res = correspondence.due(conn, 48, "test")
    assert res["care_packages"] == 1
    assert res["shared_roots"] == 1
    assert res["milestone_diaries"] == 1

    entries = correspondence.diary_of(conn, 1)
    assert any("Austin, Texas" in e["entry"] or "Texas" in e["entry"] for e in entries)
