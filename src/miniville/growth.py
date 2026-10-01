"""Town growth: immigration from the persona dataset and births.

Miniville starts with a fixed population, but a real town gains people. Two
sources are modelled:

* **Immigration** — unused Nemotron persona rows are pulled from the dataset and
  moved in as fully-formed adults (home, household, job, state). Because the
  dataset is far larger than the town, immigration is effectively unbounded and
  deterministic per (seed, tick, index).
* **Births** — married couples with high romance occasionally have a child, who
  joins the household as a child agent and ages in place.

Both are additive: nothing here removes or rewrites existing residents.
"""
from __future__ import annotations

import json
import sqlite3

from .events import HISTORIC, NOTABLE, emit
from .ingest import _list_field, _name_of, load_persona_rows
from .rng import rng_for
from .world import workplace_tags_for

P_BIRTH = 0.02          # per married couple per day
BIRTH_ROMANCE = 80.0    # couples below this are not trying

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


def _give_job(conn: sqlite3.Connection, agent_id: int, occupation: str, r) -> int | None:
    """Hire a newcomer at the workplace best matching their occupation."""
    occ = occupation or ""
    if "student" in occ.lower() or "retire" in occ.lower():
        return None
    if r.random() < 0.10:      # 10% arrive between jobs
        return None
    tags = workplace_tags_for(occ)
    rows = conn.execute(
        "SELECT id, tags FROM places WHERE kind='workplace'").fetchall()
    scored = sorted(
        ((len(set(json.loads(x["tags"])) & set(tags)), x["id"]) for x in rows),
        key=lambda t: -t[0])
    if not scored:
        return None
    best = scored[0][0]
    place_id = r.choice([pid for s, pid in scored if s == best])
    shift_start = r.choice([12, 14, 16, 18])
    conn.execute(
        "INSERT OR REPLACE INTO jobs(agent_id,place_id,role,wage_cents,"
        "shift_start,shift_end,work_days) VALUES(?,?,?,?,?,?,62)",
        (agent_id, place_id, occ, r.randint(2200, 9000), shift_start,
         min(shift_start + r.randint(14, 18), 44)))
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
    rows = load_persona_rows(dataset_dir, n * 3, seed)
    fresh = [row for row in rows if row.get("uuid") not in known][:n]
    if not fresh:
        return 0

    arrived = 0
    for i, row in enumerate(fresh):
        r = rng_for(seed, "immigrate", tick, i)
        name = _name_of(row, tick + i)
        surname = name.split()[-1]
        home = _free_home(conn, r)
        cur = conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,education_level,
               occupation,origin_city,origin_state,persona,professional_persona,
               hobbies_json,skills_json,traits_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row.get("uuid"), name, row.get("sex"), row.get("age"),
             row.get("marital_status") or "never_married",
             row.get("education_level"), row.get("occupation"),
             row.get("city"), row.get("state"), row.get("persona"),
             row.get("professional_persona"),
             json.dumps(_list_field(row.get("hobbies_and_interests_list"))),
             json.dumps(_list_field(row.get("skills_and_expertise_list"))),
             json.dumps({"background": (row.get("cultural_background") or "")[:300]})))
        aid = int(cur.lastrowid or 0)
        _new_household(conn, [aid], home, surname)
        _give_job(conn, aid, row.get("occupation") or "", r)
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?,?,?)",
            (aid, home, r.randint(200000, 1200000)))
        emit(conn, tick, "arrival", a=aid, importance=NOTABLE,
             text=f"moved to Miniville from {row.get('city') or 'out of town'}",
             tag="immigration")
        arrived += 1
    conn.commit()
    return arrived


def births(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Married couples with high romance may welcome a child."""
    couples = conn.execute(
        """SELECT a_id, b_id, romance FROM relationships
           WHERE label='spouse' AND romance >= ?""", (BIRTH_ROMANCE,)).fetchall()
    n = 0
    for c in couples:
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
               occupation,persona,household_id,home_place_id,is_child)
               VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
            (f"born-{c['a_id']}-{c['b_id']}-{tick}", cname,
             r.choice(["Male", "Female"]), 0, "never_married", "none", "child",
             f"{cname}, born in Miniville.", parent["household_id"],
             parent["home_place_id"]))
        aid = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?,?,0)",
            (aid, parent["home_place_id"]))
        emit(conn, tick, "life_event", a=c["a_id"], b=c["b_id"],
             importance=HISTORIC, text=f"welcomed a baby, {cname}",
             tag="birth")
        n += 1
    conn.commit()
    return n
