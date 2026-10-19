"""Seasonal town competitions, craft fairs, and Blue-Ribbon honours.

On festival/holiday days (`seasons.holiday_for`) and fortnightly fair
Saturdays (`day % 14 == 12`), Miniville hosts a judged competition where
residents pit their skills, graduated apprenticeships, and crafted keepsakes
against one another:

* **The Riverside Bake-Off & Pie Contest** (`baking`)
* **The Lakeshore Bass & Trout Derby** (`fishing`)
* **The Old Mill Woodworking & Craft Show** (`woodworking`)
* **The Greenhill Prize Harvest & Garden Show** (`gardening`)
* **The Tavern & Bijou Battle of the Bards** (`music`)
* **The Miniville Library Literary Prize** (`literature`)

If the town purse (`town_account`) holds `>= $1,000`, the winner receives a
strictly conserved `$50` Blue-Ribbon Prize (`5,000` cents transferred from
`town_account` to the champion's wallet, keeping `total_money_cents` invariant
to the cent), `+3` standing, and a `competition_won` life event.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .individual import _parse_json_list, profile_of
from .memory import remember
from .rng import rng_for
from .seasons import holiday_for, season_of
from .timekeeper import day_of

BLUE_RIBBON_PRIZE_CENTS = 5_000      # $50 conserved prize from town_account
PURSE_PRIZE_CUSHION = 100_000        # town purse must hold >= $1,000

COMPETITION_SPECS: dict[str, dict[str, Any]] = {
    "baking": {
        "title": "Riverside Bake-Off & Pie Contest",
        "venue": "Riverside Diner",
        "keywords": {"baking", "cooking", "pastry", "bread", "baker", "cook", "chef"},
        "keepsake_kinds": {"recipe"},
    },
    "fishing": {
        "title": "Lakeshore Bass & Trout Derby",
        "venue": "Lakeshore Marina",
        "keywords": {"fishing", "boating", "kayaking", "outdoors", "swimming"},
        "keepsake_kinds": {"woodwork", "timepiece"},
    },
    "woodworking": {
        "title": "Old Mill Woodworking & Craft Show",
        "venue": "Miniville Community Center",
        "keywords": {"woodworking", "carpentry", "repair", "diy", "crafts", "painting", "art"},
        "keepsake_kinds": {"woodwork", "artwork", "garment"},
    },
    "gardening": {
        "title": "Greenhill Prize Harvest & Garden Show",
        "venue": "Lush Meadow Park",
        "keywords": {"gardening", "plants", "botany", "hiking", "birdwatching", "cooking"},
        "keepsake_kinds": {"recipe", "artwork"},
    },
    "music": {
        "title": "Tavern & Bijou Battle of the Bards",
        "venue": "The Quaint Corner Tavern",
        "keywords": {"music", "guitar", "piano", "singing", "choir", "theater"},
        "keepsake_kinds": {"music"},
    },
    "literature": {
        "title": "Miniville Library Literary & Poetry Prize",
        "venue": "Miniville Public Library",
        "keywords": {"writing", "poetry", "reading", "storytelling", "history", "journalist"},
        "keepsake_kinds": {"manuscript"},
    },
}

_SEASON_CATEGORIES: dict[str, tuple[str, ...]] = {
    "spring": ("gardening", "fishing", "baking"),
    "summer": ("fishing", "gardening", "music"),
    "autumn": ("baking", "woodworking", "literature"),
    "winter": ("literature", "music", "woodworking"),
}


def hold_competition(conn: sqlite3.Connection, tick: int,
                     seed: str = "miniville", *,
                     category_override: str | None = None) -> dict[str, Any] | None:
    """Run a judged town competition and record the winner and runner-up."""
    day = day_of(tick)
    r = rng_for(seed, "competition", day)
    season = season_of(day)
    cat = category_override or r.choice(_SEASON_CATEGORIES.get(season, ("baking", "woodworking")))
    spec = COMPETITION_SPECS.get(cat, COMPETITION_SPECS["baking"])
    kws = spec["keywords"]
    ks_kinds = spec["keepsake_kinds"]

    vrow = conn.execute(
        "SELECT id, name FROM places WHERE name = ?", (spec["venue"],)
    ).fetchone()
    venue_id = int(vrow["id"]) if vrow else None
    venue_name = vrow["name"] if vrow else "town"

    # Preload graduated mentorship crafts and keepsakes
    grad_crafts: dict[int, set[str]] = {}
    for m in conn.execute(
        "SELECT apprentice_id, craft FROM mentorships WHERE status = 'graduated'"
    ).fetchall():
        grad_crafts.setdefault(int(m["apprentice_id"]), set()).add(str(m["craft"]).lower())

    past_wins: dict[int, int] = {}
    for pw in conn.execute(
        "SELECT winner_id, COUNT(*) AS cnt FROM competitions WHERE category = ? GROUP BY winner_id",
        (cat,),
    ).fetchall():
        past_wins[int(pw["winner_id"])] = int(pw["cnt"])

    best_keepsake: dict[int, tuple[str, int]] = {}
    for k in conn.execute(
        "SELECT owner_id, title, kind, COALESCE(appreciations, 0) AS app FROM keepsakes"
    ).fetchall():
        if k["kind"] in ks_kinds:
            oid = int(k["owner_id"])
            cur = best_keepsake.get(oid)
            if cur is None or int(k["app"]) > cur[1]:
                best_keepsake[oid] = (k["title"], int(k["app"]))

    adults = conn.execute(
        """SELECT a.id, a.name, a.occupation, a.hobbies_json, a.skills_json,
                  s.energy
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY a.id"""
    ).fetchall()
    if len(adults) < 2:
        return None

    scored: list[tuple[float, sqlite3.Row, str]] = []
    for a in adults:
        aid = int(a["id"])
        prof = profile_of(conn, aid, seed)
        tokens = {str(prof.get("knack") or "").lower(), (a["occupation"] or "").lower()}
        for item in _parse_json_list(a["hobbies_json"]) + _parse_json_list(a["skills_json"]):
            tokens.add(item.lower())
            for w in item.lower().split():
                tokens.add(w.strip(",."))

        match_hits = len(tokens & kws)
        ks = best_keepsake.get(aid)
        if match_hits == 0 and ks is None and len(adults) > 10:
            continue

        score = match_hits * 12.0
        if grad_crafts.get(aid, set()) & kws:
            score += 10.0
        work_title = f"their signature {prof.get('knack', cat)}"
        if ks is not None:
            score += 12.0 + min(15.0, ks[1] * 1.5)
            work_title = ks[0]
        score += float(prof.get("industriousness", 0.0)) * 4.0
        score += float(a["energy"] or 50.0) * 0.05
        score -= past_wins.get(aid, 0) * 9.0
        score += rng_for(seed, "comp_roll", aid, day).uniform(0.0, 26.0)
        scored.append((score, a, work_title))

    if len(scored) < 2:
        for a in adults[:6]:
            aid = int(a["id"])
            scored.append((rng_for(seed, "comp_roll", aid, day).uniform(0.0, 18.0), a, f"their {cat} entry"))

    scored.sort(key=lambda t: (-t[0], int(t[1]["id"])))
    winner_score, winner, win_work = scored[0]
    _ru_score, runner_up, _ru_work = scored[1]
    wid = int(winner["id"])
    ruid = int(runner_up["id"])

    # Conserved Blue-Ribbon Prize from town_account if purse has cushion
    prize = 0
    purse = conn.execute("SELECT balance_cents FROM town_account WHERE id = 1").fetchone()
    if purse and int(purse["balance_cents"] or 0) >= PURSE_PRIZE_CUSHION:
        prize = BLUE_RIBBON_PRIZE_CENTS
        conn.execute(
            "UPDATE town_account SET balance_cents = balance_cents - ? WHERE id = 1",
            (prize,),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (prize, wid),
        )

    cur = conn.execute(
        """INSERT INTO competitions(
               day, tick, title, category, venue_id, winner_id,
               runner_up_id, entrants, prize_cents, winning_work)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, spec["title"], cat, venue_id, wid, ruid,
         len(scored), prize, win_work),
    )
    comp_id = int(cur.lastrowid or 0)

    conn.execute(
        """UPDATE agent_state
           SET fun = MIN(100, fun + 18), stress = MAX(0, stress - 10),
               mood = 'content'
           WHERE agent_id = ?""",
        (wid,),
    )
    lo, hi = min(wid, ruid), max(wid, ruid)
    rel = conn.execute(
        "SELECT affinity FROM relationships WHERE a_id = ? AND b_id = ?", (lo, hi)
    ).fetchone()
    if rel and float(rel["affinity"] or 0.0) < 0:
        conn.execute(
            "UPDATE relationships SET affinity = MAX(-100.0, affinity - 8.0) WHERE a_id = ? AND b_id = ?",
            (lo, hi),
        )
    else:
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,6.0,5.0,0.0,'acquaintance',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.0),
                 affinity = MIN(100.0, relationships.affinity + 4.0),
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )

    prize_txt = f" and a ${prize / 100:,.0f} Blue-Ribbon prize" if prize else ""
    emit(conn, tick, "life_event", place_id=venue_id, a=wid, b=ruid,
         importance=NOTABLE,
         text=f"won the {spec['title']} at {venue_name} with {win_work} (runner-up: {runner_up['name']}){prize_txt}",
         tag="competition_won", competition_id=comp_id, category=cat, prize_cents=prize)
    remember(conn, wid, tick,
             f"I won the Blue Ribbon at the {spec['title']} with {win_work}!",
             kind="reflection", importance=4)
    remember(conn, ruid, tick,
             f"Placed runner-up behind {winner['name']} at the {spec['title']}.",
             kind="personal", importance=3)

    return {
        "id": comp_id,
        "title": spec["title"],
        "category": cat,
        "winner_id": wid,
        "winner_name": winner["name"],
        "runner_up_id": ruid,
        "runner_up_name": runner_up["name"],
        "entrants": len(scored),
        "prize_cents": prize,
        "winning_work": win_work,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    """Run a competition on holiday/festival days and every second Saturday."""
    day = day_of(tick)
    if day == 0:
        return {"held": 0}
    is_fair_day = (day % 14 == 12) or (holiday_for(conn, day) is not None)
    if not is_fair_day:
        return {"held": 0}
    res = hold_competition(conn, tick, seed)
    return {"held": 1 if res else 0}


def ribbons_of(conn: sqlite3.Connection, agent_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT c.*, p.name AS venue_name
           FROM competitions c
           LEFT JOIN places p ON p.id = c.venue_id
           WHERE c.winner_id = ?
           ORDER BY c.day DESC""",
        (agent_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["day"] = int(d["day"]) + 1
        out.append(d)
    return out


def list_competitions(conn: sqlite3.Connection,
                      limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT c.*, w.name AS winner_name, ru.name AS runner_up_name,
                  p.name AS venue_name
           FROM competitions c
           JOIN agents w ON w.id = c.winner_id
           LEFT JOIN agents ru ON ru.id = c.runner_up_id
           LEFT JOIN places p ON p.id = c.venue_id
           ORDER BY c.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["day"] = int(d["day"]) + 1
        out.append(d)
    return out
