"""Town growth: immigration from the persona dataset and births.

Miniville starts with a fixed population, but a real town gains people. Two
sources are modelled:

* **Immigration** — unused Nemotron persona rows are pulled from the dataset and
  moved in as fully-formed adults (home, household, job, state). Because the
  dataset is far larger than the town, immigration is effectively unbounded and
  deterministic per (seed, tick, index).
* **Births** — fertile married couples with high romance have about a 10%
  annual chance of a child, with at least a year between births per household.

Both are additive: nothing here removes or rewrites existing residents.
"""
from __future__ import annotations

import json
import sqlite3

from . import economy
from .db import get_meta
from .events import HISTORIC, MAJOR, NOTABLE, emit
from .ingest import _list_field, _name_of, load_persona_rows
from .rng import rng_for
from .timekeeper import day_of
from .world import workplace_tags_for

ANNUAL_BIRTH_RATE = 0.10
P_BIRTH = 1 - (1 - ANNUAL_BIRTH_RATE) ** (1 / 365)
BIRTH_ROMANCE = 80.0    # couples below this are not trying
FERTILE_AGES = (18, 44)
BIRTH_SPACING_DAYS = 365

_CHILD_FIRST = ["Ada", "Ben", "Cora", "Dex", "Elsie", "Finn", "Gwen", "Hugo",
                "Ivy", "Jonah", "Kira", "Lena", "Milo", "Nora", "Otto", "Pia",
                "Quinn", "Rosa", "Sam", "Tess", "Uri", "Vera", "Wade", "Xena",
                "Yusuf", "Zoe"]


def _free_home(conn: sqlite3.Connection, r) -> int:
    """Pick a home place, preferring ones with room left."""
    rows = conn.execute(
        """SELECT p.id, p.capacity, COUNT(a.id) AS occupants
           FROM places p LEFT JOIN agents a
             ON a.home_place_id = p.id AND a.alive = 1
           WHERE p.kind = 'home'
           GROUP BY p.id ORDER BY (COUNT(a.id) * 1.0 / p.capacity) ASC""").fetchall()
    if not rows:
        raise RuntimeError("no home places — run init first")
    # bias toward the emptiest third, then choose deterministically
    pool = rows[: max(1, len(rows) // 3)]
    return r.choice(pool)["id"]


def _new_household(conn: sqlite3.Connection, members: list[int],
                   home_id: int, surname: str) -> int:
    cur = conn.execute(
        "INSERT INTO households(name,home_place_id) VALUES(?,?)",
        (f"{surname} household", home_id))
    hid = int(cur.lastrowid or 0)
    for m in members:
        conn.execute(
            "UPDATE agents SET household_id=?, home_place_id=? WHERE id=?",
            (hid, home_id, m))
    return hid


def _give_job(conn: sqlite3.Connection, agent_id: int, occupation: str, r,
              room: dict[int, int] | None = None) -> int | None:
    """Hire a newcomer at the workplace best matching their occupation.

    `room` is the venue vacancy map (place_id -> open posts). Hiring through
    the same targets the rest of the labour market uses stops immigration
    from overfilling venues and pushing the town past full employment.
    """
    occ = occupation or ""
    if "student" in occ.lower() or "retire" in occ.lower():
        return None
    row = conn.execute("SELECT age FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row and row["age"] is not None and int(row["age"]) >= 65:
        return None            # newcomers past retirement age do not take posts
    if r.random() < 0.10:      # 10% arrive between jobs
        return None
    tags = workplace_tags_for(occ)
    rows = economy.open_workplaces(conn)
    scored = sorted(
        ((len(set(json.loads(x["tags"])) & set(tags)), x["id"]) for x in rows
         if room is None or room.get(x["id"], 0) > 0),
        key=lambda t: -t[0])
    if not scored:
        return None
    best = scored[0][0]
    place_id = r.choice([pid for s, pid in scored if s == best])
    if room is not None:
        room[place_id] -= 1
    shift_start = r.choice([12, 14, 16, 18])
    wage = r.randint(economy.WAGE_MIN_CENTS, economy.WAGE_MAX_CENTS)
    conn.execute(
        "INSERT OR REPLACE INTO jobs(agent_id,place_id,role,wage_cents,"
        "shift_start,shift_end,work_days,started_tick,rank,base_wage_cents) "
        "VALUES(?,?,?,?,?,?,62,?,0,?)",
        (agent_id, place_id, occ, wage, shift_start,
         min(shift_start + r.randint(14, 18), 44),
         int(get_meta(conn, "tick", "0") or 0), wage))
    conn.execute("UPDATE agents SET work_place_id=? WHERE id=?",
                 (place_id, agent_id))
    return place_id


def immigrate(conn: sqlite3.Connection, n: int, tick: int, seed: str,
              dataset_dir: str = r"E:\Nemotron-Personas-USA") -> int:
    """Move `n` unused dataset personas into town. Returns how many arrived."""
    if n <= 0:
        return 0
    known = {r["uuid"] for r in conn.execute(
        "SELECT uuid FROM agents WHERE uuid IS NOT NULL").fetchall()}
    # over-fetch: the dataset sample may overlap residents already in town
    rows = load_persona_rows(dataset_dir, n * 3, f"{seed}:immigrate:{tick}")
    fresh = [row for row in rows if row.get("uuid") not in known][:n]
    if not fresh:
        return 0

    from .jobs import vacancies
    room = dict(vacancies(conn))       # newcomers only take posts that exist
    arrived = 0
    for i, row in enumerate(fresh):
        r = rng_for(seed, "immigrate", tick, i)
        name = _name_of(row, tick + i)
        surname = name.split()[-1]
        home = _free_home(conn, r)
        # the persona dataset includes minors. They used to be inserted with
        # the is_child default of 0, so a child arrived as a job-holding head
        # of household — 115 of them in the live world
        try:
            age = int(row.get("age"))
        except (TypeError, ValueError):
            age = 30
        is_child = 1 if age < 18 else 0
        cur = conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,
               education_level,occupation,origin_city,origin_state,persona,
               professional_persona,hobbies_json,skills_json,traits_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row.get("uuid"), name, row.get("sex"), age, is_child,
             "never_married" if is_child else (row.get("marital_status") or "never_married"),
             row.get("education_level"), row.get("occupation"),
             row.get("city"), row.get("state"), row.get("persona"),
             row.get("professional_persona"),
             json.dumps(_list_field(row.get("hobbies_and_interests_list"))),
             json.dumps(_list_field(row.get("skills_and_expertise_list"))),
             json.dumps({"background": (row.get("cultural_background") or "")[:300]})))
        aid = int(cur.lastrowid or 0)
        if is_child:
            # a child arrives into a household that has room, not their own
            guardian = _household_with_room(conn, home)
            if guardian:
                conn.execute("UPDATE agents SET household_id=? WHERE id=?",
                             (guardian, aid))
            else:
                _new_household(conn, [aid], home, surname)
        else:
            _new_household(conn, [aid], home, surname)
            _give_job(conn, aid, row.get("occupation") or "", r, room)
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?,?,?)",
            (aid, home, r.randint(economy.STARTING_MONEY_MIN,
                                  economy.STARTING_MONEY_MAX)))
        emit(conn, tick, "arrival", a=aid, importance=NOTABLE,
             text=f"moved to Miniville from {row.get('city') or 'out of town'}",
             tag="immigration")
        arrived += 1
    conn.commit()
    return arrived


def _household_with_room(conn: sqlite3.Connection, home_place_id: int) -> int | None:
    """An existing household in the same home that is under its capacity."""
    row = conn.execute(
        """SELECT h.id, COUNT(a.id) n FROM households h
           LEFT JOIN agents a ON a.household_id = h.id AND a.alive = 1
           WHERE h.home_place_id = ?
           GROUP BY h.id HAVING n < 6 ORDER BY n DESC, h.id LIMIT 1""",
        (home_place_id,)).fetchone()
    return row["id"] if row else None


def births(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Fertile married couples may welcome a child, spaced a year apart."""
    couples = conn.execute(
        """SELECT r.a_id, r.b_id, r.romance,
                  a.age AS a_age, a.sex AS a_sex, a.household_id AS household_id,
                  b.age AS b_age, b.sex AS b_sex
           FROM relationships r
           JOIN agents a ON a.id=r.a_id
           JOIN agents b ON b.id=r.b_id
           WHERE r.label='spouse' AND r.romance >= ?
             AND a.alive=1 AND b.alive=1
             AND a.is_child=0 AND b.is_child=0""",
        (BIRTH_ROMANCE,)).fetchall()
    n = 0
    for c in couples:
        if c["a_age"] is None or c["b_age"] is None:
            continue
        a_sex = (c["a_sex"] or "").casefold()
        b_sex = (c["b_sex"] or "").casefold()
        if a_sex == "female" and b_sex == "male":
            fertile_age = c["a_age"]
        elif a_sex == "male" and b_sex == "female":
            fertile_age = c["b_age"]
        else:
            fertile_age = min(c["a_age"], c["b_age"])
        if not FERTILE_AGES[0] <= fertile_age <= FERTILE_AGES[1]:
            continue
        if conn.execute(
                """SELECT 1 FROM agents WHERE alive=1 AND household_id=?
                   AND birth_day IS NOT NULL AND birth_day>? LIMIT 1""",
                (c["household_id"], day_of(tick) - BIRTH_SPACING_DAYS)).fetchone():
            continue
        r = rng_for(seed, "birth", c["a_id"], c["b_id"], tick)
        if r.random() >= P_BIRTH:
            continue
        parent = conn.execute(
            "SELECT household_id, home_place_id, name FROM agents WHERE id=?",
            (c["a_id"],)).fetchone()
        if not parent or parent["household_id"] is None:
            continue
        surname = (parent["name"] or "Miniville").split()[-1]
        cname = f"{r.choice(_CHILD_FIRST)} {surname}"
        cur = conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,education_level,
               occupation,persona,household_id,home_place_id,is_child,birth_day)
               VALUES(?,?,?,?,?,?,?,?,?,?,1,?)""",
            (f"born-{c['a_id']}-{c['b_id']}-{tick}", cname,
             r.choice(["Male", "Female"]), 0, "never_married", "none", "child",
             f"{cname}, born in Miniville.", parent["household_id"],
             parent["home_place_id"], day_of(tick)))
        aid = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?,?,0)",
            (aid, parent["home_place_id"]))
        # MAJOR, not HISTORIC: a birth matters enormously to a family and very
        # little to the town. At importance 5 every quiet week headlined a
        # baby and the year in review was a list of them.
        emit(conn, tick, "life_event", a=c["a_id"], b=c["b_id"],
             importance=MAJOR, text=f"welcomed a baby, {cname}",
             tag="birth")
        n += 1
    conn.commit()
    return n
