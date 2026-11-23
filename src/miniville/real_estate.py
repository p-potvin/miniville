"""Real Estate Deeds, Mortgages & Dynasty Home Inheritance (v0.62).

Tracks residential property deeds (`home_deeds`), conserved down payments and
weekly mortgage installments (`agent_state.money_cents -> town_account`), and
multi-generational dynasty inheritance when a deed holder passes away.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_meta, set_meta
from .economy import town_credit
from .events import NOTABLE, emit
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY

BUYER_MIN_MONEY_CENTS = 150_000
DOWN_PAYMENT_CENTS = 25_000
MORTGAGE_INSTALLMENT_CENTS = 2_500
MIN_BUY_CADENCE_DAYS = 7
MAX_BUY_CADENCE_DAYS = 14


def ensure_real_estate_schema(conn: sqlite3.Connection) -> None:
    """Create the `home_deeds` table if it does not yet exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS home_deeds (
            place_id INTEGER PRIMARY KEY,
            owner_agent_id INTEGER,
            surname TEXT,
            acquired_day INTEGER,
            price_cents INTEGER,
            mortgage_cents INTEGER,
            renovations_count INTEGER,
            generation INTEGER,
            note TEXT
        )
        """
    )


def _surname_of(name: str | None) -> str:
    parts = (name or "").strip().split()
    return parts[-1] if parts else "Resident"


def _find_heir(
    conn: sqlite3.Connection,
    owner_id: int,
    place_id: int,
    owner_household_id: int | None,
    surname: str,
) -> sqlite3.Row | None:
    """Find a living heir for `owner_id` in priority order."""
    # 1. Living adult child
    row = conn.execute(
        """SELECT id, name FROM agents
           WHERE alive = 1 AND is_child = 0 AND id != ?
             AND (parent_a_id = ? OR parent_b_id = ?)
           ORDER BY age DESC, id ASC LIMIT 1""",
        (owner_id, owner_id, owner_id),
    ).fetchone()
    if row is not None:
        return row

    # 2. Living adult in same household
    if owner_household_id is not None:
        row = conn.execute(
            """SELECT id, name FROM agents
               WHERE alive = 1 AND is_child = 0 AND id != ? AND household_id = ?
               ORDER BY age DESC, id ASC LIMIT 1""",
            (owner_id, owner_household_id),
        ).fetchone()
        if row is not None:
            return row

    # 3. Living minor child
    row = conn.execute(
        """SELECT id, name FROM agents
           WHERE alive = 1 AND id != ?
             AND (parent_a_id = ? OR parent_b_id = ?)
           ORDER BY age DESC, id ASC LIMIT 1""",
        (owner_id, owner_id, owner_id),
    ).fetchone()
    if row is not None:
        return row

    # 4. Living adult at same home_place_id
    row = conn.execute(
        """SELECT id, name FROM agents
           WHERE alive = 1 AND is_child = 0 AND id != ? AND home_place_id = ?
           ORDER BY age DESC, id ASC LIMIT 1""",
        (owner_id, place_id),
    ).fetchone()
    if row is not None:
        return row

    # 5. Living adult sharing surname
    if surname:
        row = conn.execute(
            """SELECT id, name FROM agents
               WHERE alive = 1 AND is_child = 0 AND id != ? AND name LIKE ?
               ORDER BY age DESC, id ASC LIMIT 1""",
            (owner_id, f"% {surname}"),
        ).fetchone()
        if row is not None:
            return row

    return None


def _process_inheritances(conn: sqlite3.Connection, day: int, tick: int) -> tuple[int, int]:
    rows = conn.execute(
        """SELECT d.place_id, d.owner_agent_id, d.surname, d.generation,
                  a.alive, a.household_id
           FROM home_deeds d
           LEFT JOIN agents a ON a.id = d.owner_agent_id
           WHERE a.id IS NULL OR a.alive != 1
           ORDER BY d.place_id"""
    ).fetchall()

    inherited = 0
    released = 0
    for r in rows:
        place_id = int(r["place_id"])
        owner_id = int(r["owner_agent_id"]) if r["owner_agent_id"] is not None else -1
        surname = r["surname"] or ""
        heir = _find_heir(conn, owner_id, place_id, r["household_id"], surname)
        if heir is not None:
            new_gen = int(r["generation"] or 1) + 1
            heir_id = int(heir["id"])
            heir_name = str(heir["name"])
            note = f"Inherited by {heir_name} (Gen {new_gen}) on Day {day}"
            conn.execute(
                """UPDATE home_deeds
                   SET owner_agent_id = ?, generation = ?, note = ?
                   WHERE place_id = ?""",
                (heir_id, new_gen, note, place_id),
            )
            emit(
                conn,
                tick,
                "town_event",
                place_id=place_id,
                a=heir_id,
                b=owner_id if owner_id > 0 else None,
                importance=NOTABLE,
                tag="home_inherited",
                day=day,
                generation=new_gen,
                text=f"{heir_name} inherited the family home deed (Gen {new_gen})",
            )
            inherited += 1
        else:
            conn.execute("DELETE FROM home_deeds WHERE place_id = ?", (place_id,))
            released += 1
    return inherited, released


def _maybe_purchase_deed(conn: sqlite3.Connection, day: int, tick: int, seed: str) -> int:
    last_row = conn.execute("SELECT MAX(acquired_day) AS last_day FROM home_deeds").fetchone()
    last_day = last_row["last_day"] if last_row and last_row["last_day"] is not None else None
    if last_day is not None:
        gap = day - int(last_day)
        if gap < MIN_BUY_CADENCE_DAYS:
            return 0
        r_cadence = rng_for(seed, "real_estate_cadence", day)
        if gap < MAX_BUY_CADENCE_DAYS and r_cadence.random() >= 0.45:
            return 0

    candidates = conn.execute(
        """SELECT a.id, a.name, a.home_place_id, s.money_cents, p.district
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           JOIN places p ON p.id = a.home_place_id
           WHERE a.alive = 1
             AND a.is_child = 0
             AND a.home_place_id IS NOT NULL
             AND p.kind = 'home'
             AND s.money_cents >= ?
             AND NOT EXISTS (SELECT 1 FROM home_deeds d WHERE d.place_id = a.home_place_id)
           ORDER BY s.money_cents DESC, a.id ASC
           LIMIT 25""",
        (BUYER_MIN_MONEY_CENTS,),
    ).fetchall()
    if not candidates:
        return 0

    r = rng_for(seed, "real_estate_buy", day)
    buyer = candidates[r.randrange(len(candidates))]
    buyer_id = int(buyer["id"])
    buyer_name = str(buyer["name"])
    place_id = int(buyer["home_place_id"])
    district = str(buyer["district"] or "Miniville")

    price_cents = r.randint(80, 120) * 1000
    down_payment = min(DOWN_PAYMENT_CENTS, price_cents)
    mortgage_cents = max(0, price_cents - down_payment)

    # 100% conserved transfer: agent_state.money_cents -> town_account
    conn.execute(
        "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
        (down_payment, buyer_id),
    )
    town_credit(conn, down_payment)

    # Modest mood/stress relief for new homeowner
    conn.execute(
        """UPDATE agent_state
           SET fun = MIN(100.0, fun + 4.0),
               stress = MAX(0.0, stress - 5.0)
           WHERE agent_id = ?""",
        (buyer_id,),
    )

    surname = _surname_of(buyer_name)
    note = f"Purchased by {buyer_name} on Day {day} ({district})"
    conn.execute(
        """INSERT INTO home_deeds
           (place_id, owner_agent_id, surname, acquired_day, price_cents,
            mortgage_cents, renovations_count, generation, note)
           VALUES (?,?,?,?,?,?,0,1,?)""",
        (place_id, buyer_id, surname, day, price_cents, mortgage_cents, note),
    )

    emit(
        conn,
        tick,
        "town_event",
        place_id=place_id,
        a=buyer_id,
        importance=NOTABLE,
        tag="home_purchased",
        day=day,
        price_cents=price_cents,
        mortgage_cents=mortgage_cents,
        text=f"{buyer_name} purchased the deed to their {district} home (${price_cents // 100:,})",
    )
    return 1


def _collect_mortgages(conn: sqlite3.Connection, day: int) -> int:
    if day % 7 != 0:
        return 0
    rows = conn.execute(
        """SELECT d.place_id, d.owner_agent_id, d.mortgage_cents, s.money_cents
           FROM home_deeds d
           JOIN agents a ON a.id = d.owner_agent_id
           JOIN agent_state s ON s.agent_id = d.owner_agent_id
           WHERE a.alive = 1
             AND d.mortgage_cents > 0
             AND d.acquired_day < ?
           ORDER BY d.place_id""",
        (day,),
    ).fetchall()

    paid_total = 0
    for r in rows:
        due = min(int(r["mortgage_cents"]), MORTGAGE_INSTALLMENT_CENTS)
        if due <= 0 or int(r["money_cents"]) < due:
            continue
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
            (due, int(r["owner_agent_id"])),
        )
        town_credit(conn, due)
        conn.execute(
            "UPDATE home_deeds SET mortgage_cents = mortgage_cents - ? WHERE place_id = ?",
            (due, int(r["place_id"])),
        )
        paid_total += due
    return paid_total


def daily_real_estate(
    conn: sqlite3.Connection,
    day: int,
    seed: str = "miniville",
) -> dict[str, Any]:
    """Run daily deed inheritance, bounded home purchases, and weekly mortgages (idempotent)."""
    ensure_real_estate_schema(conn)
    day_i = int(day)
    last_done = int(get_meta(conn, "last_real_estate_day", "-1") or -1)
    if last_done >= day_i:
        return {
            "day": day_i,
            "skipped": True,
            "inherited": 0,
            "released": 0,
            "purchased": 0,
            "mortgage_paid_cents": 0,
        }

    tick = day_i * TICKS_PER_DAY
    inherited, released = _process_inheritances(conn, day_i, tick)
    purchased = _maybe_purchase_deed(conn, day_i, tick, seed)
    mortgage_paid = _collect_mortgages(conn, day_i)
    set_meta(conn, "last_real_estate_day", str(day_i))

    return {
        "day": day_i,
        "skipped": False,
        "inherited": inherited,
        "released": released,
        "purchased": purchased,
        "mortgage_paid_cents": mortgage_paid,
    }


def list_deeds(conn: sqlite3.Connection, limit: int = 25) -> list[dict[str, Any]]:
    """Return home deeds enriched with owner and place info."""
    try:
        rows = conn.execute(
            """SELECT d.place_id, d.owner_agent_id, d.surname, d.acquired_day,
                      d.price_cents, d.mortgage_cents, d.renovations_count,
                      d.generation, d.note,
                      a.name AS owner_name,
                      p.name AS place_name, p.district AS district
               FROM home_deeds d
               LEFT JOIN agents a ON a.id = d.owner_agent_id
               LEFT JOIN places p ON p.id = d.place_id
               ORDER BY d.generation DESC, d.acquired_day DESC, d.place_id ASC
               LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "place_id": int(r["place_id"]),
            "owner_agent_id": int(r["owner_agent_id"]) if r["owner_agent_id"] is not None else None,
            "owner_name": r["owner_name"],
            "place_name": r["place_name"],
            "district": r["district"],
            "surname": r["surname"],
            "acquired_day": int(r["acquired_day"] or 0),
            "price_cents": int(r["price_cents"] or 0),
            "mortgage_cents": int(r["mortgage_cents"] or 0),
            "renovations_count": int(r["renovations_count"] or 0),
            "generation": int(r["generation"] or 1),
            "note": r["note"] or "",
        }
        for r in rows
    ]


def real_estate_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate real estate deed/mortgage stats plus top deeds."""
    try:
        agg = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(CASE WHEN mortgage_cents > 0 THEN 1 ELSE 0 END), 0) AS active_mortgages,
                      COALESCE(SUM(mortgage_cents), 0) AS total_mortgage_cents,
                      COALESCE(MAX(generation), 0) AS max_generation
               FROM home_deeds"""
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "total_deeds": 0,
            "active_mortgages": 0,
            "total_mortgage_cents": 0,
            "max_generation": 0,
            "deeds": [],
        }
    return {
        "total_deeds": int(agg["cnt"]),
        "active_mortgages": int(agg["active_mortgages"]),
        "total_mortgage_cents": int(agg["total_mortgage_cents"]),
        "max_generation": int(agg["max_generation"]),
        "deeds": list_deeds(conn, limit=25),
    }
