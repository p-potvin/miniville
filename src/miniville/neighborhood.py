"""Neighborhood micro-events: district block parties, mutual aid, and civic watch.

Miniville's five districts (`Downtown`, `Lakeshore`, `Greenhill`, `Old Mill
Quarter`, `The Flats`) have distinct rents, wealth profiles, and council seats,
yet daily life inside a district had no neighborhood-level texture. This module
adds three situated district dynamics:

* **Neighborhood Watch (`watch_patrol` & `vigilance_bonus_for`)** — when petty
  thefts occur in a district, resilient/respected neighbours organize an
  evening watch patrol, raising `district_watch.vigilance` (`0.0..1.0`), which
  directly boosts `crime.py`'s catch probability in that district.
* **Conserved Neighbour Mutual Aid (`mutual_aid`)** — when a household in a
  district falls into hardship (`money_cents < 3500`), comfortable neighbours
  in the same district (`money_cents >= 250000`) chip in a strictly conserved
  `$12–$28` mutual-aid basket (`SUM(wallets)` unchanged to the cent), easing
  stress and building district cohesion.
* **Saturday District Block Parties (`block_party`)** — on Saturdays, a
  high-cohesion district hosts a street block party or porch swap led by a
  gregarious local resident, warming intra-district ties and lifting morale.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .economy import RENT_BY_DISTRICT
from .events import MINOR, NOTABLE, emit
from .individual import profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

DISTRICTS = tuple(RENT_BY_DISTRICT.keys())
VIGILANCE_DECAY = 0.02
VIGILANCE_PATROL_BUMP = 0.28
MAX_VIGILANCE_CATCH_BONUS = 0.22
MUTUAL_AID_MIN = 1_200     # $12 conserved neighbour transfer
MUTUAL_AID_MAX = 2_800     # $28 conserved neighbour transfer


def ensure_districts(conn: sqlite3.Connection) -> None:
    """Seed `district_watch` rows for all five town districts."""
    conn.executemany(
        """INSERT OR IGNORE INTO district_watch(
               district, vigilance, cohesion, last_event_day, last_event_kind)
           VALUES(?, 0.10, 0.50, -1, NULL)""",
        [(d,) for d in DISTRICTS],
    )


def vigilance_bonus_for(conn: sqlite3.Connection, place_id: int | None,
                        victim_id: int | None) -> float:
    """Return additional catch probability `[0.0, MAX_VIGILANCE_CATCH_BONUS]`
    from the target district's active Neighborhood Watch vigilance."""
    district = None
    if place_id is not None:
        prow = conn.execute(
            "SELECT district FROM places WHERE id = ?", (place_id,)
        ).fetchone()
        if prow:
            district = prow["district"]
    elif victim_id is not None:
        vrow = conn.execute(
            """SELECT p.district FROM agents a
               JOIN places p ON p.id = a.home_place_id
               WHERE a.id = ?""",
            (victim_id,),
        ).fetchone()
        if vrow:
            district = vrow["district"]
    if not district:
        return 0.0
    row = conn.execute(
        "SELECT vigilance FROM district_watch WHERE district = ?", (district,)
    ).fetchone()
    if not row:
        return 0.0
    return min(MAX_VIGILANCE_CATCH_BONUS, float(row["vigilance"] or 0.0) * MAX_VIGILANCE_CATCH_BONUS)


def _run_watch_patrols(conn: sqlite3.Connection, tick: int,
                       seed: str, r) -> int:
    """Organize a Neighborhood Watch patrol in districts with recent thefts."""
    day = day_of(tick)
    # Decay vigilance slightly each day
    conn.execute(
        """UPDATE district_watch
           SET vigilance = MAX(0.05, ROUND(vigilance - ?, 3))""",
        (VIGILANCE_DECAY,),
    )
    recent_thefts = conn.execute(
        """SELECT COALESCE(p.district, hp.district) AS target_district, COUNT(*) AS cnt
           FROM events e
           LEFT JOIN places p ON p.id = e.place_id
           LEFT JOIN agents a ON a.id = e.a_id
           LEFT JOIN places hp ON hp.id = a.home_place_id
           WHERE e.day >= ?
             AND (e.data LIKE '%"theft"%' OR e.data LIKE '%"arrest"%')
           GROUP BY target_district
           ORDER BY cnt DESC""",
        (max(0, day - 3),),
    ).fetchall()

    patrols = 0
    for row in recent_thefts:
        dist = row["target_district"]
        if not dist or dist not in DISTRICTS:
            continue
        dw = conn.execute(
            "SELECT vigilance, last_event_day FROM district_watch WHERE district = ?",
            (dist,),
        ).fetchone()
        if dw and int(dw["last_event_day"] or -1) == day:
            continue
        captain = conn.execute(
            """SELECT a.id, a.name, a.home_place_id
               FROM agents a JOIN places p ON p.id = a.home_place_id
               WHERE a.alive = 1 AND a.is_child = 0 AND p.district = ?
               ORDER BY COALESCE(a.standing, 0) DESC, a.id ASC LIMIT 1""",
            (dist,),
        ).fetchone()
        if not captain:
            continue
        conn.execute(
            """UPDATE district_watch
               SET vigilance = MIN(1.0, ROUND(vigilance + ?, 3)),
                   cohesion = MIN(1.0, ROUND(cohesion + 0.04, 3)),
                   last_event_day = ?, last_event_kind = 'watch_patrol'
               WHERE district = ?""",
            (VIGILANCE_PATROL_BUMP, day, dist),
        )
        emit(conn, tick, "life_event", place_id=captain["home_place_id"],
             a=int(captain["id"]), importance=MINOR,
             text=f"organized an evening Neighborhood Watch patrol across {dist}",
             tag="watch_patrol", district=dist)
        remember(conn, int(captain["id"]), tick,
                 f"I led a Neighborhood Watch patrol in {dist} to keep our streets safe.",
                 kind="personal", importance=3)
        patrols += 1
        break
    return patrols


def _run_mutual_aid(conn: sqlite3.Connection, tick: int,
                    seed: str, r) -> int:
    """Strictly conserved neighbour-to-neighbour mutual aid within a district."""
    day = day_of(tick)
    needy = conn.execute(
        """SELECT a.id, a.name, a.home_place_id, p.district, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents < 3500
           ORDER BY s.money_cents ASC, a.id ASC LIMIT 5"""
    ).fetchall()
    if not needy or r.random() >= 0.35:
        return 0

    recipient = needy[0]
    dist = recipient["district"]
    rid = int(recipient["id"])
    donors = conn.execute(
        """SELECT a.id, a.name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1 AND a.is_child = 0
             AND p.district = ? AND a.id != ?
             AND s.money_cents >= 250000
           ORDER BY s.money_cents DESC, a.id ASC LIMIT 5""",
        (dist, rid),
    ).fetchall()
    if not donors:
        return 0

    donor = r.choice(donors)
    did = int(donor["id"])
    gift = r.randint(MUTUAL_AID_MIN, MUTUAL_AID_MAX)

    # Strictly conserved wallet-to-wallet transfer
    conn.execute(
        "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
        (gift, did),
    )
    conn.execute(
        """UPDATE agent_state
           SET money_cents = money_cents + ?, stress = MAX(0, stress - 10),
               social = MIN(100, social + 8)
           WHERE agent_id = ?""",
        (gift, rid),
    )
    lo, hi = min(did, rid), max(did, rid)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,6.0,8.0,0.0,'acquaintance',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 3.0),
             affinity = MIN(100.0, relationships.affinity + 6.0),
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )
    conn.execute(
        """UPDATE district_watch
           SET cohesion = MIN(1.0, ROUND(cohesion + 0.05, 3)),
               last_event_day = ?, last_event_kind = 'mutual_aid'
           WHERE district = ?""",
        (day, dist),
    )
    emit(conn, tick, "life_event", place_id=recipient["home_place_id"],
         a=did, b=rid, importance=MINOR,
         text=f"shared a ${gift / 100:,.0f} {dist} mutual-aid pantry basket with neighbour {recipient['name']}",
         tag="mutual_aid", district=dist, amount_cents=gift)
    remember(conn, rid, tick,
             f"{donor['name']} dropped off a mutual-aid basket when money was tight.",
             kind="personal", importance=4)
    return 1


def _run_block_party(conn: sqlite3.Connection, tick: int,
                     seed: str, r) -> int:
    """On Saturdays (`day % 7 == 5`), one district hosts a block party."""
    day = day_of(tick)
    if day % 7 != 5:
        return 0
    dist = DISTRICTS[(day // 7) % len(DISTRICTS)]
    residents = conn.execute(
        """SELECT a.id, a.name, a.home_place_id
           FROM agents a JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1 AND a.is_child = 0 AND p.district = ?
           ORDER BY COALESCE(a.standing, 0) DESC, a.id ASC""",
        (dist,),
    ).fetchall()
    if len(residents) < 2:
        return 0

    host = residents[0]
    guest = residents[min(1, len(residents) - 1)]
    rids = [int(x["id"]) for x in residents]
    ph = ",".join("?" * len(rids))
    conn.execute(
        f"""UPDATE agent_state
            SET social = MIN(100, social + 6),
                fun = MIN(100, fun + 6),
                stress = MAX(0, stress - 5)
            WHERE agent_id IN ({ph})""",
        rids,
    )
    lo, hi = min(int(host["id"]), int(guest["id"])), max(int(host["id"]), int(guest["id"]))
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,8.0,8.0,0.0,'friend',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 4.0),
             affinity = MIN(100.0, relationships.affinity + 5.0),
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )
    conn.execute(
        """UPDATE district_watch
           SET cohesion = MIN(1.0, ROUND(cohesion + 0.06, 3)),
               last_event_day = ?, last_event_kind = 'block_party'
           WHERE district = ?""",
        (day, dist),
    )
    emit(conn, tick, "life_event", place_id=host["home_place_id"],
         a=int(host["id"]), b=int(guest["id"]), importance=NOTABLE,
         text=f"hosted the {dist} Saturday block party & porch swap with {len(rids)} neighbours",
         tag="block_party", district=dist)
    remember(conn, int(host["id"]), tick,
             f"Hosted our {dist} Saturday block party with the neighbours.",
             kind="personal", importance=3)
    return 1


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    """Daily neighborhood watch, mutual aid, and Saturday block parties."""
    ensure_districts(conn)
    day = day_of(tick)
    r = rng_for(seed, "neighborhood", day)
    patrols = _run_watch_patrols(conn, tick, seed, r)
    aid = _run_mutual_aid(conn, tick, seed, r)
    parties = _run_block_party(conn, tick, seed, r)
    return {"watch_patrols": patrols, "mutual_aid": aid, "block_parties": parties}


def neighborhood_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_districts(conn)
    rows = conn.execute(
        """SELECT dw.district, dw.vigilance, dw.cohesion,
                  dw.last_event_day, dw.last_event_kind,
                  COUNT(a.id) AS residents
           FROM district_watch dw
           LEFT JOIN places p ON p.district = dw.district AND p.kind = 'home'
           LEFT JOIN agents a ON a.home_place_id = p.id AND a.alive = 1
           GROUP BY dw.district
           ORDER BY dw.cohesion DESC, dw.vigilance DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
