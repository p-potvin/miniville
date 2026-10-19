"""Personal keepsakes, craftsman creations, and family heirlooms.

Inhabitants in a living town do not merely experience fleeting ticks — they
make, gift, and inherit tangible personal works with a history (`provenance`):

* **Craftsman Creations (`craft_keepsake` & `daily_keepsakes`)** — residents
  create named personal artifacts tied to their signature `knack`, hobbies, and
  town landmarks (e.g., *"a carved walnut mantle clock"*, *"a watercolour of
  Lakeshore Marina"*, *"a sourdough starter & recipe ledger"*, *"a chapbook of
  Old Mill poems"*).
* **Gifting (`gift_keepsakes`)** — generous or close-knit residents gift a
  treasured keepsake to a spouse, sweetheart, close friend, or graduating
  apprentice, recording the transfer in the item's `history_json` and warming
  their bond.
* **Family Heirlooms (`inherit_keepsakes`)** — when a resident dies, their
  keepsakes pass to their surviving spouse, household kin, or closest living
  friend, incrementing `generations += 1` so multi-generational family
  heirlooms accumulate across simulated years.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import get_meta
from .events import MINOR, NOTABLE, emit
from .individual import _parse_json_list, profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

MAX_KEEPSAKES_PER_AGENT = 4
P_DAILY_CRAFT = 0.06
P_DAILY_GIFT = 0.08

_CRAFT_TEMPLATES: list[tuple[set[str], str, tuple[str, ...]]] = [
    (
        {"carpentry", "woodworking", "repair", "diy", "construction", "furniture"},
        "woodwork",
        (
            "a carved walnut rocking chair",
            "a hand-joined cedar chest",
            "an inlaid oak mantle clock",
            "a turned maple serving bowl",
            "a custom birch bookshelf",
        ),
    ),
    (
        {"painting", "drawing", "art", "photography", "design", "pottery", "crafts"},
        "artwork",
        (
            "a watercolour of Lakeshore Marina",
            "an oil sketch of Old Mill Quarter",
            "a glazed stoneware pitcher",
            "a framed portrait of Lush Meadow Park",
            "a charcoal study of the town square",
        ),
    ),
    (
        {"writing", "poetry", "storytelling", "reading", "history", "journalism"},
        "manuscript",
        (
            "a chapbook of Old Mill poems",
            "a leather-bound Miniville chronicle journal",
            "a collection of fireside short stories",
            "an annotated local history notebook",
        ),
    ),
    (
        {"baking", "cooking", "pastry", "bread", "gardening"},
        "recipe",
        (
            "a handwritten family recipe ledger",
            "a prized sourdough starter & baking stone",
            "a jar of spiced Greenhill orchard preserves",
            "an heirloom seed & herb tin",
        ),
    ),
    (
        {"music", "guitar", "piano", "singing", "choir"},
        "music",
        (
            "a hand-notated acoustic ballad sheet",
            "a refurbished parlour acoustic guitar",
            "a songbook of Lakeshore folk tunes",
        ),
    ),
    (
        {"knitting", "sewing", "fashion", "tailoring", "textiles"},
        "garment",
        (
            "a hand-stitched patchwork quilt",
            "a cable-knit winter woollen shawl",
            "an embroidered linen table runner",
        ),
    ),
]

_FALLBACK_ITEMS: tuple[tuple[str, str], ...] = (
    ("timepiece", "an engraved brass pocket watch"),
    ("woodwork", "a hand-carved walking stick"),
    ("artwork", "a pressed-wildflower frame from Lush Meadow"),
    ("manuscript", "a leather-bound commonplace book"),
)


def _pick_template(craft: str, hobbies: list[str], r) -> tuple[str, str]:
    if craft == "timepiece":
        return ("timepiece", "an engraved retirement pocket watch")
    tokens = {craft.lower()}
    for w in craft.lower().split():
        tokens.add(w.strip(",."))
    for h in hobbies:
        tokens.add(h.lower())
        for w in h.lower().split():
            tokens.add(w.strip(",."))
    for kws, kind, titles in _CRAFT_TEMPLATES:
        if tokens & kws:
            return kind, r.choice(titles)
    return r.choice(_FALLBACK_ITEMS)


def craft_keepsake(conn: sqlite3.Connection, agent_id: int, tick: int,
                   seed: str = "miniville", *,
                   craft_override: str | None = None) -> int | None:
    """Create a new keepsake for `agent_id` if they are under the per-agent cap."""
    agent = conn.execute(
        "SELECT id, name, hobbies_json, skills_json, alive FROM agents WHERE id = ?",
        (agent_id,),
    ).fetchone()
    if not agent or not agent["alive"]:
        return None
    owned = conn.execute(
        "SELECT COUNT(*) n FROM keepsakes WHERE owner_id = ?", (agent_id,)
    ).fetchone()["n"]
    if owned >= MAX_KEEPSAKES_PER_AGENT:
        return None

    prof = profile_of(conn, agent_id, seed)
    craft = craft_override or str(prof.get("knack") or "craftsmanship")
    hobbies = _parse_json_list(agent["hobbies_json"])
    r = rng_for(seed, "keepsake_craft", agent_id, tick)
    kind, title = _pick_template(craft, hobbies, r)

    history = [{
        "tick": tick,
        "day": day_of(tick) + 1,
        "action": "crafted",
        "by": agent["name"],
    }]
    cur = conn.execute(
        """INSERT INTO keepsakes(
               title, kind, craft, creator_id, owner_id,
               created_tick, generations, history_json)
           VALUES(?, ?, ?, ?, ?, ?, 0, ?)""",
        (title, kind, craft, agent_id, agent_id, tick, json.dumps(history)),
    )
    kid = int(cur.lastrowid or 0)
    conn.execute(
        """UPDATE agent_state
           SET fun = MIN(100, fun + 8), stress = MAX(0, stress - 6)
           WHERE agent_id = ?""",
        (agent_id,),
    )
    emit(conn, tick, "life_event", a=agent_id, importance=MINOR,
         text=f"crafted {title} ({craft})",
         tag="keepsake_crafted", keepsake_id=kid, title=title, item_kind=kind)
    remember(conn, agent_id, tick,
             f"I finished crafting {title}.",
             kind="personal", importance=3)
    return kid


def gift_keepsake(conn: sqlite3.Connection, giver_id: int, recipient_id: int,
                  tick: int, reason: str = "as a token of affection") -> bool:
    """Transfer one of `giver_id`'s keepsakes to `recipient_id`."""
    row = conn.execute(
        """SELECT k.*, g.name AS giver_name, rc.name AS rec_name
           FROM keepsakes k
           JOIN agents g ON g.id = k.owner_id
           JOIN agents rc ON rc.id = ?
           WHERE k.owner_id = ? AND g.alive = 1 AND rc.alive = 1
           ORDER BY k.generations DESC, k.id ASC LIMIT 1""",
        (recipient_id, giver_id),
    ).fetchone()
    if not row:
        return False
    try:
        hist = json.loads(row["history_json"] or "[]")
    except Exception:
        hist = []
    hist.append({
        "tick": tick,
        "day": day_of(tick) + 1,
        "action": "gifted",
        "from": row["giver_name"],
        "to": row["rec_name"],
    })
    conn.execute(
        "UPDATE keepsakes SET owner_id = ?, history_json = ? WHERE id = ?",
        (recipient_id, json.dumps(hist), row["id"]),
    )
    lo, hi = min(giver_id, recipient_id), max(giver_id, recipient_id)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?,?,10.0,12.0,0.0,'friend',1,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MIN(100.0, relationships.familiarity + 3.0),
             affinity = MIN(100.0, relationships.affinity + 6.0),
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, tick),
    )
    conn.execute(
        """UPDATE agent_state
           SET fun = MIN(100, fun + 8), social = MIN(100, social + 8),
               stress = MAX(0, stress - 5)
           WHERE agent_id IN (?, ?)""",
        (giver_id, recipient_id),
    )
    emit(conn, tick, "life_event", a=giver_id, b=recipient_id, importance=MINOR,
         text=f"gifted {row['title']} to {row['rec_name']} {reason}",
         tag="keepsake_gifted", keepsake_id=row["id"], title=row["title"])
    remember(conn, giver_id, tick,
             f"I gifted {row['title']} to {row['rec_name']}.",
             kind="personal", importance=3)
    remember(conn, recipient_id, tick,
             f"{row['giver_name']} gifted me {row['title']}.",
             kind="personal", importance=4)
    return True


def inherit_keepsakes(conn: sqlite3.Connection, deceased: sqlite3.Row,
                      survivor: int | None, tick: int) -> int:
    """Pass any keepsakes owned by `deceased` to their heir as family heirlooms."""
    aid = int(deceased["id"])
    items = conn.execute(
        "SELECT * FROM keepsakes WHERE owner_id = ? ORDER BY id", (aid,)
    ).fetchall()
    if not items:
        return 0

    heir_id = survivor
    if heir_id is None and deceased["household_id"] is not None:
        hrow = conn.execute(
            """SELECT id FROM agents
               WHERE household_id = ? AND alive = 1 AND id != ?
               ORDER BY is_child ASC, age DESC, id ASC LIMIT 1""",
            (deceased["household_id"], aid),
        ).fetchone()
        if hrow:
            heir_id = int(hrow["id"])
    if heir_id is None:
        frow = conn.execute(
            """SELECT CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END AS other_id
               FROM relationships r
               JOIN agents a ON a.id = (CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END)
               WHERE (r.a_id = ? OR r.b_id = ?) AND a.alive = 1
               ORDER BY r.affinity DESC, r.familiarity DESC LIMIT 1""",
            (aid, aid, aid, aid),
        ).fetchone()
        if frow:
            heir_id = int(frow["other_id"])
    if heir_id is None:
        return 0

    heir_row = conn.execute(
        "SELECT id, name FROM agents WHERE id = ?", (heir_id,)
    ).fetchone()
    if not heir_row:
        return 0

    passed = 0
    for item in items:
        try:
            hist = json.loads(item["history_json"] or "[]")
        except Exception:
            hist = []
        new_gen = int(item["generations"] or 0) + 1
        hist.append({
            "tick": tick,
            "day": day_of(tick) + 1,
            "action": "inherited",
            "from": deceased["name"],
            "to": heir_row["name"],
            "generation": new_gen,
        })
        conn.execute(
            """UPDATE keepsakes
               SET owner_id = ?, generations = ?, history_json = ?
               WHERE id = ?""",
            (heir_id, new_gen, json.dumps(hist), item["id"]),
        )
        emit(conn, tick, "life_event", a=heir_id, b=aid, importance=NOTABLE,
             text=f"inherited {deceased['name']}'s {item['title']} (generation {new_gen} heirloom)",
             tag="heirloom_inherited", keepsake_id=item["id"],
             title=item["title"], generations=new_gen)
        remember(conn, heir_id, tick,
                 f"I inherited {deceased['name']}'s {item['title']} as a family heirloom.",
                 kind="personal", importance=4)
        passed += 1
    return passed


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    """Daily pass: artisans craft keepsakes and close ties exchange gifts."""
    day = day_of(tick)
    r = rng_for(seed, "keepsakes_daily", day)
    crafted = gifted = 0

    # 1. Residents who had a craft_breakthrough or hobby_triumph yesterday may
    #    immortalize it as a tangible keepsake
    if day >= 1:
        recent_creators = conn.execute(
            """SELECT DISTINCT a_id FROM events
               WHERE day = ? AND kind = 'life_event'
                 AND (data LIKE '%"craft_breakthrough"%' OR data LIKE '%"hobby_triumph"%')
                 AND a_id IS NOT NULL
               ORDER BY a_id LIMIT 3""",
            (day - 1,),
        ).fetchall()
        for row in recent_creators:
            if r.random() < 0.45:
                if craft_keepsake(conn, int(row["a_id"]), tick, seed) is not None:
                    crafted += 1

    # 2. Daily artisan creation draw (prioritizing residents with create_masterwork ambition)
    amb_creators = [
        int(x["agent_id"])
        for x in conn.execute(
            "SELECT agent_id FROM ambitions WHERE status='active' AND kind='create_masterwork' ORDER BY agent_id"
        ).fetchall()
    ]
    if amb_creators and r.random() < 0.45:
        aid = r.choice(amb_creators)
        if craft_keepsake(conn, aid, tick, seed) is not None:
            crafted += 1
    elif r.random() < P_DAILY_CRAFT:
        artisans = conn.execute(
            """SELECT a.id FROM agents a
               WHERE a.alive = 1 AND a.is_child = 0
                 AND (SELECT COUNT(*) FROM keepsakes k WHERE k.owner_id = a.id) < ?
               ORDER BY a.id""",
            (MAX_KEEPSAKES_PER_AGENT,),
        ).fetchall()
        if artisans:
            picked = r.choice(artisans)
            if craft_keepsake(conn, int(picked["id"]), tick, seed) is not None:
                crafted += 1

    # 3. Gifting pass: owners with >= 2 keepsakes (or generous owners) gift one
    #    to a spouse, sweetheart, or close friend
    if r.random() < P_DAILY_GIFT:
        owners = conn.execute(
            """SELECT k.owner_id, COUNT(*) AS cnt
               FROM keepsakes k JOIN agents a ON a.id = k.owner_id
               WHERE a.alive = 1
               GROUP BY k.owner_id
               HAVING cnt >= 1
               ORDER BY cnt DESC, k.owner_id ASC LIMIT 15"""
        ).fetchall()
        for ow in owners:
            oid = int(ow["owner_id"])
            partner = conn.execute(
                """SELECT CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END AS tid,
                          r.label
                   FROM relationships r
                   JOIN agents a ON a.id = (CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END)
                   WHERE (r.a_id = ? OR r.b_id = ?) AND a.alive = 1
                     AND r.label IN ('spouse', 'partner', 'sweetheart', 'close_friend')
                   ORDER BY r.affinity DESC, tid ASC LIMIT 1""",
                (oid, oid, oid, oid),
            ).fetchone()
            if partner and gift_keepsake(conn, oid, int(partner["tid"]), tick):
                gifted += 1
                break

    return {"crafted": crafted, "gifted": gifted}


def keepsakes_of(conn: sqlite3.Connection, agent_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT k.*, c.name AS creator_name, o.name AS owner_name
           FROM keepsakes k
           JOIN agents c ON c.id = k.creator_id
           JOIN agents o ON o.id = k.owner_id
           WHERE k.owner_id = ?
           ORDER BY k.generations DESC, k.id DESC""",
        (agent_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["history"] = json.loads(d.pop("history_json", "[]") or "[]")
        except Exception:
            d["history"] = []
        out.append(d)
    return out


def list_keepsakes(conn: sqlite3.Connection, heirlooms_only: bool = False,
                   limit: int = 40) -> list[dict[str, Any]]:
    sql = """SELECT k.*, c.name AS creator_name, o.name AS owner_name
             FROM keepsakes k
             JOIN agents c ON c.id = k.creator_id
             JOIN agents o ON o.id = k.owner_id
             WHERE o.alive = 1"""
    if heirlooms_only:
        sql += " AND k.generations >= 1"
    sql += " ORDER BY k.generations DESC, k.id DESC LIMIT ?"
    rows = conn.execute(sql, (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["history"] = json.loads(d.pop("history_json", "[]") or "[]")
        except Exception:
            d["history"] = []
        out.append(d)
    return out


def create_keepsake(conn: sqlite3.Connection, agent_id: int, tick: int,
                    seed: str = "miniville") -> int:
    """Create a single keepsake for `agent_id` and return its id."""
    row = conn.execute(
        "SELECT id, name, household_id, home_place_id, work_place_id FROM agents WHERE id = ?",
        (agent_id,),
    ).fetchone()
    if not row:
        return 0
    prof = profile_of(conn, agent_id, seed)
    craft = str(prof.get("knack") or "woodworking")
    kind, tmpl = _pick_template(craft, [], rng_for(seed, "create_ks", agent_id, tick))
    title = tmpl.format(name=row["name"])
    hist = json.dumps([{"event": "created", "by": row["name"], "tick": tick}])
    cur = conn.execute(
        """INSERT INTO keepsakes(
               owner_id, creator_id, kind, title,
               craft, created_tick, generations, history_json)
           VALUES(?, ?, ?, ?, ?, ?, 1, ?)""",
        (agent_id, agent_id, kind, title, craft, tick, hist),
    )
    return int(cur.lastrowid)

keepsakes_for_resident = keepsakes_of
