"""Population bootstrap: sample Nemotron-Personas-USA -> agents, households,
jobs, homes. Deterministic given world_seed."""
from __future__ import annotations

import glob
import json
import re
import sqlite3

import pyarrow.parquet as pq

from . import economy, jobs
from .rng import rng_for, seed_int
from .world import DISTRICTS, create_world, workplace_tags_for

NAME_RE = re.compile(r"^([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})")

# simple generated names for children & unmatched rows
FIRST = ["Ada", "Ben", "Cora", "Dex", "Elsie", "Finn", "Gwen", "Hugo", "Ivy",
         "Jonah", "Kira", "Lena", "Milo", "Nora", "Otto", "Pia", "Quinn",
         "Rosa", "Sam", "Tess", "Uri", "Vera", "Wade", "Xena", "Yusuf", "Zoe"]
LAST = ["Ashbrook", "Briar", "Calloway", "Delmar", "Ellery", "Frost",
        "Goodwin", "Hale", "Inglewood", "Jessup", "Kestrel", "Lockwood",
        "Marrow", "Nightingale", "Osborne", "Pemberton", "Quill", "Rowan",
        "Sable", "Thorne", "Underhill", "Vale", "Whitlock", "Yarrow"]

MARRIED = {"married", "married_present", "married_spouse_present",
           "married_spouse_absent"}


def _name_of(row: dict, fallback_seed: int) -> str:
    for field in ("persona", "professional_persona"):
        m = NAME_RE.match(row.get(field) or "")
        if m:
            return m.group(1)
    r = rng_for("name", fallback_seed)
    return f"{r.choice(FIRST)} {r.choice(LAST)}"


def _list_field(v) -> list[str]:
    if isinstance(v, str):
        try:
            parsed = json.loads(v.replace("'", '"'))
            return parsed if isinstance(parsed, list) else [v]
        except Exception:
            return [v] if v else []
    return list(v) if v else []


def load_persona_rows(dataset_dir: str, n_target: int, seed: str) -> list[dict]:
    """Reservoir-sample n_target rows across all parquet shards."""
    import random

    files = sorted(glob.glob(f"{dataset_dir}/data/train-*.parquet"))
    if not files:
        raise FileNotFoundError(f"no parquet shards under {dataset_dir}/data")
    rnd = random.Random(seed_int(seed, "reservoir"))
    reservoir: list[dict] = []
    seen = 0
    cols = ["uuid", "persona", "professional_persona", "sex", "age",
            "marital_status", "education_level", "bachelors_field",
            "occupation", "city", "state", "hobbies_and_interests_list",
            "skills_and_expertise_list", "cultural_background"]
    for f in files:
        batch_rows = pq.read_table(f, columns=cols).to_pylist()
        for row in batch_rows:
            seen += 1
            if len(reservoir) < n_target:
                reservoir.append(row)
            else:
                j = rnd.randrange(seen)
                if j < n_target:
                    reservoir[j] = row
        if seen >= n_target * 20 and len(reservoir) >= n_target:
            break  # enough entropy; avoid reading all 1M rows for small towns
    rnd.shuffle(reservoir)
    return reservoir


def _pick_workplace(conn, tags: list[str], r) -> int:
    """Best-matching venue that still has room for another pair of hands.

    Every non-home venue is eligible (the tavern and the theater are employers
    too) and the staffing target keeps the town's workforce spread across them.
    Without the target every unmatched occupation — and the generic fallback
    tags match the town hall best — piled into one venue: the live world ended
    up with 348 of its 435 jobs at Town Hall and none at all at the venues the
    customers actually visit.
    """
    rows = conn.execute(
        """SELECT p.id, p.tags, COUNT(j.agent_id) staff
           FROM places p LEFT JOIN jobs j ON j.place_id = p.id
           WHERE p.kind != 'home' GROUP BY p.id""").fetchall()
    targets = jobs.venue_targets(conn)
    scored = []
    for row in rows:
        ptags = set(json.loads(row["tags"]))
        room = targets.get(row["id"], 0) - row["staff"]
        if room <= 0:
            continue
        scored.append((len(ptags & set(tags)), room, row["id"]))
    if not scored:
        return conn.execute(
            "SELECT id FROM places WHERE kind='workplace' ORDER BY id LIMIT 1"
        ).fetchone()["id"]
    best = max(s for s, _, _ in scored)
    top = [(room, pid) for s, room, pid in scored if s == best]
    most_room = max(room for room, _ in top)
    return r.choice([pid for room, pid in top if room == most_room])


def _pick_leisure_home(district: str, homes: dict[str, list[int]], r) -> int:
    pool = homes.get(district) or [h for hs in homes.values() for h in hs]
    return r.choice(pool)


def populate(conn: sqlite3.Connection, dataset_dir: str, n_agents: int,
             seed: str = "miniville") -> dict[str, int]:
    """Full bootstrap. Returns stats."""
    venue_ids = create_world(conn)
    rows = load_persona_rows(dataset_dir, n_agents, seed)
    r = rng_for(seed, "populate")

    # residential homes: small home places per district
    homes: dict[str, list[int]] = {d: [] for d in DISTRICTS}
    homes_per_district = max(8, n_agents // 15)
    for d in DISTRICTS:
        for i in range(homes_per_district):
            cur = conn.execute(
                "INSERT INTO places(name,kind,district,capacity,tags) VALUES(?,?,?,?,?)",
                (f"{d} Home {i+1}", "home", d, 6, "[]"))
            homes[d].append(cur.lastrowid)

    # --- pass 1: insert adults ---
    married_ids: list[int] = []
    single_ids: list[int] = []
    for i, row in enumerate(rows[:n_agents]):
        name = _name_of(row, i)
        ms = row.get("marital_status") or "never_married"
        traits = {"background": (row.get("cultural_background") or "")[:300]}
        cur = conn.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,education_level,
               occupation,origin_city,origin_state,persona,professional_persona,
               hobbies_json,skills_json,traits_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row.get("uuid"), name, row.get("sex"), row.get("age"), ms,
             row.get("education_level"), row.get("occupation"),
             row.get("city"), row.get("state"),
             row.get("persona"), row.get("professional_persona"),
             json.dumps(_list_field(row.get("hobbies_and_interests_list"))),
             json.dumps(_list_field(row.get("skills_and_expertise_list"))),
             json.dumps(traits)),
        )
        aid = cur.lastrowid
        assert aid is not None
        (married_ids if ms in MARRIED else single_ids).append(aid)

    # --- pass 2: pair married agents into households ---
    pair_r = rng_for(seed, "pairing")
    married_ids.sort(key=lambda a: conn.execute(
        "SELECT age FROM agents WHERE id=?", (a,)).fetchone()["age"])
    taken: set[int] = set()
    couples: list[tuple[int, int]] = []
    pool = married_ids[:]
    for a in married_ids:
        if a in taken:
            continue
        arow = conn.execute("SELECT sex,age FROM agents WHERE id=?", (a,)).fetchone()
        # find nearest-age partner of opposite sex, else any, 24% same-sex ok
        candidates = [b for b in pool if b != a and b not in taken]
        if not candidates:
            break
        opp = [b for b in candidates if conn.execute(
            "SELECT sex FROM agents WHERE id=?", (b,)).fetchone()["sex"] != arow["sex"]]
        same = [b for b in candidates if b not in opp]
        use_same = pair_r.random() < 0.10 or not opp
        group = same if use_same else opp
        b = min(group, key=lambda x: abs(conn.execute(
            "SELECT age FROM agents WHERE id=?", (x,)).fetchone()["age"] - arow["age"]))
        taken.update((a, b))
        couples.append((a, b))

    # --- pass 3: households ---
    n_children = 0
    child_r = rng_for(seed, "children")
    def new_household(members: list[int], district: str, home_id: int) -> int:
        surname = conn.execute("SELECT name FROM agents WHERE id=?",
                               (members[0],)).fetchone()["name"].split()[-1]
        cur = conn.execute(
            "INSERT INTO households(name,home_place_id) VALUES(?,?)",
            (f"{surname} household", home_id))
        hid = cur.lastrowid
        for m in members:
            conn.execute(
                "UPDATE agents SET household_id=?, home_place_id=? WHERE id=?",
                (hid, home_id, m))
        return hid

    couple_ids: set[int] = set()
    for a, b in couples:
        district = r.choice(DISTRICTS)
        home = _pick_leisure_home(district, homes, r)
        hid = new_household([a, b], district, home)
        couple_ids.update((a, b))
        # relationship spouses
        lo, hi = min(a, b), max(a, b)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label,interactions,last_met_tick)
               VALUES(?,?,?,?,?,?,?,0)""",
            (lo, hi, 95, 70 + pair_r.uniform(0, 25), 80 + pair_r.uniform(0, 20), "spouse", 1000))
        # kids: 55% of couples get 1-3 children
        if child_r.random() < 0.55:
            for _ in range(child_r.randint(1, 3)):
                n_children += 1
                cr = rng_for(seed, "child", a, b, n_children)
                cage = cr.randint(0, 17)
                csex = cr.choice(["Male", "Female"])
                cname = f"{cr.choice(FIRST)} {conn.execute('SELECT name FROM agents WHERE id=?', (a,)).fetchone()['name'].split()[-1]}"
                conn.execute(
                    """INSERT INTO agents(uuid,name,sex,age,marital_status,education_level,
                       occupation,persona,household_id,home_place_id,is_child)
                       VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
                    (f"gen-{a}-{b}-{n_children}", cname, csex, cage, "never_married",
                     "in_school" if cage >= 5 else "none", "student",
                     f"{cname}, a child of Miniville.", hid, home))

    # single adults
    singles = [a for a in (married_ids + single_ids) if a not in couple_ids]
    for a in singles:
        district = r.choice(DISTRICTS)
        home = _pick_leisure_home(district, homes, r)
        new_household([a], district, home)

    # --- pass 4: jobs ---
    adults = conn.execute("SELECT id, occupation FROM agents WHERE is_child=0").fetchall()
    n_employed = 0
    for row in adults:
        occ = row["occupation"] or ""
        if "student" in occ.lower() or "retire" in occ.lower():
            continue
        if r.random() < 0.08:  # 8% unemployed
            continue
        tags = workplace_tags_for(occ)
        wid = _pick_workplace(conn, tags, r)
        shift_r = rng_for(seed, "shift", row["id"])
        shift_start = shift_r.choice([12, 14, 16, 18])
        shift_len = shift_r.randint(14, 18)
        wage = shift_r.randint(economy.WAGE_MIN_CENTS, economy.WAGE_MAX_CENTS)
        conn.execute(
            "INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,"
            "work_days,started_tick,rank) VALUES(?,?,?,?,?,?,?,?,0)",
            (row["id"], wid, occ, wage, shift_start, min(shift_start + shift_len, 44),
             62, 0))
        conn.execute("UPDATE agents SET work_place_id=? WHERE id=?", (wid, row["id"]))
        n_employed += 1

    # --- pass 5: initial agent_state ---
    for row in conn.execute("SELECT id, home_place_id FROM agents").fetchall():
        conn.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?,?,?)",
            (row["id"], row["home_place_id"],
             r.randint(economy.STARTING_MONEY_MIN, economy.STARTING_MONEY_MAX)))

    conn.commit()
    stats = {
        "agents": conn.execute("SELECT COUNT(*) c FROM agents").fetchone()["c"],
        "children": n_children,
        "households": conn.execute("SELECT COUNT(*) c FROM households").fetchone()["c"],
        "employed": n_employed,
        "places": conn.execute("SELECT COUNT(*) c FROM places").fetchone()["c"],
    }
    return stats
