"""Apprentice Guilds & Cooperative Workshops (v0.33).

Artisans, mentors, and apprentices across Miniville's signature crafts organize
into six chartered **Craft Guilds**:

1. *The Riverbend Woodworkers' Guild* (`carpentry` / `woodworking`)
2. *The Hearthstone Bakers' Guild* (`baking` / `cooking`)
3. *The Lantern & Quill Writers' Guild* (`writing` / `poetry`)
4. *The Greenhill Painters' Circle* (`painting` / `illustration`)
5. *The Porchlight Musicians' Guild* (`music` / `guitar`)
6. *The Meadow Botanists' Guild* (`gardening` / `botany`)

Every week, guilds enroll matching residents (`ensure_guilds`) and hold a
workshop night (`run_guild_workshops`): a senior Guild Master/Patron (`money_cents >= 240_000`)
sponsors a junior or apprentice member with a strictly conserved `$28` (`2,800`
cents) wallet-to-wallet **Guild Masterpiece Grant**, commissioning a collaborative
masterpiece keepsake exhibited in the Cultural Commons (`guild_masterpiece`).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import get_meta
from .events import NOTABLE, emit
from .individual import _parse_json_list, profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

GUILD_STIPEND_CENTS = 2_800       # $28 conserved grant from Guild Patron to Junior Artisan
PATRON_MIN_WALLET_CENTS = 240_000

_GUILD_SPECS: tuple[tuple[str, str, set[str], str], ...] = (
    (
        "woodworking",
        "The Riverbend Woodworkers' Guild",
        {"woodworking", "carpentry", "repair", "diy", "construction"},
        "Flats Auto & Hardware",
    ),
    (
        "baking",
        "The Hearthstone Bakers' Guild",
        {"baking", "pastry", "pastry baking", "cooking", "bread"},
        "Fresh Crust Bakery",
    ),
    (
        "writing",
        "The Lantern & Quill Writers' Guild",
        {"writing", "creative writing", "poetry", "reading", "history", "storytelling"},
        "Miniville Public Library",
    ),
    (
        "painting",
        "The Greenhill Painters' Circle",
        {"painting", "illustration", "drawing", "photography", "art", "pottery"},
        "Miniville Community Center",
    ),
    (
        "music",
        "The Porchlight Musicians' Guild",
        {"music", "guitar", "piano", "singing", "choir"},
        "The Quaint Corner Tavern",
    ),
    (
        "gardening",
        "The Meadow Botanists' Guild",
        {"gardening", "botany", "plants", "hiking", "birdwatching"},
        "Lush Meadow Park",
    ),
)


def ensure_guilds(conn: sqlite3.Connection, tick: int = 0,
                  seed: str = "miniville") -> int:
    """Charter the 6 town Craft Guilds and enroll matching artisans."""
    places_by_name = {
        r["name"]: int(r["id"])
        for r in conn.execute("SELECT id, name FROM places").fetchall()
    }
    fallback_hall = next(iter(places_by_name.values()), None)

    for craft, gname, _kws, venue_name in _GUILD_SPECS:
        hall_id = places_by_name.get(venue_name, fallback_hall)
        conn.execute(
            """INSERT OR IGNORE INTO guilds(
                   craft, name, hall_place_id, members_count,
                   patronage_cents, masterpieces_count, founded_tick)
               VALUES(?, ?, ?, 0, 0, 0, ?)""",
            (craft, gname, hall_id, tick),
        )

    guild_rows = {
        r["craft"]: dict(r)
        for r in conn.execute("SELECT * FROM guilds").fetchall()
    }
    existing_members = {
        (int(r["guild_id"]), int(r["agent_id"]))
        for r in conn.execute("SELECT guild_id, agent_id FROM guild_members").fetchall()
    }

    adults = conn.execute(
        """SELECT id, name, age, hobbies_json, skills_json, COALESCE(standing, 0) AS standing
           FROM agents WHERE alive = 1 AND is_child = 0 ORDER BY id"""
    ).fetchall()

    added = 0
    for a in adults:
        aid = int(a["id"])
        prof = profile_of(conn, aid, seed)
        tokens = {str(prof.get("knack", "")).lower()}
        for h in _parse_json_list(a["hobbies_json"]):
            tokens.add(h.lower())
        for s in _parse_json_list(a["skills_json"]):
            tokens.add(s.lower())

        for craft, _gname, kws, _vname in _GUILD_SPECS:
            g = guild_rows.get(craft)
            if not g:
                continue
            gid = int(g["id"])
            if (gid, aid) in existing_members:
                continue
            if tokens & kws:
                rank = "master" if (int(a["age"] or 30) >= 42 or int(a["standing"]) >= 8) else "journeyman"
                conn.execute(
                    """INSERT OR IGNORE INTO guild_members(guild_id, agent_id, rank, joined_tick)
                       VALUES(?, ?, ?, ?)""",
                    (gid, aid, rank, tick),
                )
                existing_members.add((gid, aid))
                added += 1
                break

    for craft, g in guild_rows.items():
        gid = int(g["id"])
        mems = conn.execute(
            """SELECT gm.agent_id, gm.rank, a.age, COALESCE(a.standing, 0) AS standing
               FROM guild_members gm
               JOIN agents a ON a.id = gm.agent_id
               WHERE gm.guild_id = ? AND a.alive = 1
               ORDER BY (CASE WHEN gm.rank = 'master' THEN 0 ELSE 1 END),
                        COALESCE(a.standing, 0) DESC, a.age DESC, a.id ASC""",
            (gid,),
        ).fetchall()
        master_id = int(mems[0]["agent_id"]) if mems else None
        conn.execute(
            "UPDATE guilds SET members_count = ?, master_id = ? WHERE id = ?",
            (len(mems), master_id, gid),
        )
    return added


def run_guild_workshops(conn: sqlite3.Connection, tick: int,
                        seed: str = "miniville") -> dict[str, int]:
    """Weekly cooperative guild workshop & conserved Masterpiece Grant."""
    day = day_of(tick)
    enrolled = ensure_guilds(conn, tick, seed)
    if day % 7 != 3:
        return {"enrolled": enrolled, "masterpieces": 0, "patronage_cents": 0}

    r = rng_for(seed, "guild_workshop", day)
    guilds = conn.execute(
        "SELECT * FROM guilds WHERE members_count >= 2 ORDER BY id"
    ).fetchall()
    if not guilds:
        return {"enrolled": enrolled, "masterpieces": 0, "patronage_cents": 0}

    chosen_guild = r.choice(guilds)
    gid = int(chosen_guild["id"])
    gname = chosen_guild["name"]
    craft = chosen_guild["craft"]
    hall_id = chosen_guild["hall_place_id"]

    mems = conn.execute(
        """SELECT gm.agent_id, gm.rank, a.name, a.age, s.money_cents
           FROM guild_members gm
           JOIN agents a ON a.id = gm.agent_id
           JOIN agent_state s ON s.agent_id = a.id
           WHERE gm.guild_id = ? AND a.alive = 1
           ORDER BY s.money_cents DESC, a.id ASC""",
        (gid,),
    ).fetchall()
    if len(mems) < 2:
        return {"enrolled": enrolled, "masterpieces": 0, "patronage_cents": 0}

    patron = mems[0]
    recipient = mems[-1]
    pid = int(patron["agent_id"])
    rid = int(recipient["agent_id"])
    if pid == rid or int(patron["money_cents"]) < PATRON_MIN_WALLET_CENTS:
        return {"enrolled": enrolled, "masterpieces": 0, "patronage_cents": 0}

    conn.execute(
        "UPDATE agent_state SET money_cents = money_cents - ?, fun = MIN(100, fun + 6) WHERE agent_id = ?",
        (GUILD_STIPEND_CENTS, pid),
    )
    conn.execute(
        "UPDATE agent_state SET money_cents = money_cents + ?, fun = MIN(100, fun + 10), stress = MAX(0, stress - 6) WHERE agent_id = ?",
        (GUILD_STIPEND_CENTS, rid),
    )

    title = f"{gname} Masterpiece by {recipient['name']} & {patron['name']}"
    hist = json.dumps([
        {
            "event": "created",
            "by": recipient["name"],
            "guild": gname,
            "patron": patron["name"],
            "grant_cents": GUILD_STIPEND_CENTS,
            "tick": tick,
        }
    ])
    conn.execute(
        """INSERT INTO keepsakes(
               owner_id, creator_id, venue_id, kind, title, craft,
               created_tick, generations, appreciations, history_json)
           VALUES(?, ?, ?, 'craft', ?, ?, ?, 1, 2, ?)""",
        (rid, rid, hall_id, title, craft, tick, hist),
    )
    conn.execute(
        """UPDATE guilds
           SET patronage_cents = patronage_cents + ?,
               masterpieces_count = masterpieces_count + 1
           WHERE id = ?""",
        (GUILD_STIPEND_CENTS, gid),
    )
    lo, hi = min(pid, rid), max(pid, rid)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,8.0,10.0,0.0,'friend',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 4.0),
             affinity = MIN(100.0, relationships.affinity + 5.0),
             interactions = relationships.interactions + 1,
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )
    text = (
        f"completed a guild masterpiece for {gname} with a "
        f"${GUILD_STIPEND_CENTS / 100:,.0f} fellowship grant from {patron['name']}"
    )
    emit(conn, tick, "life_event", place_id=hall_id, a=rid, b=pid,
         importance=NOTABLE, text=text, tag="guild_masterpiece",
         guild=gname, grant_cents=GUILD_STIPEND_CENTS)
    remember(conn, rid, tick,
             f"With {patron['name']}'s ${GUILD_STIPEND_CENTS / 100:,.0f} grant at {gname}, I crafted '{title}'!",
             kind="reflection", importance=4)
    remember(conn, pid, tick,
             f"Sponsored {recipient['name']}'s guild masterpiece at {gname}.",
             kind="personal", importance=3)
    return {
        "enrolled": enrolled,
        "masterpieces": 1,
        "patronage_cents": GUILD_STIPEND_CENTS,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    return run_guild_workshops(conn, tick, seed)


def list_guilds(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_guilds(conn, int(get_meta(conn, "tick", "0") or 0),
                  get_meta(conn, "seed", "miniville") or "miniville")
    rows = conn.execute(
        """SELECT g.*, a.name AS master_name, p.name AS hall_name
           FROM guilds g
           LEFT JOIN agents a ON a.id = g.master_id
           LEFT JOIN places p ON p.id = g.hall_place_id
           ORDER BY g.members_count DESC, g.id ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


def guilds_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    items = list_guilds(conn)
    return {
        "total_guilds": len(items),
        "total_members": sum(int(g["members_count"] or 0) for g in items),
        "total_masterpieces": sum(int(g["masterpieces_count"] or 0) for g in items),
        "total_patronage_cents": sum(int(g["patronage_cents"] or 0) for g in items),
        "guilds": items,
    }
