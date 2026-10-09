"""Housing: where a household lives follows what it can afford.

The life lottery used to move a household to a random home anywhere in town
about 1.5 times per adult per year — some 700 moves a year in a town of 600,
none of them about money, so a broke family in The Flats was as likely to
land in Downtown as anywhere. The only economically driven move was the
downsize after missed rent, and it only ever went one way.

Now a household that considers moving asks what it can afford:

* **up** — enough income to carry a pricier district's rent comfortably and
  enough savings to make the jump: it moves to the best district it can;
* **down** — rent eating most of its income with little put by: it moves
  somewhere cheaper before the arrears start;
* otherwise most households stay where they are, and a few move within their
  own district (a bigger place, a quieter street).

Districts sort themselves by money over the years, which is exactly what the
council's district seats are sensitive to. The home inside a district is the
least crowded one, so a district fills evenly. Deterministic.
"""
from __future__ import annotations

import sqlite3

from .economy import PENSION_AGE, RENT_BY_DISTRICT, pension_week, rent_for
from .events import MINOR, NOTABLE, emit

UP_INCOME_RATIO = 2.5        # weekly income at least this many weeks' rent
UP_SAVINGS_WEEKS = 20        # ...and this many weeks of the new rent saved
UP_P = 0.5
DOWN_INCOME_RATIO = 1.4      # income below this many times the rent
DOWN_SAVINGS_WEEKS = 6       # ...with less than this many weeks put by
LATERAL_P = 0.15             # an unpressured household that moves anyway


def household_means(conn: sqlite3.Connection, hid: int) -> tuple[int, int]:
    """(weekly income, savings) of a household's living adults."""
    rows = conn.execute(
        """SELECT a.id, a.age, s.money_cents, j.wage_cents, j.work_days
           FROM agents a JOIN agent_state s ON s.agent_id=a.id
           LEFT JOIN jobs j ON j.agent_id=a.id
           WHERE a.household_id=? AND a.alive=1 AND a.is_child=0""", (hid,)).fetchall()
    pension = pension_week(conn)
    income = savings = 0
    for r in rows:
        savings += int(r["money_cents"] or 0)
        if r["wage_cents"] is not None:
            days = bin(int(r["work_days"] or 62)).count("1")
            income += int(r["wage_cents"]) * days
        elif (r["age"] or 0) >= PENSION_AGE:
            income += pension
    return income, savings


def _home_in(conn: sqlite3.Connection, district: str, exclude: int | None) -> int | None:
    row = conn.execute(
        """SELECT p.id, (SELECT COUNT(*) FROM agents a
                         WHERE a.home_place_id=p.id AND a.alive=1) n
           FROM places p WHERE p.kind='home' AND p.district=? AND p.id != ?
           ORDER BY n, p.id LIMIT 1""", (district, exclude or -1)).fetchone()
    return row["id"] if row else None


def choose_district(conn: sqlite3.Connection, hid: int, current: str | None,
                    r) -> tuple[str | None, str]:
    """(district to move to or None, why). Draws from `r`."""
    income, savings = household_means(conn, hid)
    current = current or "Old Mill Quarter"
    from .politics import policy
    mult = policy(conn, "rent_multiplier")
    rent_here = int(RENT_BY_DISTRICT.get(current, 35000) * mult)
    by_price = sorted(RENT_BY_DISTRICT, key=RENT_BY_DISTRICT.get)
    roll = r.random()

    if income < rent_here * DOWN_INCOME_RATIO and savings < rent_here * DOWN_SAVINGS_WEEKS:
        cheaper = [d for d in by_price if RENT_BY_DISTRICT[d] < RENT_BY_DISTRICT.get(current, 0)]
        if cheaper:
            return cheaper[-1], "down"           # one step down, not straight to the bottom
    if roll < UP_P:
        better = [d for d in by_price
                  if RENT_BY_DISTRICT[d] > RENT_BY_DISTRICT.get(current, 0)
                  and income >= RENT_BY_DISTRICT[d] * mult * UP_INCOME_RATIO
                  and savings >= RENT_BY_DISTRICT[d] * mult * UP_SAVINGS_WEEKS]
        if better:
            return better[-1], "up"
    if roll > 1 - LATERAL_P:
        return current, "lateral"
    return None, "stay"


def consider_move(conn: sqlite3.Connection, agent: sqlite3.Row, tick: int, r) -> bool:
    """The household of `agent` thinks about moving. Returns True if it did."""
    hid = agent["household_id"]
    if hid is None:
        return False
    h = conn.execute("SELECT home_place_id FROM households WHERE id=?", (hid,)).fetchone()
    old_home = h["home_place_id"] if h else agent["home_place_id"]
    here = conn.execute("SELECT district FROM places WHERE id=?", (old_home,)).fetchone()
    current = here["district"] if here else None
    district, why = choose_district(conn, hid, current, r)
    if district is None:
        return False
    new_home = _home_in(conn, district, old_home)
    if new_home is None:
        return False
    members = conn.execute(
        "SELECT id FROM agents WHERE household_id=? AND alive=1", (hid,)).fetchall()
    conn.execute("UPDATE agents SET home_place_id=? WHERE household_id=? AND alive=1",
                 (new_home, hid))
    conn.execute("UPDATE households SET home_place_id=? WHERE id=?", (new_home, hid))
    others = max(0, len(members) - 1)
    if why == "up":
        text, imp = f"moved up to {district} with {others} other(s)", NOTABLE
    elif why == "down":
        text, imp = (f"moved somewhere cheaper in {district} with {others} other(s) "
                     f"before the rent got away from them"), NOTABLE
    else:
        text, imp = f"moved house within {district} with {others} other(s)", MINOR
    emit(conn, tick, "life_event", a=agent["id"], importance=imp, text=text,
         tag="move", direction=why, district=district,
         rent=rent_for(conn, new_home))
    return True


def district_profile(conn: sqlite3.Connection) -> list[dict]:
    """Residents, households and median household savings per district."""
    out = []
    for d in sorted(RENT_BY_DISTRICT, key=RENT_BY_DISTRICT.get, reverse=True):
        hh = conn.execute(
            """SELECT h.id FROM households h JOIN places p ON p.id=h.home_place_id
               WHERE p.district=? AND EXISTS (SELECT 1 FROM agents a
                   WHERE a.household_id=h.id AND a.alive=1)""", (d,)).fetchall()
        savings = sorted(household_means(conn, x["id"])[1] for x in hh)
        residents = conn.execute(
            """SELECT COUNT(*) n FROM agents a JOIN places p ON p.id=a.home_place_id
               WHERE a.alive=1 AND p.district=?""", (d,)).fetchone()["n"]
        out.append({"district": d, "rent_cents": RENT_BY_DISTRICT[d],
                    "households": len(hh), "residents": residents,
                    "median_savings_cents": savings[len(savings) // 2] if savings else 0})
    return out
