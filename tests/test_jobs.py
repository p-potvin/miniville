"""The town labour market: staffing targets, hiring, turnover, retirement."""
import sqlite3

import pytest

from miniville import db, jobs, world

# occupation -> the venue the matcher should pick
NURSE, COOK, CLERK, MECHANIC = "nurse", "cook", "clerk", "mechanic"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)", (home,))
    people = [("Ada Ashbrook", "Female", 30, NURSE, 0),
              ("Ben Ashbrook", "Male", 31, COOK, 0),
              ("Cass Ashbrook", "Female", 9, "student", 1),
              ("Dee Ashbrook", "Female", 68, "retired", 0)]
    for i, (name, sex, age, occ, is_child) in enumerate(people, start=1):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
               hobbies_json,household_id,home_place_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (f"t{i}", name, sex, age, is_child, "never_married", occ, "[]", 1, home))
        c.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                  (i, home, 500_000))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def place_id(conn, name):
    return conn.execute("SELECT id FROM places WHERE name=?", (name,)).fetchone()["id"]


def add_workers(conn, n, occupation, first_id=100):
    """Unemployed adults, ids from first_id up."""
    for i in range(n):
        aid = first_id + i
        conn.execute(
            """INSERT INTO agents(id,uuid,name,sex,age,is_child,occupation,
               household_id,home_place_id) VALUES(?,?,'Worker','Male',35,0,?,1,
               (SELECT id FROM places WHERE name='H1'))""",
            (aid, f"w{aid}", occupation))
        conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                     (aid, 1, 100_000))
    conn.commit()


def employ(conn, agent_ids, place_name):
    for aid in agent_ids:
        conn.execute(
            """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
               work_days) VALUES(?,?,'staff',6300,16,30,62)""",
            (aid, place_id(conn, place_name)))
    conn.commit()


def test_venue_weight_follows_capacity_and_traffic():
    hall = jobs.venue_weight(30, 0, {"civic", "office"})
    busy = jobs.venue_weight(50, 380, {"retail", "food"})
    quiet = jobs.venue_weight(50, 0, {"retail", "food"})
    assert busy > quiet > hall
    assert jobs.venue_weight(10, 0, set()) > 0


def test_venue_targets_cover_every_non_home_venue(conn):
    targets = jobs.venue_targets(conn)
    n_venues = conn.execute("SELECT COUNT(*) c FROM places WHERE kind != 'home'").fetchone()["c"]
    assert len(targets) == n_venues
    # demand-driven: the town hall is a handful of clerks, the busy grocer a crew
    assert targets[place_id(conn, "Town Hall")] < targets[place_id(conn, "Miniville Grocer")]
    assert targets[place_id(conn, "Town Hall")] <= 12       # not the old 348
    assert targets[place_id(conn, "Miniville Grocer")] >= 2


def test_targets_scale_with_the_working_population(conn):
    """A growing town gets more posts; the employment rate stays put."""
    before = sum(jobs.venue_targets(conn).values())
    add_workers(conn, 200, CLERK, first_id=1000)
    after = sum(jobs.venue_targets(conn).values())
    assert after > before


def test_hiring_matches_occupation_to_the_right_venue(conn):
    # only the nurse is unemployed, so the single weekly hire is deterministic
    employ(conn, [2, 4], "The Quaint Corner Tavern")
    result = jobs.hiring_pass(conn, tick=0, seed="test")
    assert result["hired"] == 1
    job = conn.execute(
        "SELECT j.*, a.occupation FROM jobs j JOIN agents a ON a.id=j.agent_id "
        "WHERE a.occupation='nurse'").fetchone()
    assert job is not None
    assert job["place_id"] == place_id(conn, "Miniville General Hospital")
    assert conn.execute("SELECT work_place_id FROM agents WHERE id=1").fetchone()[0] == job["place_id"]
    assert conn.execute(
        "SELECT 1 FROM events WHERE kind='life_event' AND data LIKE '%hired%'").fetchone()


def test_children_and_seniors_are_never_hired(conn):
    # employ the two working-age adults; only the child and the 68-year-old remain
    employ(conn, [1, 2], "The Quaint Corner Tavern")
    before = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    jobs.hiring_pass(conn, tick=0, seed="test")
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == before
    assert not conn.execute("SELECT 1 FROM jobs WHERE agent_id IN (3,4)").fetchone()


def test_a_closed_venue_does_not_hire(conn):
    hid = place_id(conn, "Miniville General Hospital")
    conn.execute("INSERT INTO businesses(place_id,status) VALUES(?,'closed')", (hid,))
    conn.commit()
    jobs.hiring_pass(conn, tick=0, seed="test")
    assert not conn.execute("SELECT 1 FROM jobs WHERE place_id=?", (hid,)).fetchone()


def test_retirement_frees_the_post(conn, monkeypatch):
    monkeypatch.setattr(jobs, "RETIRE_ANNUAL", 1.0)
    monkeypatch.setattr(jobs, "RETIRE_ANNUAL_OLD", 1.0)
    employ(conn, [4], "Miniville General Hospital")
    out = []
    for day in range(1, 400):                # probabilistic per day; sweep
        out = jobs.retirements(conn, tick=48 * day, seed="test")
        if out:
            break
    assert len(out) == 1 and out[0]["age"] == 68
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0
    row = conn.execute("SELECT occupation, work_place_id FROM agents WHERE id=4").fetchone()
    assert row["occupation"] == "Retired" and row["work_place_id"] is None
    assert conn.execute("SELECT 1 FROM events WHERE data LIKE '%retired%'").fetchone()


def test_turnover_sheds_the_surplus_at_one_venue(conn):
    add_workers(conn, 12, CLERK)
    pid = place_id(conn, "Town Hall")
    employ(conn, list(range(100, 112)), "Town Hall")
    target = jobs.venue_targets(conn)[pid]
    assert 12 > target                                     # genuinely overstaffed
    left = jobs.turnover(conn, tick=0, seed="test")
    staff = conn.execute("SELECT COUNT(*) c FROM jobs WHERE place_id=?", (pid,)).fetchone()["c"]
    assert left == max(1, int((12 - target) * jobs.EXCESS_SHED_SHARE))
    assert staff == 12 - left
    assert conn.execute("SELECT 1 FROM events WHERE data LIKE '%left their job%'").fetchone()


def test_hiring_budget_clears_the_firing_rate(conn):
    """The weekly budget must out-pace separations or the town sheds posts."""
    from miniville.life import P_FIRE
    add_workers(conn, 30, COOK)
    add_workers(conn, 40, CLERK, first_id=200)
    employ(conn, list(range(200, 240)), "Town Hall")
    result = jobs.hiring_pass(conn, tick=0, seed="test")
    separations = 40 * P_FIRE * 7
    assert result["hired"] >= min(30, int(separations * jobs.SEPARATION_MARGIN))
    assert result["hired"] <= 30                            # never more than the candidates
