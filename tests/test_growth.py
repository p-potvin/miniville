"""Town growth: births and immigration (dataset stubbed out)."""
import sqlite3

import pytest

from miniville import db, growth, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute("INSERT INTO households(name,home_place_id) VALUES('Ashbrook household',?)",
              (home,))
    for i, name in enumerate(["Ada Ashbrook", "Ben Ashbrook"]):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               hobbies_json,household_id,home_place_id) VALUES(?,?,?,?,?,?,?,?,?)""",
            (f"t{i}", name, "Female" if i == 0 else "Male", 30,
             "married_present", "teacher", '["Reading"]', 1, home))
        c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(?,?)",
                  (i + 1, home))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_births_add_child_to_household(conn):
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,95,80,95,'spouse')""")
    conn.commit()
    born = 0
    # births are probabilistic per day; sweep days until one lands
    for tick in range(0, 48 * 365 * 40, 48):
        born += growth.births(conn, tick, "test")
        if born:
            break
    assert born >= 1
    child = conn.execute(
        "SELECT * FROM agents WHERE is_child=1").fetchone()
    assert child is not None
    assert child["household_id"] == 1
    assert child["age"] == 0
    assert conn.execute(
        "SELECT 1 FROM events WHERE kind='life_event' AND data LIKE '%birth%'"
    ).fetchone()


def test_births_skip_low_romance(conn):
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,95,80,10,'spouse')""")
    conn.commit()
    for tick in range(0, 48 * 200, 48):
        assert growth.births(conn, tick, "test") == 0


def test_immigrate_adds_residents(conn, monkeypatch):
    rows = [{
        "uuid": f"new-{i}", "persona": f"Casey Newcomer{i} is a baker.",
        "professional_persona": "", "sex": "Female", "age": 34,
        "marital_status": "never_married", "education_level": "bachelors",
        "occupation": "baker", "city": "Springfield", "state": "IL",
        "hobbies_and_interests_list": ["Baking"], "skills_and_expertise_list": [],
        "cultural_background": "",
    } for i in range(3)]
    monkeypatch.setattr(growth, "load_persona_rows", lambda *a, **k: rows)

    before = conn.execute("SELECT COUNT(*) c FROM agents").fetchone()["c"]
    arrived = growth.immigrate(conn, 3, tick=48, seed="test")
    after = conn.execute("SELECT COUNT(*) c FROM agents").fetchone()["c"]

    assert arrived == 3
    assert after == before + 3
    # every newcomer got a household, a home, and state
    for r in conn.execute("SELECT id FROM agents WHERE uuid LIKE 'new-%'"):
        st = conn.execute("SELECT * FROM agent_state WHERE agent_id=?",
                          (r["id"],)).fetchone()
        assert st is not None and st["place_id"] is not None
    assert conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='arrival'").fetchone()["c"] == 3


def test_immigrate_skips_known_uuids(conn, monkeypatch):
    rows = [{"uuid": "t0", "persona": "Ada Ashbrook again.", "sex": "Female",
             "age": 30, "occupation": "teacher",
             "hobbies_and_interests_list": [], "skills_and_expertise_list": []}]
    monkeypatch.setattr(growth, "load_persona_rows", lambda *a, **k: rows)
    assert growth.immigrate(conn, 1, tick=0, seed="test") == 0


def test_newborn_records_birth_day(conn, monkeypatch):
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,95,80,95,'spouse')""")

    class BirthRng:
        def random(self):
            return 0

        def choice(self, values):
            return values[0]

    monkeypatch.setattr(growth, "rng_for", lambda *args: BirthRng())
    assert growth.births(conn, 10 * 48, "test") == 1
    newborn = conn.execute(
        "SELECT birth_day FROM agents WHERE is_child=1").fetchone()
    assert newborn["birth_day"] == 10


def _force_birth_roll(monkeypatch):
    class BirthRng:
        def random(self):
            return 0

        def choice(self, values):
            return values[0]

    monkeypatch.setattr(growth, "rng_for", lambda *args: BirthRng())


def _add_spouse_pair(conn):
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,95,80,95,'spouse')""")


def test_no_births_past_fertile_age(conn, monkeypatch):
    _add_spouse_pair(conn)
    conn.execute("UPDATE agents SET age=60")
    _force_birth_roll(monkeypatch)

    for tick in range(0, 48 * 50, 48):
        assert growth.births(conn, tick, "test") == 0


def test_male_partner_age_does_not_gate(conn, monkeypatch):
    _add_spouse_pair(conn)
    conn.execute("UPDATE agents SET age=60 WHERE id=2")
    _force_birth_roll(monkeypatch)

    assert growth.births(conn, 10 * 48, "test") == 1


def test_birth_spacing(conn, monkeypatch):
    _add_spouse_pair(conn)
    _force_birth_roll(monkeypatch)

    assert growth.births(conn, 10 * 48, "test") == 1
    assert growth.births(conn, 200 * 48, "test") == 0
    assert growth.births(conn, (10 + 365 + 1) * 48, "test") == 1


def test_dead_spouse_cannot_conceive(conn, monkeypatch):
    _add_spouse_pair(conn)
    conn.execute("UPDATE agents SET alive=0 WHERE id=2")
    _force_birth_roll(monkeypatch)

    assert growth.births(conn, 10 * 48, "test") == 0


def test_daily_rate_is_calibrated():
    assert abs((1 - (1 - growth.P_BIRTH) ** 365)
               - growth.ANNUAL_BIRTH_RATE) < 1e-9


def test_immigration_reservoir_seed_varies_by_tick(conn, monkeypatch):
    calls = []

    def empty_sample(dataset_dir, n, seed):
        calls.append((n, seed))
        return []

    monkeypatch.setattr(growth, "load_persona_rows", empty_sample)
    growth.immigrate(conn, 1, tick=48, seed="test")
    growth.immigrate(conn, 1, tick=96, seed="test")
    assert calls == [
        (3, "test:immigrate:48"),
        (3, "test:immigrate:96"),
    ]
