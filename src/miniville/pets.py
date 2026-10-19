"""Household pet companions, dog-walking encounters, and lost-pet reunions (v0.28).

Small-town households often share their homes with beloved dogs and cats:

* **Household Pets (`pets` table)** — ~35% of households adopt a dog or cat
  (`ensure_pets` / `adopt_pets`), prioritizing single/widowed residents and
  families with children. Each pet has a name, species (`dog` or `cat`), breed,
  and endearing quirk.
* **Daily Pet Comfort & Dog-Walking (`due`)** — pets give household members a
  gentle daily morale lift (+3 social, +3 fun, -2 stress). Residents with dogs
  who visit parks or outdoors meet other dog walkers (`pet_encounter`, warming
  familiarity +4.0 and affinity +5.0). Occasionally a curious pet wanders off
  and a neighbour brings them safely home (`lost_pet_returned`, +7.0 affinity).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_meta
from .events import MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

TARGET_PET_HOUSEHOLD_RATIO = 0.35

_DOG_BREEDS = (
    "Golden Retriever",
    "Border Collie",
    "Beagle",
    "Labrador Mix",
    "Corgi",
    "Australian Shepherd",
    "Terrier Mix",
    "Spaniel",
)

_CAT_BREEDS = (
    "Orange Tabby",
    "Tuxedo Cat",
    "Calico",
    "Maine Coon Mix",
    "Russian Blue",
    "Tortoiseshell",
    "Siamese Mix",
)

_PET_NAMES = (
    "Barnaby", "Biscuit", "Clover", "Maple", "Pepper", "Waffles", "Hazel",
    "Milo", "Sadie", "Otis", "Poppy", "Rosie", "Buster", "Toby", "Willow",
    "Chester", "Daisy", "Finn", "Ginger", "Louie", "Mochi", "Nutmeg", "Penny",
    "Scout", "Ziggy", "Pippin", "Pickles", "Sunny", "Bruno", "olive",
)

_DOG_QUIRKS = (
    "carries a favorite pinecone on every walk",
    "greets every neighbour at the fence with a full-body wag",
    "trots proudly beside bicycles on Main Street",
    "naps on the sunny front porch steps all afternoon",
    "knows the sound of the bakery door opening from two blocks away",
    "chases autumn leaves across Lush Meadow Park",
)

_CAT_QUIRKS = (
    "watches the street from the front parlour windowsill",
    "curls up beside anyone reading a book in the evening",
    "patrols the garden beds for field mice at dusk",
    "chirps softly at songbirds from the porch rail",
    "claims the warmest chair in the kitchen every morning",
    "follows their favourite human from room to room like a shadow",
)


def ensure_pets(conn: sqlite3.Connection, tick: int = 0,
                seed: str = "miniville") -> int:
    """Ensure roughly `TARGET_PET_HOUSEHOLD_RATIO` of living households have a pet."""
    hhs = conn.execute(
        """SELECT h.id AS hid, h.name AS hname, h.home_place_id,
                  COUNT(a.id) AS n_members,
                  SUM(CASE WHEN a.is_child = 1 THEN 1 ELSE 0 END) AS n_kids
           FROM households h
           JOIN agents a ON a.household_id = h.id AND a.alive = 1
           GROUP BY h.id
           ORDER BY h.id"""
    ).fetchall()
    if not hhs:
        return 0

    existing_hhs = {
        int(r["household_id"])
        for r in conn.execute("SELECT household_id FROM pets").fetchall()
    }
    target_count = max(1, int(round(len(hhs) * TARGET_PET_HOUSEHOLD_RATIO)))
    need = target_count - len(existing_hhs)
    if need <= 0:
        return 0

    # Prioritize families with kids and single-resident households
    candidates = [h for h in hhs if int(h["hid"]) not in existing_hhs]
    candidates.sort(
        key=lambda h: (
            0 if int(h["n_kids"] or 0) > 0 or int(h["n_members"] or 0) == 1 else 1,
            int(h["hid"]),
        )
    )

    adopted = 0
    for hh in candidates:
        if adopted >= need:
            break
        hid = int(hh["hid"])
        r = rng_for(seed, "pet_init", hid)
        if r.random() > 0.65 and (len(candidates) - adopted) > need:
            continue
        owner_row = conn.execute(
            """SELECT id, name FROM agents
               WHERE household_id = ? AND alive = 1 AND is_child = 0
               ORDER BY id ASC LIMIT 1""",
            (hid,),
        ).fetchone()
        if not owner_row:
            owner_row = conn.execute(
                "SELECT id, name FROM agents WHERE household_id = ? AND alive = 1 ORDER BY id ASC LIMIT 1",
                (hid,),
            ).fetchone()
        if not owner_row:
            continue
        species = "dog" if r.random() < 0.58 else "cat"
        breed = r.choice(_DOG_BREEDS if species == "dog" else _CAT_BREEDS)
        quirk = r.choice(_DOG_QUIRKS if species == "dog" else _CAT_QUIRKS)
        name = r.choice(_PET_NAMES).capitalize()
        conn.execute(
            """INSERT OR IGNORE INTO pets(
                   household_id, owner_id, name, species, breed, quirk, adopted_tick)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (hid, int(owner_row["id"]), name, species, breed, quirk, tick),
        )
        adopted += 1
    return adopted


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    """Daily pet comfort, park dog-walker socializing, and lost-pet returns."""
    day = day_of(tick)
    adopted = ensure_pets(conn, tick, seed)

    pet_rows = conn.execute(
        """SELECT p.*, a.name AS owner_name, a.alive AS owner_alive,
                  h.home_place_id
           FROM pets p
           JOIN households h ON h.id = p.household_id
           LEFT JOIN agents a ON a.id = p.owner_id
           ORDER BY p.id"""
    ).fetchall()
    if not pet_rows:
        return {"adopted": adopted, "comforted": 0, "walks": 0, "reunions": 0}

    # Reassign pet owner if previous owner passed away, or remove if household empty
    active_pets = []
    for pr in pet_rows:
        hid = int(pr["household_id"])
        if not pr["owner_alive"]:
            new_owner = conn.execute(
                "SELECT id, name FROM agents WHERE household_id = ? AND alive = 1 ORDER BY is_child ASC, id ASC LIMIT 1",
                (hid,),
            ).fetchone()
            if new_owner:
                conn.execute(
                    "UPDATE pets SET owner_id = ? WHERE id = ?",
                    (int(new_owner["id"]), int(pr["id"])),
                )
                active_pets.append({**dict(pr), "owner_id": int(new_owner["id"]), "owner_name": new_owner["name"]})
            else:
                conn.execute("DELETE FROM pets WHERE id = ?", (int(pr["id"]),))
        else:
            active_pets.append(dict(pr))

    if not active_pets:
        return {"adopted": adopted, "comforted": 0, "walks": 0, "reunions": 0}

    pet_hhs = [int(p["household_id"]) for p in active_pets]
    ph = ",".join("?" * len(pet_hhs))
    member_rows = conn.execute(
        f"SELECT id FROM agents WHERE alive = 1 AND household_id IN ({ph})",
        pet_hhs,
    ).fetchall()
    member_ids = [int(r["id"]) for r in member_rows]
    if member_ids:
        mph = ",".join("?" * len(member_ids))
        conn.execute(
            f"""UPDATE agent_state
                SET social = MIN(100, social + 3),
                    fun = MIN(100, fun + 3),
                    stress = MAX(0, stress - 2)
                WHERE agent_id IN ({mph})""",
            member_ids,
        )

    r = rng_for(seed, "pets_daily", day)
    walks = reunions = 0

    # Park dog-walking encounter between two dog owners
    dog_pets = [p for p in active_pets if p["species"] == "dog"]
    if len(dog_pets) >= 2 and r.random() < 0.45:
        p1, p2 = r.sample(dog_pets, 2)
        a1, a2 = int(p1["owner_id"]), int(p2["owner_id"])
        if a1 != a2:
            park = conn.execute(
                "SELECT id, name FROM places WHERE name = 'Lush Meadow Park' LIMIT 1"
            ).fetchone()
            pid = int(park["id"]) if park else p1["home_place_id"]
            pname = park["name"] if park else "the park"
            lo, hi = min(a1, a2), max(a1, a2)
            conn.execute(
                """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                       label,interactions,last_met_tick)
                   VALUES(?,?,6.0,8.0,0.0,'acquaintance',1,?)
                   ON CONFLICT(a_id,b_id) DO UPDATE SET
                     familiarity = MIN(100.0, relationships.familiarity + 4.0),
                     affinity = MIN(100.0, relationships.affinity + 5.0),
                     interactions = relationships.interactions + 1,
                     last_met_tick = excluded.last_met_tick""",
                (lo, hi, tick),
            )
            text = (
                f"stopped to chat with {p2['owner_name']} at {pname} while walking "
                f"{p1['name']} ({p1['breed']}) and {p2['name']} ({p2['breed']})"
            )
            emit(conn, tick, "life_event", place_id=pid, a=a1, b=a2,
                 importance=MINOR, text=text, tag="pet_encounter",
                 pet_name=p1["name"])
            remember(conn, a1, tick,
                     f"My dog {p1['name']} made friends with {p2['owner_name']}'s {p2['name']} at {pname}.",
                     kind="personal", importance=2)
            walks += 1

    # Occasional lost-pet neighbour reunion
    if active_pets and r.random() < 0.18:
        chosen = r.choice(active_pets)
        owner_id = int(chosen["owner_id"])
        neighbours = conn.execute(
            """SELECT id, name FROM agents
               WHERE alive = 1 AND is_child = 0 AND id != ?
                 AND household_id != ?
               ORDER BY id LIMIT 30""",
            (owner_id, int(chosen["household_id"])),
        ).fetchall()
        if neighbours:
            finder = r.choice(neighbours)
            fid = int(finder["id"])
            lo, hi = min(owner_id, fid), max(owner_id, fid)
            conn.execute(
                """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                       label,interactions,last_met_tick)
                   VALUES(?,?,8.0,10.0,0.0,'friend',1,?)
                   ON CONFLICT(a_id,b_id) DO UPDATE SET
                     familiarity = MIN(100.0, relationships.familiarity + 5.0),
                     affinity = MIN(100.0, relationships.affinity + 7.0),
                     interactions = relationships.interactions + 1,
                     last_met_tick = excluded.last_met_tick""",
                (lo, hi, tick),
            )
            text = (
                f"found {chosen['owner_name']}'s {chosen['breed']} {chosen['name']} "
                f"wandering down the block and brought them safely home"
            )
            emit(conn, tick, "life_event", place_id=chosen["home_place_id"],
                 a=fid, b=owner_id, importance=NOTABLE, text=text,
                 tag="lost_pet_returned", pet_name=chosen["name"])
            remember(conn, owner_id, tick,
                     f"{finder['name']} brought our {chosen['species']} {chosen['name']} safely back home!",
                     kind="personal", importance=4)
            remember(conn, fid, tick,
                     f"I helped return {chosen['owner_name']}'s {chosen['species']} {chosen['name']} home.",
                     kind="personal", importance=3)
            reunions += 1

    return {
        "adopted": adopted,
        "comforted": len(member_ids),
        "walks": walks,
        "reunions": reunions,
    }


def pet_for_resident(conn: sqlite3.Connection,
                     agent_id: int) -> dict[str, Any] | None:
    """Return the household pet for `agent_id`, if any."""
    row = conn.execute(
        """SELECT p.id, p.name, p.species, p.breed, p.quirk,
                  p.owner_id, oa.name AS owner_name
           FROM agents a
           JOIN pets p ON p.household_id = a.household_id
           LEFT JOIN agents oa ON oa.id = p.owner_id
           WHERE a.id = ? LIMIT 1""",
        (agent_id,),
    ).fetchone()
    return dict(row) if row else None


def list_pets(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    ensure_pets(conn, int(get_meta(conn, "tick", "0") or 0),
                get_meta(conn, "seed", "miniville") or "miniville")
    rows = conn.execute(
        """SELECT p.*, a.name AS owner_name, h.name AS household_name,
                  pl.district
           FROM pets p
           JOIN households h ON h.id = p.household_id
           LEFT JOIN agents a ON a.id = p.owner_id
           LEFT JOIN places pl ON pl.id = h.home_place_id
           ORDER BY p.id ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def pet_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    items = list_pets(conn, limit=60)
    dogs = sum(1 for x in items if x["species"] == "dog")
    cats = sum(1 for x in items if x["species"] == "cat")
    total = conn.execute("SELECT COUNT(*) n FROM pets").fetchone()["n"]
    walks = conn.execute(
        'SELECT COUNT(*) n FROM events WHERE data LIKE \'%"pet_encounter"%\''
    ).fetchone()["n"]
    reunions = conn.execute(
        'SELECT COUNT(*) n FROM events WHERE data LIKE \'%"lost_pet_returned"%\''
    ).fetchone()["n"]
    return {
        "total_pets": int(total),
        "dogs": dogs,
        "cats": cats,
        "pet_encounters": int(walks),
        "reunions": int(reunions),
        "pets": items[:25],
    }
