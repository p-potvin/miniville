"""Affiliations: the congregations, clubs and unions that make a society.

Everything social in the town used to be pairwise — a `relationships` row
between two people — or private, inside a household. Real towns are not like
that: they are made of overlapping groups, and a resident belongs to several
at once. That layer is what this module adds.

Two decisions worth stating:

* **Faith comes from the persona, not from me.** Each persona's own
  `cultural_background` names a tradition often enough to be worth parsing
  (~28% of them; the rest say nothing, which is a fact about the person and
  is left as it is rather than filled in). Children inherit from the adult
  they live with, because that is what children do.
* **Groups shape who is where when, and the rest is emergent.** A meeting is
  a schedule slot that puts members in the same venue at the same tick, and
  the existing encounter engine does the social work from there. Nothing here
  scripts a friendship; it only arranges the room.

A group's standing is the mean standing of its members, so a congregation led
by someone the town distrusts is itself distrusted — power by association.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from .events import NOTABLE, emit
from .rng import rng_for
from .timekeeper import day_of

# --- faith, parsed from the persona's own words ------------------------------

TRADITIONS: list[tuple[str, str]] = [
    ("catholic", r"catholic|jesuit|franciscan"),
    ("protestant", r"protestant|presbyterian|episcopal|anglican"),
    ("methodist", r"methodist"),
    ("baptist", r"baptist"),
    ("lutheran", r"lutheran"),
    ("evangelical", r"evangelical|pentecostal|born[- ]again"),
    ("mormon", r"mormon|latter[- ]day saint"),
    ("orthodox", r"orthodox christian|greek orthodox"),
    ("jewish", r"jewish|judaism|synagogue"),
    ("muslim", r"muslim|islam|mosque"),
    ("hindu", r"hindu"),
    ("buddhist", r"buddhis"),
    ("quaker", r"quaker|friends meeting"),
    ("unaffiliated", r"atheist|agnostic|non[- ]religious|secular|not religious"),
]
_COMPILED = [(name, re.compile(pat, re.I)) for name, pat in TRADITIONS]
# a congregation needs a quorum; below this a tradition is a few neighbours,
# not an institution
CONGREGATION_QUORUM = 6

# --- interests, from the persona's hobbies -----------------------------------

# hobby keyword -> (club label, venue tags we look for)
INTERESTS: list[tuple[str, str, list[str]]] = [
    ("fishing", r"fish|angling", ["water", "outdoors"]),
    ("hiking", r"hik|trail|walking", ["outdoors"]),
    ("gardening", r"garden|horticult", ["outdoors", "community"]),
    ("sport", r"sport|soccer|baseball|basketball|football|running|cycling",
     ["sport", "fitness"]),
    ("reading", r"read|book|literature|poetry", ["quiet", "study"]),
    ("music", r"music|guitar|piano|sing|choir|band", ["arts"]),
    ("cooking", r"cook|bak|culinary|grill|barbecue", ["food"]),
    ("crafts", r"craft|woodwork|knit|sew|paint|pottery|diy", ["community"]),
    ("volunteering", r"volunteer|charit|community service", ["community"]),
    ("photography", r"photograph|camera|film", ["arts"]),
]
_CLUB_QUORUM = 5
# A club is small and voluntary. Matching an interest against free-text
# hobbies catches almost everyone ("reading" is in half the town's hobby
# list), so a club takes a slice of the interested and the rest of them start
# another one: the town ends up with several reading groups, which is what a
# real town has, rather than one 326-member demographic.
CLUB_SIZE = 10
CLUB_MAX = 18
MAX_MEMBERSHIPS = 2          # one congregation and one club, typically
MAX_GROUPS = 40
MAX_CLUBS_PER_INTEREST = 3
CLUB_SUFFIXES = ["club", "circle", "society"]


def faith_of(text: str | None) -> str | None:
    """The tradition a persona's own words name, or None if they name none."""
    if not text:
        return None
    for name, pat in _COMPILED:
        if pat.search(text):
            return name
    return None


def _persona_text(row: sqlite3.Row) -> str:
    bits = [row["persona"] or ""]
    try:
        traits = json.loads(row["traits_json"] or "{}")
        bits.append(traits.get("background") or "")
    except (TypeError, ValueError):
        pass
    return " ".join(bits)


def assign_faith(conn: sqlite3.Connection) -> dict:
    """Give every resident the faith their persona names; children inherit."""
    adults = conn.execute(
        """SELECT id, persona, traits_json, household_id FROM agents
           WHERE alive=1 AND is_child=0""").fetchall()
    named = 0
    for row in adults:
        f = faith_of(_persona_text(row))
        if f:
            conn.execute("UPDATE agents SET faith=? WHERE id=?", (f, row["id"]))
            named += 1
    # a child holds the faith of the household they live in
    kids = conn.execute(
        """SELECT a.id, a.household_id FROM agents a
           WHERE a.alive=1 AND a.is_child=1 AND a.household_id IS NOT NULL""").fetchall()
    inherited = 0
    for kid in kids:
        row = conn.execute(
            """SELECT faith FROM agents WHERE household_id=? AND alive=1
               AND is_child=0 AND faith IS NOT NULL
               ORDER BY age DESC LIMIT 1""", (kid["household_id"],)).fetchone()
        if row and row["faith"]:
            conn.execute("UPDATE agents SET faith=? WHERE id=?", (row["faith"], kid["id"]))
            inherited += 1
    conn.commit()
    return {"named": named, "inherited": inherited}


# --- forming the groups -------------------------------------------------------


def _venues_by_tags(conn: sqlite3.Connection, tags: list[str]) -> list[int]:
    rows = conn.execute("SELECT id, tags, kind FROM places WHERE kind != 'home'").fetchall()
    want = set(tags)
    scored = []
    for r in rows:
        ptags = set(json.loads(r["tags"] or "[]"))
        hit = len(ptags & want)
        if hit:
            scored.append((hit, r["id"]))
    scored.sort(key=lambda t: -t[0])
    return [pid for _, pid in scored]


def _pick_venue(conn: sqlite3.Connection, tags: list[str], used: set[int],
                r) -> int | None:
    for pid in _venues_by_tags(conn, tags):
        if pid not in used:
            return pid
    rows = _venues_by_tags(conn, tags)
    return r.choice(rows) if rows else None


def form_groups(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Create the congregations and clubs the town's own make-up supports."""
    r = rng_for(seed, "groups", tick)
    day = day_of(tick)
    used_venues: set[int] = set()
    existing = {row["name"] for row in conn.execute("SELECT name FROM groups")}
    founded = []

    # congregations: one per tradition with a quorum, meeting Sunday morning
    faiths = Counter(
        row["faith"] for row in conn.execute(
            "SELECT faith FROM agents WHERE alive=1 AND faith IS NOT NULL "
            "AND faith != 'unaffiliated'"))
    for tradition, count in faiths.most_common():
        if count < CONGREGATION_QUORUM:
            continue
        name = f"the {tradition} congregation"
        if name in existing:
            continue
        venue = _pick_venue(conn, ["worship", "community"], used_venues, r)
        if venue is None:
            break
        used_venues.add(venue)
        gid = _create(conn, name, "congregation", venue, meets_day=6, meets_tick=20,
                      tick=tick)
        members = [row["id"] for row in conn.execute(
            "SELECT id FROM agents WHERE alive=1 AND faith=? ORDER BY id", (tradition,))]
        _seat(conn, gid, members, tick, r)
        founded.append({"name": name, "kind": "congregation", "members": len(members),
                        "tradition": tradition})

    # clubs: the interests enough residents actually share
    hobby_counts: Counter = Counter()
    by_interest: dict[str, list[int]] = {}
    for row in conn.execute(
            """SELECT id, hobbies_json FROM agents
               WHERE alive=1 AND is_child=0 AND hobbies_json NOT IN ('[]','')"""):
        text = (row["hobbies_json"] or "").lower()
        for label, pat, _tags in INTERESTS:
            if re.search(pat, text):
                hobby_counts[label] += 1
                by_interest.setdefault(label, []).append(row["id"])
    # who is still free to take on a club
    taken = {row["agent_id"] for row in conn.execute("SELECT agent_id FROM memberships")}
    free_count = {row["id"]: 0 for row in conn.execute(
        "SELECT id FROM agents WHERE alive=1")}
    for row in conn.execute("SELECT agent_id, COUNT(*) n FROM memberships "
                            "GROUP BY agent_id"):
        free_count[row["agent_id"]] = row["n"]
    for label, _count in hobby_counts.most_common():
        if len(founded) >= MAX_GROUPS:
            break
        candidates = [aid for aid in by_interest.get(label, [])
                      if free_count.get(aid, 0) < MAX_MEMBERSHIPS]
        if len(candidates) < _CLUB_QUORUM:
            continue
        r.shuffle(candidates)
        tags = next(t for lbl, _p, t in INTERESTS if lbl == label)
        for suffix in CLUB_SUFFIXES[:MAX_CLUBS_PER_INTEREST]:
            if len(candidates) < _CLUB_QUORUM or len(founded) >= MAX_GROUPS:
                break
            size = min(len(candidates), r.randint(_CLUB_QUORUM, CLUB_MAX))
            members, candidates = candidates[:size], candidates[size:]
            venue = _pick_venue(conn, tags, used_venues, r)
            if venue is None:
                break
            # named for where it meets — "the Greenhill reading circle" is how
            # a town names a club; six suffix-variants of one interest is not
            district = conn.execute("SELECT district FROM places WHERE id=?",
                                    (venue,)).fetchone()["district"]
            where = district if district.lower().startswith("the") else f"the {district}"
            name = f"{where} {label} {suffix}"
            if name in existing:
                continue
            used_venues.add(venue)
            # clubs meet Mon-Sat (0-5); Sunday is the congregations' day, and
            # leaving Monday out meant some days had no gathering at all
            # evening, but clear of the dinner window (tick 38)
            gid = _create(conn, name, "club", venue, meets_day=r.randrange(0, 6),
                          meets_tick=r.choice([32, 34, 42]), tick=tick)
            _seat(conn, gid, members, tick, r)
            for aid in members:
                free_count[aid] = free_count.get(aid, 0) + 1
            founded.append({"name": name, "kind": "club", "members": len(members)})

    conn.commit()
    return {"founded": founded, "groups": len(founded)}


def _create(conn: sqlite3.Connection, name: str, kind: str, venue: int,
            meets_day: int | None, meets_tick: int | None, tick: int) -> int:
    cur = conn.execute(
        """INSERT INTO groups(name,kind,venue_id,meets_day,meets_tick,founded_tick)
           VALUES(?,?,?,?,?,?)""", (name, kind, venue, meets_day, meets_tick, tick))
    gid = int(cur.lastrowid or 0)
    venue_name = conn.execute("SELECT name FROM places WHERE id=?",
                              (venue,)).fetchone()["name"]
    emit(conn, tick, "town_event", place_id=venue, importance=NOTABLE,
         text=f"{name} was founded; it meets at {venue_name}"
              + (f" on {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][meets_day]}s"
                 if meets_day is not None else ""),
         tag="group_founded", group=name, group_kind=kind)
    return gid


def _seat(conn: sqlite3.Connection, gid: int, members: list[int], tick: int,
          r) -> None:
    """Everyone joins; the town's best-regarded member takes the chair."""
    if not members:
        return
    ranked = conn.execute(
        f"""SELECT id, COALESCE(standing,0) s FROM agents
            WHERE id IN ({','.join('?' * len(members))})
            ORDER BY s DESC, id""", members).fetchall()
    officer = ranked[0]["id"] if ranked else None
    for aid in members:
        conn.execute(
            """INSERT OR IGNORE INTO memberships(group_id,agent_id,role,joined_tick)
               VALUES(?,?,?,?)""",
            (gid, aid, "officer" if aid == officer else "member", tick))


# --- the meetings, and the social work they hand to encounters ----------------

def meetings_today(conn: sqlite3.Connection, day: int) -> dict[int, tuple[int, int, str]]:
    """agent_id -> (venue_id, tick, group_name) for today's gatherings.

    Built once per day rebuild and handed to the planner, so a group is
    nothing more than an arrangement of who is in the room.
    """
    weekday = day % 7
    out: dict[int, tuple[int, int, str]] = {}
    rows = conn.execute(
        """SELECT g.id, g.name, g.venue_id, g.meets_tick, m.agent_id
           FROM groups g JOIN memberships m ON m.group_id=g.id
           WHERE g.meets_day=? AND g.venue_id IS NOT NULL AND g.meets_tick IS NOT NULL
           ORDER BY g.id""", (weekday,)).fetchall()
    for r in rows:
        out.setdefault(r["agent_id"], (r["venue_id"], r["meets_tick"], r["name"]))
    return out


def refresh_standing(conn: sqlite3.Connection) -> int:
    """A group's standing is the mean of its members': power by association."""
    rows = conn.execute(
        """SELECT g.id, AVG(COALESCE(a.standing,0)) s
           FROM groups g JOIN memberships m ON m.group_id=g.id
           JOIN agents a ON a.id=m.agent_id AND a.alive=1
           GROUP BY g.id""").fetchall()
    for r in rows:
        conn.execute("UPDATE groups SET standing=? WHERE id=?",
                     (int(r["s"] or 0), r["id"]))
    return len(rows)


def memberships_of(conn: sqlite3.Connection, agent_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT g.id, g.name, g.kind, m.role, g.standing, p.name venue
           FROM memberships m JOIN groups g ON g.id=m.group_id
           LEFT JOIN places p ON p.id=g.venue_id
           WHERE m.agent_id=? ORDER BY g.id""", (agent_id,))]


def roster(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT g.id, g.name, g.kind, g.standing, p.name venue, g.meets_day,
                  g.meets_tick, COUNT(m.agent_id) members
           FROM groups g LEFT JOIN places p ON p.id=g.venue_id
           LEFT JOIN memberships m ON m.group_id=g.id
           GROUP BY g.id ORDER BY members DESC, g.id""")]
