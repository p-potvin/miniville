"""Influence, and the three things a town does about a grudge.

Phase 3 of the affiliations work. The town already knows who dislikes whom
(`relationships.affinity` goes negative, and the labels become `friction`,
`rival`, `estranged`) and what it thinks of everyone (`agents.standing`). What
was missing was a way for that to *do* something beyond a label on a row.

Influence is a reading, not a new fact: standing, rank, money, a council seat,
and the size of the flock you lead. It decides who can start something.

Then three acts, each with a consequence in the world rather than in a
relationship label:

* **slander** — a rival talks. The target loses standing, and the more people
  the slanderer is connected to, the further it carries.
* **boycott** — a group withdraws its custom from a venue. Its members stop
  spending there, the venue's traffic decays, and a business that loses its
  customers can fail. A grudge can close a shop.
* **schism** — a congregation whose members no longer get along splits, and
  the leavers found their own, meeting somewhere else.

Everything is seeded per day, so a replay produces the same grudges.
"""
from __future__ import annotations

import json
import sqlite3

from .events import HISTORIC, MAJOR, NOTABLE, emit
from .rng import rng_for
from .timekeeper import day_of

RIVAL_AFFINITY = -20.0       # below this the town calls it a rivalry
SLANDER_P_DAY = 0.02         # chance a rival speaks up on a given day
SLANDER_BITE = 3             # standing the target loses per story
BOYCOTT_P_DAY = 0.01         # chance a group takes its custom elsewhere
BOYCOTT_DAYS = 30
SCHISM_MIN_MEMBERS = 12
SCHISM_P_DAY = 0.01


def influence_of(conn: sqlite3.Connection, agent_id: int) -> float:
    """Standing, rank, money, a seat and a flock — the town's own arithmetic."""
    row = conn.execute(
        """SELECT a.standing, a.is_child, s.money_cents,
                  COALESCE(j.rank, 0) rank
           FROM agents a LEFT JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN jobs j ON j.agent_id = a.id
           WHERE a.id = ?""", (agent_id,)).fetchone()
    if not row or row["is_child"]:
        return 0.0
    seat = conn.execute("SELECT COUNT(*) n FROM council WHERE agent_id=?",
                        (agent_id,)).fetchone()["n"]
    publisher = conn.execute(
        "SELECT 1 FROM newspaper_profile WHERE publisher_id=?", (agent_id,)).fetchone()
    officer = conn.execute(
        """SELECT COUNT(*) n, COALESCE(SUM((SELECT COUNT(*) FROM memberships m2
               WHERE m2.group_id = m.group_id)), 0) flock
           FROM memberships m WHERE m.agent_id = ? AND m.role = 'officer'""",
        (agent_id,)).fetchone()
    owns = conn.execute(
        "SELECT COUNT(*) n FROM businesses WHERE owner_id=? AND status='open'",
        (agent_id,)).fetchone()["n"]
    wallets = conn.execute(
        """SELECT s.money_cents m FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1 AND a.is_child=0""").fetchall()
    median = sorted(x["m"] for x in wallets)[len(wallets) // 2] if wallets else 1
    wealth = (row["money_cents"] or 0) / max(1, median) - 1.0
    return round((row["standing"] or 0) / 10.0
                 + row["rank"] * 2.0
                 + max(-3.0, min(3.0, wealth * 3.0))
                 + seat * 8.0
                 + (6.0 if publisher else 0.0)  # owns the town's paper
                 + owns * 4.0                   # owns a shop people depend on
                 + officer["n"] * 2.0
                 + (officer["flock"] or 0) ** 0.5 * 0.5, 2)


def most_influential(conn: sqlite3.Connection, n: int = 10) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, standing FROM agents
           WHERE alive=1 AND is_child=0 ORDER BY COALESCE(standing,0) DESC LIMIT 60"""
    ).fetchall()
    scored = [{"id": r["id"], "name": r["name"], "influence": influence_of(conn, r["id"])}
              for r in rows]
    scored.sort(key=lambda x: (-x["influence"], x["id"]))
    return scored[:n]


# --- slander ------------------------------------------------------------------


def slanders(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """A rival talks about you, and it carries as far as they are connected."""
    day = day_of(tick)
    rivals = conn.execute(
        """SELECT a_id, b_id FROM relationships WHERE affinity <= ?""",
        (RIVAL_AFFINITY,)).fetchall()
    told = 0
    for rel in rivals:
        r = rng_for(seed, "slander", rel["a_id"], rel["b_id"], day)
        if r.random() >= SLANDER_P_DAY:
            continue
        speaker, target = rel["a_id"], rel["b_id"]
        if r.random() < 0.5:
            speaker, target = target, speaker
        reach = conn.execute(
            "SELECT COUNT(*) n FROM relationships WHERE a_id=? OR b_id=?",
            (speaker, speaker)).fetchone()["n"]
        bite = SLANDER_BITE + min(3, reach // 40)      # a well-connected gossip
        names = conn.execute(
            "SELECT id, name FROM agents WHERE id IN (?,?)", (speaker, target)).fetchall()
        by = {x["id"]: x["name"] for x in names}
        conn.execute(
            "UPDATE agents SET standing = MAX(-100, COALESCE(standing,0) - ?) WHERE id=?",
            (bite, target))
        emit(conn, tick, "town_event", a=speaker, b=target, importance=MAJOR,
             text=f"{by.get(speaker, 'someone')} has been telling people about "
                  f"{by.get(target, 'someone')}", tag="slander",
             bite=bite, reach=reach)
        told += 1
    return told


# --- boycott ------------------------------------------------------------------


def boycotts(conn: sqlite3.Connection, tick: int, seed: str) -> list[dict]:
    """A group takes its custom elsewhere; the venue loses its customers."""
    day = day_of(tick)
    conn.execute("DELETE FROM boycotts WHERE until_day < ?", (day,))
    started = []
    rows = conn.execute(
        """SELECT g.id gid, g.name gname, g.venue_id,
                  COUNT(m.agent_id) members
           FROM groups g JOIN memberships m ON m.group_id = g.id
           GROUP BY g.id HAVING members >= 6""").fetchall()
    for g in rows:
        r = rng_for(seed, "boycott", g["gid"], day)
        if r.random() >= BOYCOTT_P_DAY:
            continue
        # the group needs a grievance: a venue where its members are unhappy
        angry = conn.execute(
            """SELECT r.a_id, r.b_id, p.id place_id, p.name place_name
               FROM relationships r
               JOIN memberships m ON m.agent_id = r.a_id AND m.group_id = ?
               JOIN agents a ON a.id = r.b_id
               JOIN jobs j ON j.agent_id = r.b_id
               JOIN places p ON p.id = j.place_id
               WHERE r.affinity <= ? LIMIT 1""",
            (g["gid"], RIVAL_AFFINITY)).fetchone()
        if not angry or angry["place_id"] == g["venue_id"]:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO boycotts(group_id,place_id,started_day,
                   until_day,reason) VALUES(?,?,?,?,?)""",
            (g["gid"], angry["place_id"], day, day + BOYCOTT_DAYS,
             f"a grievance against someone who works there"))
        emit(conn, tick, "town_event", place_id=angry["place_id"], importance=MAJOR,
             text=f"{g['gname']} is boycotting {angry['place_name']}",
             tag="boycott", group=g["gname"], until_day=day + BOYCOTT_DAYS)
        started.append({"group": g["gname"], "venue": angry["place_name"]})
    return started


def boycotting(conn: sqlite3.Connection, agent_id: int, place_id: int,
               day: int) -> bool:
    """Is this resident withholding their custom from this venue today?"""
    return conn.execute(
        """SELECT 1 FROM boycotts b JOIN memberships m ON m.group_id = b.group_id
           WHERE m.agent_id = ? AND b.place_id = ? AND b.until_day >= ?""",
        (agent_id, place_id, day)).fetchone() is not None


# --- schism -------------------------------------------------------------------


def schisms(conn: sqlite3.Connection, tick: int, seed: str) -> list[dict]:
    """A congregation that has stopped getting along splits in two."""
    day = day_of(tick)
    out = []
    for g in conn.execute(
            """SELECT g.id, g.name, g.kind, g.venue_id, COUNT(m.agent_id) members
               FROM groups g JOIN memberships m ON m.group_id = g.id
               WHERE g.kind = 'congregation'
               GROUP BY g.id HAVING members >= ?""", (SCHISM_MIN_MEMBERS,)):
        r = rng_for(seed, "schism", g["id"], day)
        if r.random() >= SCHISM_P_DAY:
            continue
        # the least content quarter of the congregation walks out
        members = [x["agent_id"] for x in conn.execute(
            "SELECT agent_id FROM memberships WHERE group_id=?", (g["id"],))]
        if len(members) < SCHISM_MIN_MEMBERS:
            continue
        ranked = []
        for aid in members:
            warmth = conn.execute(
                f"""SELECT COALESCE(AVG(affinity),0) a FROM relationships
                    WHERE (a_id IN ({','.join('?' * len(members))})
                       AND b_id = ?) OR (b_id IN ({','.join('?' * len(members))})
                       AND a_id = ?)""", (*members, aid, *members, aid)).fetchone()["a"]
            ranked.append((warmth, aid))
        ranked.sort()
        leavers = [aid for _w, aid in ranked[:max(4, len(members) // 4)]]
        # somewhere else to meet
        venue = conn.execute(
            """SELECT id, name FROM places
               WHERE kind != 'home' AND id != ? AND tags LIKE '%community%'
               ORDER BY id LIMIT 1""", (g["venue_id"],)).fetchone()
        if venue is None:
            continue
        name = f"the {g['name'].split()[-2] if len(g['name'].split()) > 2 else 'breakaway'} " \
               f"meeting"
        name = f"the breakaway {g['name'].replace('the ', '')}"
        cur = conn.execute(
            """INSERT OR IGNORE INTO groups(name,kind,venue_id,meets_day,meets_tick,
                   founded_tick) VALUES(?,?,?,?,?,?)""",
            (name, "congregation", venue["id"], 6, 20, tick))
        if not cur.rowcount:
            continue
        gid = int(cur.lastrowid or 0)
        for aid in leavers:
            conn.execute("DELETE FROM memberships WHERE group_id=? AND agent_id=?",
                         (g["id"], aid))
            conn.execute(
                """INSERT OR IGNORE INTO memberships(group_id,agent_id,role,joined_tick)
                   VALUES(?,?,'member',?)""", (gid, aid, tick))
        emit(conn, tick, "town_event", place_id=venue["id"], importance=HISTORIC,
             text=f"{g['name']} has split; {len(leavers)} of its members now "
                  f"meet at {venue['name']}", tag="schism",
             from_group=g["name"], to_group=name, left=len(leavers))
        out.append({"from": g["name"], "to": name, "left": len(leavers)})
    return out


def due(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Weekly: grudges act. Monthly: congregations reconsider each other."""
    day = day_of(tick)
    out: dict = {}
    if day % 7 == 0:
        n = slanders(conn, tick, seed)
        if n:
            out["slandered"] = n
        b = boycotts(conn, tick, seed)
        if b:
            out["boycotts"] = b
    if day % 30 == 0:
        s = schisms(conn, tick, seed)
        if s:
            out["schisms"] = s
    return out
