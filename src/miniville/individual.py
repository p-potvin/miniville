"""Inhabitant individualism: persona-derived temperaments, chronotypes, habits,
and situated personal happenings.

Miniville's early strata moved residents as a crowd: everyone decayed at the
same rate, woke and slept on the same tick, chose leisure venues uniformly at
random, and experienced town events (`TOWN_EVENTS`) as disembodied headlines
that happened to nobody in particular. Even the daily lottery rolled flat,
context-blind probabilities at midnight.

This module gives each resident an individual profile and situated personal
events — with zero per-tick LLM calls so the simulation stays deterministic and
runs in a few milliseconds on a daily-driver workstation (30 GB RAM / 12 GB
VRAM):

* **Temperament & Chronotype** — deterministically derived from the resident's
  own `persona`, `professional_persona`, `hobbies_json`, `skills_json` (which
  was previously unused), and `occupation`, then cached in memory per SQLite
  connection. Five axes (`sociability`, `industriousness`, `curiosity`,
  `resilience`, `thrift` in [-1, 1]), a `chronotype` (`early_bird`, `morning`,
  `day_walker`, `evening`, `night_owl` with wake/sleep tick offsets), and a
  signature `knack`.
* **Situated Personal Happenings (`tick_happenings` & `daily_happenings`)** —
  events happen *to* specific individuals based on where they are, what they
  are doing, and who they are with: workplace craft breakthroughs (with a
  conserved spot bonus from the till), burnout mishaps, hobby triumphs at a
  venue, household DIY fixes vs. hardware repair bills, spontaneous good turns
  between two co-present neighbours, household/workplace illness contagion, and
  empathic caregiving by a spouse or close friend.
* **Habits & Favorite Places** — positive experiences anchor a resident's
  `favorite_place_id` (their "third place"), while negative encounters and
  rivalries steer them away from venues where a rival works.
* **Operator Fate (`inject_fate`)** — the individual counterpart to `shocks.py`:
  trigger a personal event (`breakthrough`, `mishap`, `triumph`, `repair`,
  `care`, `epiphany`) on a specific named resident.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import get_meta
from .events import MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY, day_of, tick_of_day

_PROFILE_CACHE: dict[tuple[int, int], dict[str, Any]] = {}

P_PERSONAL_TICK = 0.045          # ~1.5 situated personal events per day across town
SPOT_BONUS_MIN = 1_500           # $15 spot bonus from a profitable employer's till
SPOT_BONUS_MAX = 4_000           # $40 max spot bonus (strictly conserved)
EMPLOYER_BONUS_CUSHION = 50_000  # employer till must hold at least $500 to pay a bonus
REPAIR_BILL_MIN = 1_800          # $18 hardware/repair bill paid to a local trades shop
REPAIR_BILL_MAX = 4_200          # $42 max repair bill (strictly conserved)
P_CONTAGION_HOME = 0.035         # daily chance of catching a housemate's illness
P_CONTAGION_WORK = 0.015         # daily chance of catching a coworker's illness
P_CAREGIVE = 0.35                # chance a close tie tends to a sick resident today

FATE_KINDS = ("breakthrough", "mishap", "triumph", "repair", "care", "epiphany")

_AXIS_KEYWORDS: dict[str, tuple[list[str], list[str]]] = {
    "sociability": (
        ["outgoing", "social", "community", "choir", "volunteer", "host",
         "party", "talkative", "team", "extrovert", "club", "teaching",
         "customer service", "communication", "bartend", "waiter", "waitress"],
        ["quiet", "solitude", "introvert", "alone", "private", "reading",
         "research", "book", "solo", "birdwatching", "chess", "poetry",
         "independent", "reserved"],
    ),
    "industriousness": (
        ["ambitious", "driven", "hardworking", "dedicated", "meticulous",
         "organization", "lead", "manager", "entrepreneur", "business",
         "build", "discipline", "career", "carpentry", "electrician"],
        ["relaxed", "easygoing", "casual", "unhurried", "leisure", "laid-back",
         "gentle", "wandering", "daydream"],
    ),
    "curiosity": (
        ["curious", "explore", "travel", "learn", "experiment", "art",
         "science", "photography", "painting", "music", "invent", "discover",
         "writing", "design", "hiking", "birdwatching"],
        ["routine", "traditional", "steady", "predictable", "habit",
         "conventional", "familiar"],
    ),
    "resilience": (
        ["resilient", "calm", "steady", "patient", "endurance", "running",
         "cycling", "athletic", "outdoors", "nurse", "physician", "police",
         "paramedic", "veteran", "repair", "construction"],
        ["anxious", "sensitive", "worry", "nervous", "frail", "fragile",
         "overwhelmed", "restless"],
    ),
    "thrift": (
        ["frugal", "budget", "budgeting", "bookkeeping", "accountant",
         "save", "practical", "repair work", "diy", "gardening", "knitting",
         "sewing", "careful"],
        ["generous", "luxury", "fashion", "collect", "dining", "nightlife",
         "shopping", "spend", "lavish"],
    ),
}

_EARLY_KEYWORDS = ["morning", "dawn", "early", "baker", "baking", "running",
                   "birdwatching", "fishing", "gardening", "construction",
                   "elementary"]
_NIGHT_KEYWORDS = ["night", "late", "bartender", "music", "guitar", "theater",
                   "writer", "poetry", "gaming", "nightlife", "brew"]

_DIY_KEYWORDS = {"repair", "repair work", "carpentry", "woodworking", "diy",
                 "electrician", "mechanic", "plumber", "maintenance", "crafts",
                 "construction"}


def clear_cache(conn: sqlite3.Connection | None = None) -> None:
    """Clear cached profiles (for a connection or globally)."""
    if conn is None:
        _PROFILE_CACHE.clear()
        return
    cid = id(conn)
    for k in [k for k in _PROFILE_CACHE if k[0] == cid]:
        _PROFILE_CACHE.pop(k, None)


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(x) for x in val] if isinstance(val, list) else []
    except Exception:
        return []


def _parse_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return dict(val) if isinstance(val, dict) else {}
    except Exception:
        return {}


def _archetype_label(axes: dict[str, float], chronotype: str) -> str:
    top_axis = max(axes.items(), key=lambda kv: abs(kv[1]))
    name, val = top_axis
    adj = {
        ("sociability", True): "gregarious",
        ("sociability", False): "solitary",
        ("industriousness", True): "industrious",
        ("industriousness", False): "easygoing",
        ("curiosity", True): "curious",
        ("curiosity", False): "steady",
        ("resilience", True): "stoic",
        ("resilience", False): "sensitive",
        ("thrift", True): "frugal",
        ("thrift", False): "free-spending",
    }.get((name, val >= 0), "balanced")
    noun = {
        "early_bird": "early bird",
        "morning": "morning riser",
        "day_walker": "regular",
        "evening": "evening stroller",
        "night_owl": "night owl",
    }.get(chronotype, "resident")
    return f"{adj} {noun}"


def derive_profile(agent: sqlite3.Row | dict, seed: str = "miniville") -> dict[str, Any]:
    """Deterministically derive a resident's temperament, chronotype, and knack."""
    aid = int(agent["id"])
    r = rng_for(seed, "temperament", aid)
    keys = agent.keys() if hasattr(agent, "keys") else ()
    skills = _parse_json_list(agent["skills_json"] if "skills_json" in keys else None)
    hobbies = _parse_json_list(agent["hobbies_json"] if "hobbies_json" in keys else None)
    occ = (agent["occupation"] or "").replace("_", " ")
    blob = " ".join([
        agent["persona"] or "",
        (agent["professional_persona"] if "professional_persona" in keys else "") or "",
        " ".join(skills),
        " ".join(hobbies),
        occ,
    ]).lower()

    axes: dict[str, float] = {}
    for axis, (pos_kws, neg_kws) in _AXIS_KEYWORDS.items():
        pos = sum(1 for kw in pos_kws if kw in blob)
        neg = sum(1 for kw in neg_kws if kw in blob)
        jitter = r.uniform(-0.35, 0.35)
        raw = (pos - neg) * 0.28 + jitter
        axes[axis] = round(max(-1.0, min(1.0, raw)), 2)

    early_hits = sum(1 for kw in _EARLY_KEYWORDS if kw in blob)
    night_hits = sum(1 for kw in _NIGHT_KEYWORDS if kw in blob)
    chrono_score = (night_hits - early_hits) + r.uniform(-1.1, 1.1)
    if chrono_score <= -1.0:
        chronotype, wake_off, sleep_off = "early_bird", -2, -2
    elif chrono_score <= -0.35:
        chronotype, wake_off, sleep_off = "morning", -1, -1
    elif chrono_score >= 1.0:
        chronotype, wake_off, sleep_off = "night_owl", 2, 2
    elif chrono_score >= 0.35:
        chronotype, wake_off, sleep_off = "evening", 1, 1
    else:
        chronotype, wake_off, sleep_off = "day_walker", 0, 0

    if skills:
        knack = skills[0].lower()
    elif hobbies:
        knack = hobbies[0].lower()
    elif occ and occ.lower() not in ("student", "child", "retired", "not in workforce"):
        knack = occ.lower()
    else:
        knack = r.choice(["storytelling", "gardening", "home repair", "baking", "whittling"])

    return {
        **axes,
        "chronotype": chronotype,
        "wake_offset": wake_off,
        "sleep_offset": sleep_off,
        "knack": knack,
        "archetype": _archetype_label(axes, chronotype),
    }


def ensure_profiles(conn: sqlite3.Connection, seed: str = "miniville") -> int:
    """Populate individual temperament profiles on any agent missing one."""
    rows = conn.execute(
        """SELECT id, persona, professional_persona, occupation,
                  hobbies_json, skills_json, traits_json
           FROM agents WHERE alive=1"""
    ).fetchall()
    updated = 0
    updates = []
    cid = id(conn)
    for row in rows:
        traits = _parse_json_dict(row["traits_json"])
        if "chronotype" not in traits:
            prof = derive_profile(row, seed)
            traits.update(prof)
            updates.append((json.dumps(traits), row["id"]))
            updated += 1
        _PROFILE_CACHE[(cid, int(row["id"]))] = traits
    if updates:
        conn.executemany("UPDATE agents SET traits_json=? WHERE id=?", updates)
    return updated


def profile_of(conn: sqlite3.Connection, agent_id: int,
               seed: str = "miniville") -> dict[str, Any]:
    """Return a resident's temperament dict, computing and caching on miss."""
    key = (id(conn), int(agent_id))
    cached = _PROFILE_CACHE.get(key)
    if cached is not None:
        return cached
    row = conn.execute(
        """SELECT id, persona, professional_persona, occupation,
                  hobbies_json, skills_json, traits_json
           FROM agents WHERE id=?""",
        (agent_id,),
    ).fetchone()
    if not row:
        return {
            "sociability": 0.0, "industriousness": 0.0, "curiosity": 0.0,
            "resilience": 0.0, "thrift": 0.0, "chronotype": "day_walker",
            "wake_offset": 0, "sleep_offset": 0, "knack": "helping out",
            "archetype": "balanced regular",
        }
    traits = _parse_json_dict(row["traits_json"])
    if "chronotype" not in traits:
        traits.update(derive_profile(row, seed))
    _PROFILE_CACHE[key] = traits
    return traits


def _has_diy_knack(prof: dict[str, Any], hobbies_json: str | None,
                   skills_json: str | None) -> bool:
    if prof.get("knack") in _DIY_KEYWORDS or prof.get("industriousness", 0.0) >= 0.45:
        return True
    items = {x.lower() for x in (_parse_json_list(hobbies_json) + _parse_json_list(skills_json))}
    return bool(items & _DIY_KEYWORDS)


def _open_trades_venue(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT p.id, p.name FROM places p
           JOIN businesses b ON b.place_id = p.id
           WHERE b.status = 'open' AND p.tags LIKE '%"trades"%'
           ORDER BY p.id LIMIT 1"""
    ).fetchone()


def _trigger_work_event(conn: sqlite3.Connection, row: sqlite3.Row,
                        prof: dict[str, Any], tick: int, r) -> bool:
    """Situated workplace event: craft breakthrough or burnout strain."""
    aid = int(row["agent_id"])
    pid = row["place_id"]
    if pid is None:
        return False
    venue = conn.execute(
        """SELECT p.name, COALESCE(b.balance_cents, 0) AS balance_cents,
                  COALESCE(b.status, 'open') AS status
           FROM places p LEFT JOIN businesses b ON b.place_id = p.id
           WHERE p.id = ?""",
        (pid,),
    ).fetchone()
    if not venue or venue["status"] == "closed":
        return False
    vname = venue["name"]
    knack = prof.get("knack") or "their trade"

    resilience = float(prof.get("resilience", 0.0))
    if (row["energy"] < 26 or row["stress"] > 72) and r.random() < (0.45 - 0.15 * resilience):
        conn.execute(
            """UPDATE agent_state
               SET stress = MIN(100, stress + 10), fun = MAX(0, fun - 6)
               WHERE agent_id = ?""",
            (aid,),
        )
        if row["energy"] < 20:
            conn.execute(
                """INSERT INTO conditions(agent_id, kind, until_tick)
                   VALUES(?, 'sick', ?)
                   ON CONFLICT(agent_id, kind) DO UPDATE
                   SET until_tick = MAX(until_tick, excluded.until_tick)""",
                (aid, tick + TICKS_PER_DAY),
            )
        text = f"pulled a muscle and burned out mid-shift at {vname}"
        emit(conn, tick, "life_event", place_id=pid, a=aid, importance=MINOR,
             text=text, tag="work_mishap")
        remember(conn, aid, tick, f"I overdid it on shift at {vname}.",
                 kind="personal", importance=3)
        return True

    bonus = 0
    if int(venue["balance_cents"]) >= EMPLOYER_BONUS_CUSHION:
        bonus = r.randint(SPOT_BONUS_MIN, SPOT_BONUS_MAX)
        conn.execute(
            "UPDATE businesses SET balance_cents = balance_cents - ? WHERE place_id = ?",
            (bonus, pid),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (bonus, aid),
        )
    conn.execute(
        """UPDATE agent_state
           SET fun = MIN(100, fun + 8), stress = MAX(0, stress - 8)
           WHERE agent_id = ?""",
        (aid,),
    )
    bonus_txt = f", earning a ${bonus / 100:,.0f} spot bonus" if bonus else ""
    text = f"put their knack for {knack} to work at {vname}{bonus_txt}"
    emit(conn, tick, "life_event", place_id=pid, a=aid, importance=MINOR,
         text=text, tag="craft_breakthrough", bonus_cents=bonus, knack=knack)
    remember(conn, aid, tick,
             f"My {knack} really shone at {vname} today{bonus_txt}.",
             kind="personal", importance=3)
    return True


def _trigger_leisure_event(conn: sqlite3.Connection, row: sqlite3.Row,
                           prof: dict[str, Any], tick: int, r) -> bool:
    """Situated venue happening: dyadic good turn or personal hobby triumph."""
    aid = int(row["agent_id"])
    pid = row["place_id"]
    if pid is None:
        return False
    venue = conn.execute(
        "SELECT id, name, kind FROM places WHERE id=?", (pid,)
    ).fetchone()
    if not venue or venue["kind"] == "home":
        return False
    vname = venue["name"]

    others = conn.execute(
        """SELECT s.agent_id, a.name FROM agent_state s
           JOIN agents a ON a.id = s.agent_id
           WHERE s.place_id = ? AND s.agent_id != ? AND a.alive = 1
             AND s.activity != 'sleep'
           ORDER BY s.agent_id""",
        (pid, aid),
    ).fetchall()
    if others and r.random() < 0.45:
        other = r.choice(others)
        bid = int(other["agent_id"])
        lo, hi = min(aid, bid), max(aid, bid)
        deeds = [
            f"returned a lost wallet to {other['name']} at {vname}",
            f"helped {other['name']} out of a tight spot at {vname}",
            f"shared their umbrella and a warm laugh with {other['name']} at {vname}",
            f"pitched in when {other['name']} dropped their things at {vname}",
        ]
        deed = r.choice(deeds)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,4.0,6.0,0.0,'acquaintance',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.0),
                 affinity = MIN(100.0, relationships.affinity + 6.0),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        conn.execute(
            """UPDATE agent_state
               SET social = MIN(100, social + 8), stress = MAX(0, stress - 5),
                   fun = MIN(100, fun + 5)
               WHERE agent_id IN (?, ?)""",
            (aid, bid),
        )
        emit(conn, tick, "life_event", place_id=pid, a=aid, b=bid,
             importance=MINOR, text=deed, tag="good_turn")
        remember(conn, aid, tick, f"I {deed}.", kind="personal", importance=3)
        remember(conn, bid, tick, f"{row['name']} helped me out at {vname}.",
                 kind="personal", importance=3)
        return True

    hobbies = _parse_json_list(row["hobbies_json"])
    passion = r.choice(hobbies) if hobbies else prof.get("knack", "their hobby")
    triumphs = [
        f"had a personal breakthrough with {passion} at {vname}",
        f"spent a deeply rewarding hour on {passion} at {vname}",
        f"turned heads with their {passion} at {vname}",
    ]
    text = r.choice(triumphs)
    conn.execute(
        """UPDATE agent_state
           SET fun = MIN(100, fun + 16), stress = MAX(0, stress - 10),
               energy = MIN(100, energy + 4)
           WHERE agent_id = ?""",
        (aid,),
    )
    conn.execute(
        "UPDATE agents SET favorite_place_id = ? WHERE id = ?", (pid, aid)
    )
    emit(conn, tick, "life_event", place_id=pid, a=aid, importance=MINOR,
         text=text, tag="hobby_triumph", passion=passion)
    remember(conn, aid, tick, f"Had a wonderful time with {passion} at {vname}.",
             kind="personal", importance=3)
    return True


def _trigger_home_event(conn: sqlite3.Connection, row: sqlite3.Row,
                        prof: dict[str, Any], tick: int, r) -> bool:
    """Situated domestic incident: DIY fix or conserved hardware repair bill."""
    aid = int(row["agent_id"])
    pid = row["place_id"]
    snags = ["a leaky kitchen pipe", "a sticking front door", "a balky water heater",
             "a loose porch step", "a blown fuse box"]
    snag = r.choice(snags)

    if _has_diy_knack(prof, row["hobbies_json"], row["skills_json"]):
        conn.execute(
            """UPDATE agent_state
               SET fun = MIN(100, fun + 8), stress = MAX(0, stress - 6)
               WHERE agent_id = ?""",
            (aid,),
        )
        text = f"fixed {snag} at home with their own tools"
        emit(conn, tick, "life_event", place_id=pid, a=aid, importance=MINOR,
             text=text, tag="diy_fix")
        remember(conn, aid, tick, f"Fixed {snag} myself at home.",
                 kind="personal", importance=2)
        return True

    shop = _open_trades_venue(conn)
    if shop and int(row["money_cents"]) >= 5_000:
        cost = r.randint(REPAIR_BILL_MIN, REPAIR_BILL_MAX)
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ?, "
            "stress = MIN(100, stress + 5) WHERE agent_id = ?",
            (cost, aid),
        )
        conn.execute(
            """UPDATE businesses
               SET revenue_today = revenue_today + ?,
                   revenue_total = revenue_total + ?,
                   traffic_today = traffic_today + 1
               WHERE place_id = ?""",
            (cost, cost, shop["id"]),
        )
        text = f"had to pay {shop['name']} ${cost / 100:,.0f} to fix {snag}"
        emit(conn, tick, "life_event", place_id=shop["id"], a=aid,
             importance=MINOR, text=text, tag="repair_bill", cost_cents=cost)
        remember(conn, aid, tick,
                 f"Paid {shop['name']} ${cost / 100:,.0f} to repair {snag}.",
                 kind="personal", importance=2)
        return True
    return False


def tick_happenings(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Evaluate situated individual happenings for the current tick."""
    tod = tick_of_day(tick)
    if tod < 12 or tod > 44:
        return 0
    r = rng_for(seed, "ind_tick", tick)
    if r.random() >= P_PERSONAL_TICK:
        return 0

    candidates = conn.execute(
        """SELECT s.agent_id, s.place_id, s.activity, s.energy, s.stress,
                  s.fun, s.money_cents, a.name, a.hobbies_json, a.skills_json
           FROM agent_state s JOIN agents a ON a.id = s.agent_id
           WHERE a.alive = 1 AND a.is_child = 0
             AND s.activity IN ('work', 'leisure', 'gathering', 'eat_out',
                                'shopping', 'home')
           ORDER BY s.agent_id"""
    ).fetchall()
    if not candidates:
        return 0

    chosen = r.choice(candidates)
    prof = profile_of(conn, chosen["agent_id"], seed)
    act = chosen["activity"]
    fired = False
    if act == "work":
        fired = _trigger_work_event(conn, chosen, prof, tick, r)
    elif act in ("leisure", "gathering", "eat_out", "shopping"):
        fired = _trigger_leisure_event(conn, chosen, prof, tick, r)
    elif act == "home":
        fired = _trigger_home_event(conn, chosen, prof, tick, r)
    return 1 if fired else 0


def daily_happenings(conn: sqlite3.Connection, tick: int, seed: str) -> dict[str, int]:
    """Daily pass for individualized contagion, caregiving, and epiphanies."""
    day = day_of(tick)
    ensure_profiles(conn, seed)
    r = rng_for(seed, "ind_daily", day)
    contagion_n = caregive_n = epiphany_n = 0

    sick_rows = conn.execute(
        """SELECT c.agent_id, c.until_tick, a.name, a.household_id, a.work_place_id
           FROM conditions c JOIN agents a ON a.id = c.agent_id
           WHERE c.kind = 'sick' AND a.alive = 1
           ORDER BY c.agent_id"""
    ).fetchall()
    already_sick = {row["agent_id"] for row in sick_rows}

    for srow in sick_rows:
        sid = int(srow["agent_id"])
        if srow["household_id"] is not None:
            mates = conn.execute(
                """SELECT a.id, a.name, st.energy FROM agents a
                   JOIN agent_state st ON st.agent_id = a.id
                   WHERE a.household_id = ? AND a.id != ? AND a.alive = 1
                     AND a.is_child = 0 ORDER BY a.id""",
                (srow["household_id"], sid),
            ).fetchall()
            for m in mates:
                mid = int(m["id"])
                if mid in already_sick:
                    continue
                mprof = profile_of(conn, mid, seed)
                p = P_CONTAGION_HOME * (1.0 - 0.3 * float(mprof.get("resilience", 0.0)))
                if m["energy"] < 35:
                    p *= 1.35
                if r.random() < p:
                    days = r.randint(1, 2)
                    conn.execute(
                        """INSERT OR REPLACE INTO conditions(agent_id, kind, until_tick)
                           VALUES(?, 'sick', ?)""",
                        (mid, tick + days * TICKS_PER_DAY),
                    )
                    already_sick.add(mid)
                    emit(conn, tick, "life_event", a=mid, b=sid, importance=MINOR,
                         text=f"caught a bug going around the household from {srow['name']}",
                         tag="sick")
                    contagion_n += 1

        carers = conn.execute(
            """SELECT CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END AS cid,
                      a.name AS cname
               FROM relationships r
               JOIN agents a ON a.id = (CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END)
               WHERE (r.a_id = ? OR r.b_id = ?)
                 AND a.alive = 1 AND a.is_child = 0
                 AND (r.label IN ('spouse', 'partner', 'close_friend')
                      OR (r.familiarity >= 30 AND r.affinity >= 25))
               ORDER BY r.affinity DESC, cid LIMIT 3""",
            (sid, sid, sid, sid),
        ).fetchall()
        healthy_carers = [c for c in carers if int(c["cid"]) not in already_sick]
        if healthy_carers and r.random() < P_CAREGIVE:
            carer = healthy_carers[0]
            cid = int(carer["cid"])
            new_until = max(tick + 6, int(srow["until_tick"]) - 12)
            conn.execute(
                "UPDATE conditions SET until_tick = ? WHERE agent_id = ? AND kind = 'sick'",
                (new_until, sid),
            )
            conn.execute(
                """UPDATE agent_state
                   SET stress = MAX(0, stress - 10), social = MIN(100, social + 8)
                   WHERE agent_id = ?""",
                (sid,),
            )
            lo, hi = min(sid, cid), max(sid, cid)
            conn.execute(
                """UPDATE relationships
                   SET affinity = MIN(100.0, affinity + 4.0),
                       familiarity = MIN(100.0, familiarity + 2.0)
                   WHERE a_id = ? AND b_id = ?""",
                (lo, hi),
            )
            emit(conn, tick, "life_event", a=cid, b=sid, importance=MINOR,
                 text=f"brought soup and looked after {srow['name']} while they were ill",
                 tag="caregiven")
            remember(conn, sid, tick,
                     f"{carer['cname']} looked after me while I was sick.",
                     kind="personal", importance=4)
            remember(conn, cid, tick,
                     f"I looked after {srow['name']} while they were laid up.",
                     kind="personal", importance=3)
            caregive_n += 1

    if day > 0 and day % 3 == 0:
        strained = conn.execute(
            """SELECT r.a_id, r.b_id, r.affinity, a.name AS aname, b.name AS bname
               FROM relationships r
               JOIN agents a ON a.id = r.a_id
               JOIN agents b ON b.id = r.b_id
               WHERE r.affinity BETWEEN -19.5 AND -5.0
                 AND a.alive = 1 AND b.alive = 1
               ORDER BY r.familiarity DESC, r.a_id LIMIT 5"""
        ).fetchall()
        if strained and r.random() < 0.35:
            pair = r.choice(strained)
            aid, bid = int(pair["a_id"]), int(pair["b_id"])
            conn.execute(
                """UPDATE relationships
                   SET affinity = MIN(100.0, affinity + 12.0), label = 'acquaintance'
                   WHERE a_id = ? AND b_id = ?""",
                (aid, bid),
            )
            conn.execute(
                "UPDATE agent_state SET stress = MAX(0, stress - 10) WHERE agent_id IN (?, ?)",
                (aid, bid),
            )
            emit(conn, tick, "life_event", a=aid, b=bid, importance=MINOR,
                 text=f"cleared the air with {pair['bname']} after reflecting on things",
                 tag="epiphany")
            remember(conn, aid, tick,
                     f"I decided to let go of my tension with {pair['bname']}.",
                     kind="reflection", importance=3)
            epiphany_n += 1

    return {
        "contagion": contagion_n,
        "caregiven": caregive_n,
        "epiphanies": epiphany_n,
    }


def grounded_town_event(conn: sqlite3.Connection, tick: int, seed: str, r) -> None:
    """Anchor ambient town happenings to real individual residents when possible."""
    from . import seasons

    weather = [("weather", text, None, None) for text in seasons.SEASON_WEATHER[
        seasons.season_of(day_of(tick))]]

    plumber = conn.execute(
        """SELECT a.id, a.name, j.place_id FROM agents a
           JOIN jobs j ON j.agent_id = a.id
           JOIN places p ON p.id = j.place_id
           WHERE a.alive = 1 AND (p.tags LIKE '%"trades"%' OR p.tags LIKE '%"civic"%')
           ORDER BY a.id LIMIT 10"""
    ).fetchall()
    athlete = conn.execute(
        """SELECT a.id, a.name, a.home_place_id FROM agents a
           WHERE a.alive = 1 AND (a.hobbies_json LIKE '%sport%'
                                  OR a.hobbies_json LIKE '%ball%'
                                  OR a.hobbies_json LIKE '%run%'
                                  OR a.is_child = 1)
           ORDER BY a.id LIMIT 15"""
    ).fetchall()
    grower = conn.execute(
        """SELECT a.id, a.name FROM agents a
           WHERE a.alive = 1 AND a.is_child = 0
             AND (a.hobbies_json LIKE '%garden%' OR a.hobbies_json LIKE '%bak%'
                  OR a.hobbies_json LIKE '%cook%')
           ORDER BY a.id LIMIT 15"""
    ).fetchall()
    cc = conn.execute(
        "SELECT id FROM places WHERE name='Miniville Community Center'"
    ).fetchone()
    park = conn.execute(
        "SELECT id FROM places WHERE name='Lush Meadow Park'"
    ).fetchone()
    hall = conn.execute(
        "SELECT id FROM places WHERE name='Town Hall'"
    ).fetchone()

    situated: list[tuple[str, str, int | None, int | None]] = []
    if plumber:
        who = r.choice(plumber)
        situated.append((
            "town",
            f"A pipe bursts downtown; {who['name']} leads the scramble to patch the main.",
            who["id"],
            hall["id"] if hall else who["place_id"],
        ))
    else:
        situated.append(("town", "A pipe bursts downtown; Town Hall crews scramble.",
                         None, hall["id"] if hall else None))

    if athlete:
        who = r.choice(athlete)
        situated.append((
            "town",
            f"{who['name']} scores the winning point at the home game; Greenhill celebrates.",
            who["id"],
            park["id"] if park else None,
        ))
    else:
        situated.append(("town", "The high school team wins a home game; Greenhill celebrates.",
                         None, park["id"] if park else None))

    if grower:
        who = r.choice(grower)
        situated.append((
            "town",
            f"A farmers' market sets up at the Community Center; {who['name']}'s stall sells out early.",
            who["id"],
            cc["id"] if cc else None,
        ))
    else:
        situated.append(("town", "A farmers' market sets up in the Community Center lot.",
                         None, cc["id"] if cc else None))

    situated.append(("town", "The Miniville Gazette publishes its weekly edition.", None, None))
    kind, text, aid, pid = r.choice(situated + weather)
    emit(conn, tick, "town_event", place_id=pid, a=aid, importance=2,
         text=text, tag=kind)


def update_favorite_places(conn: sqlite3.Connection, day: int) -> int:
    """At day end, residents who had warm/delightful encounters at a venue
    adopt it as their favorite third place."""
    rows = conn.execute(
        """SELECT e.a_id, e.b_id, e.place_id, e.data
           FROM events e JOIN places p ON p.id = e.place_id
           WHERE e.day = ? AND e.kind = 'encounter' AND p.kind != 'home'""",
        (day,),
    ).fetchall()
    if not rows:
        return 0
    best_by_agent: dict[int, int] = {}
    for r in rows:
        try:
            d = json.loads(r["data"] or "{}")
        except ValueError:
            continue
        if d.get("tone") not in ("delightful", "warm", "engaging"):
            continue
        for aid in (r["a_id"], r["b_id"]):
            if aid:
                best_by_agent[int(aid)] = int(r["place_id"])
    if not best_by_agent:
        return 0
    conn.executemany(
        "UPDATE agents SET favorite_place_id = ? WHERE id = ?",
        [(pid, aid) for aid, pid in sorted(best_by_agent.items())],
    )
    return len(best_by_agent)


def _find_resident(conn: sqlite3.Connection, who: str) -> sqlite3.Row | None:
    if str(who).isdigit():
        row = conn.execute(
            "SELECT * FROM agents WHERE id=? AND alive=1", (int(who),)
        ).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM agents WHERE name LIKE ? AND alive=1 ORDER BY id LIMIT 1",
        (f"%{who}%",),
    ).fetchone()


def inject_fate(conn: sqlite3.Connection, who: str, kind: str,
                seed: str = "miniville") -> dict[str, Any]:
    """Trigger a personal event on a specific living resident (`cli fate`)."""
    if kind not in FATE_KINDS:
        return {"ok": False, "message": f"unknown fate {kind!r} (pick from {', '.join(FATE_KINDS)})"}
    agent = _find_resident(conn, who)
    if not agent:
        return {"ok": False, "message": f"no living resident matches {who!r}"}
    aid = int(agent["id"])
    tick = int(get_meta(conn, "tick", "0") or 0)
    r = rng_for(seed, "fate", aid, tick, kind)
    prof = profile_of(conn, aid, seed)
    st = conn.execute(
        """SELECT s.*, a.name, a.hobbies_json, a.skills_json
           FROM agent_state s JOIN agents a ON a.id = s.agent_id
           WHERE s.agent_id = ?""",
        (aid,),
    ).fetchone()

    if kind == "breakthrough":
        pid = agent["work_place_id"] or st["place_id"]
        vname = "town"
        if pid:
            pr = conn.execute("SELECT name FROM places WHERE id=?", (pid,)).fetchone()
            if pr:
                vname = pr["name"]
        knack = prof.get("knack", "their craft")
        conn.execute(
            "UPDATE agent_state SET fun=MIN(100,fun+15), stress=MAX(0,stress-12) WHERE agent_id=?",
            (aid,),
        )
        text = f"achieved a personal breakthrough in {knack} at {vname}"
        emit(conn, tick, "life_event", place_id=pid, a=aid, importance=NOTABLE,
             text=text, tag="craft_breakthrough", knack=knack)
        remember(conn, aid, tick, f"I achieved a personal breakthrough in {knack}!",
                 kind="personal", importance=4)
        conn.commit()
        return {"ok": True, "message": f"{agent['name']}: {text}"}

    if kind == "mishap":
        days = 2
        conn.execute(
            """INSERT INTO conditions(agent_id, kind, until_tick)
               VALUES(?, 'sick', ?)
               ON CONFLICT(agent_id, kind) DO UPDATE
               SET until_tick = MAX(until_tick, excluded.until_tick)""",
            (aid, tick + days * TICKS_PER_DAY),
        )
        conn.execute(
            "UPDATE agent_state SET place_id=?, activity='resting', "
            "stress=MIN(100,stress+15) WHERE agent_id=?",
            (agent["home_place_id"], aid),
        )
        text = f"suffered a personal mishap and is laid up at home for {days} days"
        emit(conn, tick, "life_event", place_id=agent["home_place_id"], a=aid,
             importance=NOTABLE, text=text, tag="work_mishap")
        remember(conn, aid, tick, "I suffered a nasty mishap and have to rest at home.",
                 kind="personal", importance=4)
        conn.commit()
        return {"ok": True, "message": f"{agent['name']}: {text}"}

    if kind == "triumph":
        keys = agent.keys() if hasattr(agent, "keys") else ()
        pid = agent["favorite_place_id"] if "favorite_place_id" in keys and agent["favorite_place_id"] else None
        if not pid:
            v = conn.execute(
                "SELECT id FROM places WHERE kind='public' ORDER BY id LIMIT 1"
            ).fetchone()
            pid = v["id"] if v else st["place_id"]
        vname = conn.execute("SELECT name FROM places WHERE id=?", (pid,)).fetchone()["name"]
        hobbies = _parse_json_list(agent["hobbies_json"])
        passion = hobbies[0] if hobbies else prof.get("knack", "their passion")
        conn.execute(
            "UPDATE agent_state SET fun=MIN(100,fun+20), stress=MAX(0,stress-15) WHERE agent_id=?",
            (aid,),
        )
        conn.execute("UPDATE agents SET favorite_place_id=? WHERE id=?", (pid, aid))
        text = f"celebrated a personal triumph with {passion} at {vname}"
        emit(conn, tick, "life_event", place_id=pid, a=aid, importance=NOTABLE,
             text=text, tag="hobby_triumph", passion=passion)
        remember(conn, aid, tick, f"Celebrated a personal triumph with {passion} at {vname}!",
                 kind="personal", importance=4)
        conn.commit()
        return {"ok": True, "message": f"{agent['name']}: {text}"}

    if kind == "repair":
        _trigger_home_event(conn, st, prof, tick, r)
        conn.commit()
        ev = conn.execute(
            "SELECT * FROM events WHERE a_id=? ORDER BY id DESC LIMIT 1", (aid,)
        ).fetchone()
        from .events import describe
        return {"ok": True, "message": describe(conn, ev) if ev else f"{agent['name']}: repair handled"}

    if kind == "care":
        conn.execute(
            "UPDATE conditions SET until_tick = MAX(?, until_tick - 24) WHERE agent_id=?",
            (tick, aid),
        )
        conn.execute(
            "UPDATE agent_state SET stress=MAX(0,stress-15), social=MIN(100,social+12), "
            "energy=MIN(100,energy+10) WHERE agent_id=?",
            (aid,),
        )
        text = "received a warm visit and home-cooked meal from a neighbour"
        emit(conn, tick, "life_event", place_id=agent["home_place_id"], a=aid,
             importance=MINOR, text=text, tag="caregiven")
        remember(conn, aid, tick, "A neighbour checked in on me and lifted my spirits.",
                 kind="personal", importance=3)
        conn.commit()
        return {"ok": True, "message": f"{agent['name']}: {text}"}

    conn.execute(
        "UPDATE agent_state SET mood='content', stress=MAX(0,stress-20), "
        "fun=MIN(100,fun+10) WHERE agent_id=?",
        (aid,),
    )
    text = f"had a quiet personal epiphany about their {prof.get('archetype', 'life in town')}"
    emit(conn, tick, "life_event", place_id=st["place_id"], a=aid,
         importance=NOTABLE, text=text, tag="epiphany")
    remember(conn, aid, tick, "I had a quiet realization about what matters most to me here.",
             kind="reflection", importance=4)
    conn.commit()
    return {"ok": True, "message": f"{agent['name']}: {text}"}


def resident_summary(conn: sqlite3.Connection, agent_id: int) -> dict[str, Any]:
    """Return structured individualism metadata for CLI `inspect` and UI."""
    seed = get_meta(conn, "seed", "miniville") or "miniville"
    prof = profile_of(conn, agent_id, seed)
    row = conn.execute(
        """SELECT a.favorite_place_id, p.name AS favorite_place
           FROM agents a LEFT JOIN places p ON p.id = a.favorite_place_id
           WHERE a.id = ?""",
        (agent_id,),
    ).fetchone()
    personal_evs = conn.execute(
        """SELECT id, tick, day, kind, place_id, a_id, b_id, importance, data
           FROM events
           WHERE (a_id = ? OR b_id = ?) AND kind = 'life_event'
           ORDER BY id DESC LIMIT 8""",
        (agent_id, agent_id),
    ).fetchall()
    from .events import describe
    return {
        "archetype": prof.get("archetype", "balanced regular"),
        "chronotype": prof.get("chronotype", "day_walker"),
        "wake_offset": prof.get("wake_offset", 0),
        "sleep_offset": prof.get("sleep_offset", 0),
        "knack": prof.get("knack", "helping out"),
        "axes": {
            k: prof.get(k, 0.0)
            for k in ("sociability", "industriousness", "curiosity", "resilience", "thrift")
        },
        "favorite_place_id": row["favorite_place_id"] if row else None,
        "favorite_place": row["favorite_place"] if row else None,
        "personal_events": [
            {"tick": e["tick"], "day": e["day"] + 1, "text": describe(conn, e)}
            for e in personal_evs
        ],
    }


def town_individualism_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summary of town-wide chronotypes, archetypes, and personal events."""
    seed = get_meta(conn, "seed", "miniville") or "miniville"
    ensure_profiles(conn, seed)
    rows = conn.execute("SELECT id FROM agents WHERE alive=1").fetchall()
    chronos: dict[str, int] = {}
    archetypes: dict[str, int] = {}
    for r in rows:
        p = profile_of(conn, r["id"], seed)
        c = p.get("chronotype", "day_walker")
        a = p.get("archetype", "balanced regular")
        chronos[c] = chronos.get(c, 0) + 1
        archetypes[a] = archetypes.get(a, 0) + 1
    favs = conn.execute(
        """SELECT p.name, COUNT(*) n FROM agents a
           JOIN places p ON p.id = a.favorite_place_id
           WHERE a.alive = 1 GROUP BY p.id ORDER BY n DESC LIMIT 6"""
    ).fetchall()
    personal_tags = ("craft_breakthrough", "work_mishap", "hobby_triumph",
                     "diy_fix", "repair_bill", "good_turn", "caregiven", "epiphany")
    tag_counts: dict[str, int] = {}
    for t in personal_tags:
        c = conn.execute(
            "SELECT COUNT(*) n FROM events WHERE data LIKE ?",
            (f'%"tag": "{t}"%',),
        ).fetchone()["n"]
        if c:
            tag_counts[t] = c
    return {
        "chronotypes": chronos,
        "top_archetypes": dict(sorted(archetypes.items(), key=lambda kv: -kv[1])[:8]),
        "favorite_places": [{"venue": r["name"], "regulars": r["n"]} for r in favs],
        "personal_event_counts": tag_counts,
    }
