"""The Miniville Gazette: weekly front page composition."""
import sqlite3

import pytest

from miniville import db, newspaper, world


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
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id,household_id) VALUES('t0','Ada Ashbrook',
           'Female',30,'married_present','teacher','[]',?,1)""", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(1,?)", (home,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_week_of():
    assert newspaper.week_of(0) == 0
    assert newspaper.week_of(6) == 0
    assert newspaper.week_of(7) == 1


def test_the_paper_covers_the_systems_the_town_gained(conn):
    """The Gazette was blind to the council, clubs, feuds and careers."""
    from miniville.events import emit
    for i, tag in enumerate(("motion_passed", "boycott", "promoted",
                             "group_founded", "obituary", "retired"), start=1):
        emit(conn, 48, "town_event", a=1, importance=3, text=f"{tag} happened",
             tag=tag)
    text = newspaper.publish_week(conn, 0, "test")
    for section in ("The Town Council", "The Feud", "Working Life",
                    "Clubs & Congregations", "Obituaries"):
        assert section in text, f"{section} missing from the Gazette"


def test_a_birth_is_not_headline_history(conn, monkeypatch):
    """A birth matters to a family, not to the town.

    Emitted at HISTORIC it headlined every quiet week and the year in review
    became a list of babies.
    """
    from miniville import growth
    from miniville.events import HISTORIC
    monkeypatch.setattr(growth, "P_BIRTH", 1.0)   # not a 0.03% a day lottery
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
           VALUES(1,2,95,80,95,'spouse')""")
    conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id,household_id) VALUES('t1','Ben Ashbrook',
           'Male',30,'married_present','teacher','[]',1,1)""")
    conn.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(2,1)")
    conn.commit()
    for day in range(0, 48 * 365 * 3, 48):
        if growth.births(conn, day, "test"):
            break
    row = conn.execute(
        "SELECT importance FROM events WHERE data LIKE '%\"tag\": \"birth\"%'"
    ).fetchone()
    assert row is not None and row["importance"] < HISTORIC


def test_year_in_review_reads_the_year_back(conn):
    from miniville.events import emit
    conn.execute("""INSERT INTO council(seat,agent_id,elected_tick,district,backers,
                    backers_wallet,backers_unemployed)
                    VALUES(1,1,0,'Downtown',30,15000,0.1)""")
    conn.commit()
    emit(conn, 48 * 10, "life_event", a=1, importance=5, text="died at 70",
         tag="death")
    emit(conn, 48 * 20, "town_event", a=1, importance=5,
         text="the town went to the polls", tag="election")
    emit(conn, 48 * 30, "town_event", a=1, importance=3,
         text="the council raised the levy", tag="motion_passed")
    emit(conn, 48 * 40, "town_event", a=1, importance=3,
         text="a group is boycotting the tavern", tag="boycott")
    text = newspaper.year_in_review(conn, 1)
    assert "the year 1" in text
    assert "THE COUNCIL" in text
    assert "THE FEUDS" in text
    assert "GONE" in text and "Ada Ashbrook died at 70" in text
    assert "1 boycotts" not in text          # singular reads properly
    assert "A group declared a boycott" in text
    assert "WHAT PEOPLE WILL REMEMBER" in text
    assert "motion_passed happened" not in text   # described, not raw tags


def test_a_year_that_is_not_over_says_so(conn):
    db.set_meta(conn, "tick", str(100 * 48))       # mid-year 1
    conn.commit()
    assert "(so far)" in newspaper.year_in_review(conn, 1)


def test_gazette_is_written_by_a_resident_and_publisher_line_is_visible(conn):
    from miniville import newspaper
    from miniville.conflict import influence_of
    # Gazette staff, with a clear writer/editor
    gazette = conn.execute(
        "SELECT id FROM places WHERE name='Miniville Gazette'").fetchone()["id"]
    writer = conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id,standing)
           VALUES('writer','Laverne Local','Female',42,'never_married',
                  'writer_or_author','[]',1,20)""").lastrowid
    owner = conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id,standing)
           VALUES('owner','Sarah Publisher','Female',45,'never_married',
                  'editor','[]',1,30)""").lastrowid
    for aid, role in ((writer, "writer_or_author"), (owner, "editor")):
        conn.execute(
            """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,
               shift_end,work_days,started_tick,rank,base_wage_cents)
               VALUES(?,?,?,?,16,30,62,0,0,10000)""",
            (aid, gazette, role, 10000))
    conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                 (writer, 1, 500000))
    conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(?,?,?)",
                 (owner, 1, 500000))
    conn.commit()
    text = newspaper.publish_week(conn, 0, "test")
    profile = conn.execute("SELECT * FROM newspaper_profile WHERE id=1").fetchone()
    assert profile["publisher_id"] in (writer, owner)
    assert profile["editor_id"] in (writer, owner)
    assert "Owned by" in text and "by Laverne Local" in text
    assert "Editorial line:" in text
    assert conn.execute("SELECT publisher_id FROM newspapers WHERE week=0").fetchone()[0]


def test_gazette_can_print_a_false_claim_without_changing_the_ledger(conn, monkeypatch):
    from miniville.events import emit
    # Establishment prefers the levy-up motion to pass; this one is rejected.
    conn.execute("""INSERT INTO newspaper_profile(id,publisher_id,editor_id,founded_tick,
                    editorial_line,editorial_basis,credibility)
                    VALUES(1,1,1,0,'working','test',1.0)""")
    conn.execute("""INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,
                    shift_end,work_days) VALUES(1,1,'writer',10000,16,30,62)""")
    event_id = emit(conn, 48, "town_event", a=1, importance=3,
                    text="the council rejected a motion to raise levy_rate",
                    tag="motion_rejected", policy="levy_rate", direction=1,
                    passed=False, value=0.06)
    conn.commit()
    monkeypatch.setattr(newspaper, "FALSE_CLAIM_BASE", 1.0)
    text = newspaper.publish_week(conn, 0, "test")
    claim = conn.execute("SELECT * FROM newspaper_claims WHERE week=0").fetchone()
    assert claim is not None and claim["truth"] == 0
    assert claim["claim"] in text, f"claim={claim['claim']!r} text={text!r}"
    # The world event is unchanged: the council really did reject it.
    actual = conn.execute("SELECT data FROM events WHERE id=?", (event_id,)).fetchone()[0]
    assert '"passed": false' in actual


def test_editorial_line_changes_coverage_not_the_observer_record(conn):
    from miniville.events import emit
    from miniville.newspaper import observer_record
    emit(conn, 48, "town_event", a=1, importance=3, text="a wage floor motion passed",
         tag="motion_passed", policy="min_wage", direction=1, passed=True, value=0.1)
    conn.commit()
    truth = observer_record(conn, 0)
    assert any("wage floor motion passed" in event["text"] for event in truth)
    assert conn.execute("SELECT COUNT(*) FROM newspaper_claims").fetchone()[0] == 0


def test_publish_week_writes_edition(conn):
    from miniville.events import emit
    emit(conn, 48, "life_event", a=1, importance=5,
         text="got married!", tag="marriage")
    emit(conn, 50, "arrival", a=1, importance=3,
         text="moved to Miniville from Springfield", tag="immigration")
    conn.commit()

    text = newspaper.publish_week(conn, 0, "test")
    assert "THE MINIVILLE GAZETTE" in text
    assert "Week 1" in text
    assert "Weddings & Births" in text
    assert "Arrivals" in text
    assert "Population this week" in text
    row = conn.execute("SELECT * FROM newspapers WHERE week=0").fetchone()
    assert row and row["text"] == text


def test_publish_week_is_idempotent(conn, monkeypatch):
    from miniville.events import emit
    conn.execute("""INSERT INTO newspaper_profile(id,publisher_id,editor_id,founded_tick,
                    editorial_line,editorial_basis,credibility)
                    VALUES(1,1,1,0,'working','test',1.0)""")
    emit(conn, 48, "town_event", a=1, importance=3,
         text="the council rejected a motion to raise levy_rate", tag="motion_rejected",
         policy="levy_rate", direction=1, passed=False, value=0.06)
    conn.commit()
    monkeypatch.setattr(newspaper, "FALSE_CLAIM_BASE", 1.0)
    newspaper.publish_week(conn, 0, "test")
    before = conn.execute("SELECT credibility FROM newspaper_profile WHERE id=1").fetchone()[0]
    nclaims = conn.execute("SELECT COUNT(*) FROM newspaper_claims WHERE week=0").fetchone()[0]
    assert nclaims == 1
    text = newspaper.publish_week(conn, 0, "test")
    after = conn.execute("SELECT credibility FROM newspaper_profile WHERE id=1").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) c FROM newspapers").fetchone()["c"] == 1
    assert after == before                      # same false report, no double penalty
    assert text == conn.execute("SELECT text FROM newspapers WHERE week=0").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM newspaper_claims WHERE week=0").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE data LIKE '%press_claim%'").fetchone()[0] == 1
    assert "council's motion" in text


def test_editorial_line_selects_the_agenda_not_the_truth(conn):
    from miniville.events import emit
    motion = emit(conn, 48, "town_event", a=1, importance=3,
                  text="the council lowered the levy", tag="motion_passed",
                  policy="levy_rate", direction=-1, passed=True, value=0.04)
    fired = emit(conn, 48, "life_event", a=1, importance=3,
                 text="lost their job at the grocer", tag="fired")
    conn.commit()
    motion_row, fired_row = conn.execute("SELECT * FROM events WHERE id IN (?,?) ORDER BY id",
                                         (motion, fired)).fetchall()
    assert newspaper.editorial_weight(fired_row, "working") > \
           newspaper.editorial_weight(motion_row, "working")
    assert newspaper.editorial_weight(motion_row, "business") > \
           newspaper.editorial_weight(fired_row, "business")
    # Same two recorded events; the publisher's line changes which leads.
    assert "lost their job" in newspaper._headline(
        conn, [motion_row, fired_row], {"editorial_line": "working"})
    assert "lowered the levy" in newspaper._headline(
        conn, [motion_row, fired_row], {"editorial_line": "business"})
    neutral = newspaper.observer_record(conn, 0)
    assert any("lowered the levy" in e["text"] for e in neutral)
    assert any("lost their job" in e["text"] for e in neutral)


def test_quiet_week_still_publishes(conn):
    text = newspaper.publish_week(conn, 0, "test")
    assert "quiet week" in text.lower()
