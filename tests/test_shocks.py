"""God-mode shocks: closures, fires, festivals, and the cohabitation guard."""
import json
import sqlite3

import pytest

from miniville import db as mvdb
from miniville import economy, engine, schedules, shocks, world
from miniville.life import dating_arc_check
from miniville.seasons import holiday_for
from miniville.timekeeper import TICKS_PER_DAY

DAY = TICKS_PER_DAY


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    mvdb.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) "
              "VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    for i, name in enumerate(["Ada Ashbrook", "Ben Briar"], start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               hobbies_json,home_place_id) VALUES(?,?,?,30,'never_married',
               'cook','[]',?)""", (f"t{i}", name, "Female", home))
        c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(?,?)",
                  (i, home))
    mvdb.set_meta(c, "seed", "test")
    mvdb.set_meta(c, "tick", "0")
    economy.ensure_businesses(c)
    c.commit()
    yield c
    c.close()


def _place(conn, name):
    return conn.execute("SELECT id FROM places WHERE name=?", (name,)).fetchone()["id"]


def _biz(conn, name):
    return conn.execute(
        "SELECT * FROM businesses WHERE place_id=?",
        (_place(conn, name),)).fetchone()


def _employ(conn, agent_id, venue):
    pid = _place(conn, venue)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,
           shift_end,work_days) VALUES(?,?,'cook',10000,12,30,62)""",
        (agent_id, pid))
    conn.execute("UPDATE agents SET work_place_id=? WHERE id=?", (pid, agent_id))


# --- closure & fire ---------------------------------------------------------


def test_closure_shock_lays_off_staff_and_closes(conn):
    _employ(conn, 1, "Riverside Diner")
    out = shocks.inject(conn, "closure", "Riverside", "test")
    assert out["ok"]
    b = _biz(conn, "Riverside Diner")
    assert b["status"] == "closed" and b["closed_tick"] == 0
    diner = _place(conn, "Riverside Diner")
    assert conn.execute("SELECT COUNT(*) n FROM jobs WHERE place_id=?",
                        (diner,)).fetchone()["n"] == 0
    assert conn.execute("SELECT work_place_id FROM agents WHERE id=1"
                        ).fetchone()["work_place_id"] is None
    ev = conn.execute(
        "SELECT * FROM events WHERE kind='town_event' ORDER BY id DESC"
    ).fetchone()
    assert json.loads(ev["data"])["tag"] == "shock_closure"
    assert "1 people lost their jobs" in json.loads(ev["data"])["text"]


def test_closure_reopen_day_overrides_the_market_cooldown(conn):
    shocks.inject(conn, "closure", "Riverside", "test", days=3)
    b = _biz(conn, "Riverside Diner")
    assert b["reopen_day"] == 3

    # settling for days before the reopening leaves it shut
    assert economy.settle_businesses(conn, 2 * DAY, "test")["reopened"] == 0
    # when the day arrives it reopens before anyone plans
    assert economy.settle_businesses(conn, 3 * DAY, "test")["reopened"] == 1
    b = _biz(conn, "Riverside Diner")
    assert b["status"] == "open" and b["reopen_day"] is None


def test_closure_without_days_uses_the_normal_reopen_rule(conn):
    shocks.inject(conn, "closure", "Riverside", "test")
    assert _biz(conn, "Riverside Diner")["reopen_day"] is None
    assert economy.settle_businesses(conn, 10 * DAY, "test")["reopened"] == 0
    assert economy.settle_businesses(
        conn, (economy.REOPEN_AFTER_DAYS + 1) * DAY, "test")["reopened"] == 1


def test_fire_injures_occupants_and_schedules_repairs(conn):
    diner = _place(conn, "Riverside Diner")
    _employ(conn, 1, "Riverside Diner")
    conn.execute(
        "UPDATE agent_state SET place_id=?, activity='leisure' "
        "WHERE agent_id IN (1,2)", (diner,))

    out = shocks.inject(conn, "fire", "Riverside", "test")
    assert out["ok"] and "2 hurt" in out["message"]

    b = _biz(conn, "Riverside Diner")
    assert b["status"] == "closed"
    assert b["reopen_day"] == shocks.FIRE_REPAIR_DAYS
    hurt = conn.execute(
        "SELECT COUNT(*) n FROM conditions WHERE kind='sick'").fetchone()["n"]
    assert hurt == 2
    # both occupants were evacuated home to rest
    home = conn.execute(
        "SELECT home_place_id FROM agents WHERE id=1").fetchone()["home_place_id"]
    rows = conn.execute(
        "SELECT place_id, activity FROM agent_state ORDER BY agent_id").fetchall()
    assert all(r["place_id"] == home and r["activity"] == "resting" for r in rows)
    ev = conn.execute(
        "SELECT * FROM events WHERE kind='town_event' ORDER BY id DESC"
    ).fetchone()
    data = json.loads(ev["data"])
    assert data["tag"] == "shock_fire" and ev["importance"] == 5


def test_shock_reroutes_the_rest_of_the_days_plans(conn):
    diner = _place(conn, "Riverside Diner")
    home = conn.execute(
        "SELECT home_place_id FROM agents WHERE id=1").fetchone()["home_place_id"]
    conn.execute(
        "INSERT INTO plans(agent_id,tick,place_id,activity) VALUES(1,10,?,'leisure')",
        (diner,))
    conn.execute(
        "INSERT INTO plans(agent_id,tick,place_id,activity) VALUES(1,30,?,'leisure')",
        (diner,))
    mvdb.set_meta(conn, "tick", "20")

    shocks.inject(conn, "closure", "Riverside", "test")

    past = conn.execute(
        "SELECT place_id, activity FROM plans WHERE agent_id=1 AND tick=10"
    ).fetchone()
    later = conn.execute(
        "SELECT place_id, activity FROM plans WHERE agent_id=1 AND tick=30"
    ).fetchone()
    assert past["place_id"] == diner                     # history stands
    assert later["place_id"] == home and later["activity"] == "home"


def test_scheduled_closure_lands_at_day_start(conn):
    _employ(conn, 1, "Riverside Diner")
    out = shocks.inject(conn, "closure", "Riverside", "test", day=2)
    assert out["ok"] and "scheduled" in out["message"]
    assert _biz(conn, "Riverside Diner")["status"] == "open"

    mvdb.set_meta(conn, "tick", str(2 * DAY))
    stats = engine.step(conn, "test")
    assert stats["shocks"] == 1
    assert _biz(conn, "Riverside Diner")["status"] == "closed"
    assert conn.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"] == 0


def test_shock_rejects_homes_unknown_venues_and_the_past(conn):
    assert not shocks.inject(conn, "fire", "H1", "test")["ok"]
    assert not shocks.inject(conn, "closure", "Nowhere", "test")["ok"]
    assert not shocks.inject(conn, "closure", "Riverside", "test", day=-1)["ok"]
    conn.execute("UPDATE businesses SET status='closed' WHERE place_id=?",
                 (_place(conn, "Riverside Diner"),))
    assert not shocks.inject(conn, "closure", "Riverside", "test")["ok"]


# --- festival ---------------------------------------------------------------


def test_festival_surfaces_as_a_holiday(conn):
    out = shocks.inject(conn, "festival", "Lush Meadow Park", "test")
    assert out["ok"] and "day 2" in out["message"]

    h = holiday_for(conn, 1)
    assert h is not None and h.tag == "festival"
    assert h.venue == "Lush Meadow Park" and not h.day_off
    assert holiday_for(conn, 2) is None and holiday_for(conn, 5) is None

    # announced today, lands tomorrow
    ev = conn.execute(
        "SELECT * FROM events WHERE kind='town_event' ORDER BY id").fetchone()
    assert json.loads(ev["data"])["tag"] == "festival_announced"


def test_festival_day_draws_a_crowd(conn):
    park = _place(conn, "Lush Meadow Park")
    shocks.inject(conn, "festival", "Lush Meadow Park", "test")
    conn.execute(
        "UPDATE shocks SET detail=json_set(detail,'$.p_attend',1.0) "
        "WHERE kind='festival'")

    agent = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    plan = schedules.build_plan(conn, agent, 1, "test")
    celebrated = [t for t, place, act in plan
                  if act == "celebrate" and place == park]
    assert celebrated == list(range(shocks.FESTIVAL_START, shocks.FESTIVAL_END))


def test_festival_is_announced_at_day_start(conn):
    shocks.inject(conn, "festival", "Miniville Community Center", "test")
    mvdb.set_meta(conn, "tick", str(DAY))
    engine.step(conn, "test")
    tags = [json.loads(r["data"]).get("tag") for r in conn.execute(
        "SELECT data FROM events WHERE day=1 AND kind='town_event'")]
    assert "festival" in tags


def test_festival_rejects_today_and_double_booking(conn):
    assert not shocks.inject(conn, "festival", "Park", "test", day=0)["ok"]
    shocks.inject(conn, "festival", "Lush Meadow Park", "test")
    out = shocks.inject(conn, "festival", "The Bijou Theater", "test", day=1)
    assert not out["ok"] and "already has a festival" in out["message"]


# --- the cohabitation guard -------------------------------------------------


def _rel(conn, a, b, label, rom=0.0, fam=0.0):
    conn.execute(
        """INSERT OR REPLACE INTO relationships(a_id,b_id,familiarity,affinity,
           romance,label,interactions,last_met_tick)
           VALUES(?,?,?,0,?,?,0,0)""", (min(a, b), max(a, b), fam, rom, label))


def _get_rel(conn, a, b):
    return conn.execute(
        "SELECT * FROM relationships WHERE a_id=? AND b_id=?",
        (min(a, b), max(a, b))).fetchone()


def test_a_taken_resident_does_not_move_in_with_a_second_sweetheart(conn):
    conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id) VALUES('t3','Cora Dale','Female',30,
           'never_married','clerk','[]',
           (SELECT id FROM places WHERE name='H1'))""")
    conn.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(3,1)")
    _rel(conn, 1, 3, "partner")          # Ada already lives with Cora
    _rel(conn, 1, 2, "sweetheart", rom=80)

    home_before = conn.execute(
        "SELECT household_id FROM agents WHERE id=2").fetchone()["household_id"]
    for tick in range(300):
        rel = _get_rel(conn, 1, 2)
        assert dating_arc_check(conn, 1, 2, rel, tick, "test") is None
    assert conn.execute(
        "SELECT household_id FROM agents WHERE id=2"
    ).fetchone()["household_id"] == home_before


def test_partners_are_not_demoted_back_to_sweethearts(conn):
    """Regression: interact() recomputed 'sweetheart' from rom>80 and demoted
    'partner' back to it, so the same pair could emit 'moved in together'
    every time they met (day-37 Laverne Miller x10)."""
    from miniville.encounters import interact
    _rel(conn, 1, 2, "partner", rom=82, fam=50)   # rom<85: no marriage path
    a = conn.execute("SELECT * FROM agents WHERE id=1").fetchone()
    b = conn.execute("SELECT * FROM agents WHERE id=2").fetchone()
    home = a["home_place_id"]
    before = conn.execute(
        """SELECT COUNT(*) n FROM events
           WHERE kind='life_event' AND json_extract(data,'$.tag')='cohabitation'"""
    ).fetchone()["n"]
    for tick in range(60):
        interact(conn, a, b, home, tick, "test")
    # they may legitimately marry, but must never slip back to sweetheart
    assert _get_rel(conn, 1, 2)["label"] in ("partner", "spouse")
    after = conn.execute(
        """SELECT COUNT(*) n FROM events
           WHERE kind='life_event' AND json_extract(data,'$.tag')='cohabitation'"""
    ).fetchone()["n"]
    assert after == before


def test_cohabitation_still_fires_for_a_free_pair(conn):
    _rel(conn, 1, 2, "sweetheart", rom=80)
    fired = False
    for tick in range(300):
        rel = _get_rel(conn, 1, 2)
        if dating_arc_check(conn, 1, 2, rel, tick, "test") == "cohabitation":
            fired = True
            break
    assert fired
    same = conn.execute(
        "SELECT household_id FROM agents WHERE id=1").fetchone()["household_id"]
    assert conn.execute(
        "SELECT household_id FROM agents WHERE id=2"
    ).fetchone()["household_id"] == same


# --- listing ----------------------------------------------------------------


def test_list_shocks_reports_everything(conn):
    shocks.inject(conn, "closure", "Riverside", "test", days=2)
    shocks.inject(conn, "festival", "Lush Meadow Park", "test")
    rows = shocks.list_shocks(conn)
    assert len(rows) == 2
    fest = next(r for r in rows if r["kind"] == "festival")
    assert fest["venue"] == "Lush Meadow Park" and fest["applied"] == 0
    close = next(r for r in rows if r["kind"] == "closure")
    assert close["venue"] == "Riverside Diner" and close["applied"] == 1
