"""Miniville economy: rent, prices, business revenue, and business failure.

The town had money but no *flows*: wages were minted, nothing was ever priced,
and no business could fail. This module gives the town a cost of living and a
business sector.

Households
    Money leaves a household as rent (weekly, by district), groceries (one run
    per resident per day, paid to the grocer), meals out, shopping trips, and
    small leisure purchases (a coffee, a pint, a cinema ticket). Rent that
    cannot be paid becomes arrears; two missed payments and the household
    downsizes to the cheapest district.

Businesses
    Every non-home venue has a row in `businesses`. Customer spending is
    credited to the venue that served it; wages paid to its staff are debited.
    Public-service venues (hospital, school, town hall, gazette, church) are
    funded by the town and break even. A commercial venue that bleeds for long
    enough fails: it closes, lays off its staff, and reopens weeks later under
    new management.

    A struggling business also raises its prices (`price_index`), so failure is
    not inevitable — but a venue that loses its customers cannot price its way
    out.

Wages
    A weekly labour-market drift nudges `wage_index` (in `meta`) down when
    unemployment is high and up when it is scarce, so wages no longer only ever
    go up.

All rolls are deterministic per (seed, day).
"""
from __future__ import annotations

import json
import sqlite3

from .db import get_meta, set_meta
from .events import MAJOR, MINOR, NOTABLE, emit
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY, day_of, tick_of_day

# --- pay scales -------------------------------------------------------------

# A day's wage and a starting wallet, calibrated against the prices below so
# that a working household roughly covers rent, food and errands: the town
# should not inflate away its own scarcity. The wage is a *day's* pay for five
# days of work, which is why it looks large next to a weekly rent.
WAGE_MIN_CENTS, WAGE_MAX_CENTS = 6300, 25200
STARTING_MONEY_MIN, STARTING_MONEY_MAX = 400_000, 2_400_000


# --- prices -----------------------------------------------------------------

# weekly rent in cents, by the district the household lives in
RENT_BY_DISTRICT = {
    "Downtown": 46000,
    "Lakeshore": 42000,
    "Greenhill": 38500,
    "Old Mill Quarter": 35000,
    "The Flats": 29000,
}
DEFAULT_RENT = 35000

GROCERY_CENTS = 750          # one grocery run per resident per day
DINING_BASE_CENTS = 1800
SHOPPING_BASE_CENTS = 3500
GROCERY_TICK = 14            # 07:00 — the daily shop

# --- business tuning --------------------------------------------------------

# venues whose payroll is covered by the town rather than by customers
PUBLIC_TAGS = {"health", "education", "civic", "office", "media", "worship"}

FAIL_THRESHOLD_CENTS = -6_000_000     # -$60,000 of accumulated losses
REOPEN_AFTER_DAYS = 21
PRICE_INDEX_MAX = 1.6
PRICE_INDEX_MIN = 0.85

# Business reserves would otherwise hoard money forever — nothing a business
# earns ever comes back to a household. A weekly levy on those reserves is paid
# out again as a civic dividend, which both closes the loop and keeps the
# money supply from draining away.
BUSINESS_TAX_RATE = 0.05
# Most of the levy is spent on the town's public services (the hospital, the
# school, the town hall) and only the surplus is handed back out, so the
# dividend stays a modest rebate rather than becoming the town's main income.
LEVY_DIVIDEND_SHARE = 0.35
WAGE_INDEX_MIN, WAGE_INDEX_MAX = 0.6, 1.6
UNEMPLOYMENT_HIGH = 0.12   # above this, wages drift down
UNEMPLOYMENT_LOW = 0.05    # below this, wages drift up
WAGE_STEP = 0.005          # 0.5% per week

PAID_LEISURE = {"coffee", "drink", "arts", "nightlife", "fitness"}


def wage_index(conn: sqlite3.Connection) -> float:
    return float(get_meta(conn, "wage_index", "1.0") or 1.0)


def venue_price(tags: set[str], activity: str, price_index: float = 1.0) -> int:
    """What one visit costs a resident, before the venue's price index."""
    if activity == "eat_out":
        c = DINING_BASE_CENTS
        if "drink" in tags:
            c += 700
        if "coffee" in tags:
            c -= 800
    elif activity == "shopping":
        c = SHOPPING_BASE_CENTS
        if "trades" in tags:
            c += 2000
        if "food" in tags:
            c -= 500
    elif activity == "leisure":
        if "coffee" in tags:
            c = 700
        elif "drink" in tags:
            c = 1400
        elif "arts" in tags or "nightlife" in tags:
            c = 1600
        elif "fitness" in tags:
            c = 1200
        else:
            c = 0
    elif activity == "eat":
        c = GROCERY_CENTS
    else:
        c = 0
    return max(0, int(round(c * price_index)))


def rent_for(conn: sqlite3.Connection, home_place_id: int | None) -> int:
    if home_place_id is None:
        return DEFAULT_RENT
    row = conn.execute("SELECT district FROM places WHERE id=?",
                       (home_place_id,)).fetchone()
    if not row:
        return DEFAULT_RENT
    return RENT_BY_DISTRICT.get(row["district"], DEFAULT_RENT)


# --- business bookkeeping ---------------------------------------------------


def ensure_businesses(conn: sqlite3.Connection) -> int:
    """Create a business row for every venue that does not have one yet."""
    cur = conn.execute(
        """INSERT INTO businesses(place_id, opened_tick)
           SELECT p.id, ? FROM places p
           WHERE p.kind != 'home'
             AND NOT EXISTS (SELECT 1 FROM businesses b WHERE b.place_id = p.id)""",
        (int(get_meta(conn, "tick", "0") or 0),))
    return cur.rowcount


def _venue_ctx(conn: sqlite3.Connection) -> dict:
    """Place tags + price index, keyed by place_id (one query per tick)."""
    rows = conn.execute(
        """SELECT p.id, p.name, p.kind, p.tags, p.district,
                  COALESCE(b.price_index, 1.0) price_index, COALESCE(b.status,'open') status
           FROM places p LEFT JOIN businesses b ON b.place_id = p.id""").fetchall()
    ctx = {}
    for r in rows:
        ctx[r["id"]] = {
            "name": r["name"], "kind": r["kind"], "district": r["district"],
            "tags": set(json.loads(r["tags"] or "[]")),
            "price_index": float(r["price_index"]), "status": r["status"],
        }
    return ctx


def pay_wages(conn: sqlite3.Connection, tick: int) -> int:
    """Pay every shift that ends on this tick; debit the employer.

    Only staff who actually worked today are paid. `jobs.work_days` has always
    said Mon-Fri, but the old wage pass ignored it and paid every job at its
    shift end seven days a week — so a business was paying a full week's
    payroll for five days of work.
    """
    tod = tick_of_day(tick)
    idx = wage_index(conn)
    rows = conn.execute(
        """SELECT j.agent_id, j.place_id, j.wage_cents FROM jobs j
           WHERE j.shift_end=?
             AND EXISTS (SELECT 1 FROM plans p
                         WHERE p.agent_id=j.agent_id
                           AND p.activity IN ('work','break'))""",
        (tod,)).fetchall()
    total = 0
    for row in rows:
        paid = int(round(row["wage_cents"] * idx))
        conn.execute(
            "UPDATE agent_state SET money_cents=money_cents+? WHERE agent_id=?",
            (paid, row["agent_id"]))
        conn.execute(
            """UPDATE businesses SET payroll_today=payroll_today+?,
                   payroll_total=payroll_total+?
               WHERE place_id=?""",
            (paid, paid, row["place_id"]))
        total += paid
    return total


def charge_spending(conn: sqlite3.Connection, tick: int) -> dict:
    """Charge residents for meals, shopping and small leisure purchases.

    Groceries are charged once a day (the morning shop) and credited to the
    town grocer; everything else is credited to the venue that served it.
    Returns {'spent': cents, 'charged': n_agents}.
    """
    tod = tick_of_day(tick)
    ctx = _venue_ctx(conn)
    grocer = conn.execute(
        "SELECT id FROM places WHERE tags LIKE '%\"food\"%' AND kind='workplace' "
        "ORDER BY id LIMIT 1").fetchone()
    grocer_id = grocer["id"] if grocer else None
    if grocer_id is not None and ctx.get(grocer_id, {}).get("status") == "closed":
        grocer_id = None                       # the grocer is shut: no groceries

    rows = conn.execute(
        """SELECT s.agent_id, s.place_id, s.activity, s.money_cents
           FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1 AND s.activity IN ('eat','eat_out','shopping','leisure')"""
    ).fetchall()

    spent, charged = 0, 0
    for row in rows:
        act = row["activity"]
        place = ctx.get(row["place_id"]) or {}
        tags = place.get("tags", set())

        if act == "eat":
            if tod != GROCERY_TICK or grocer_id is None:
                continue                       # one grocery run per day
            cost = venue_price(tags, "eat")
            credit_to = grocer_id
        elif act in ("eat_out", "shopping"):
            if place.get("status") == "closed":
                continue                       # shut shops serve nobody
            cost = venue_price(tags, act, place.get("price_index", 1.0))
            credit_to = row["place_id"]
        else:  # leisure — only some venues charge
            if not (tags & PAID_LEISURE) or place.get("status") == "closed":
                continue
            cost = venue_price(tags, "leisure", place.get("price_index", 1.0))
            credit_to = row["place_id"]

        if cost <= 0:
            continue
        affordable = min(cost, max(0, row["money_cents"]))
        if affordable <= 0:
            continue
        conn.execute("UPDATE agent_state SET money_cents=money_cents-? WHERE agent_id=?",
                     (affordable, row["agent_id"]))
        conn.execute(
            """UPDATE businesses SET revenue_today=revenue_today+?,
                   revenue_total=revenue_total+?, traffic_today=traffic_today+1
               WHERE place_id=?""",
            (affordable, affordable, credit_to))
        spent += affordable
        charged += 1
    if spent:
        _bump_day(conn, day_of(tick), spending=spent)
    return {"spent": spent, "charged": charged}


def _bump_day(conn: sqlite3.Connection, day: int, spending: int = 0,
              rent: int = 0) -> None:
    """Accumulate a day's flows into the economy time series as they happen."""
    if spending == 0 and rent == 0:
        return
    conn.execute(
        """INSERT INTO economy_days(day, spending_cents, rent_cents) VALUES(?,?,?)
           ON CONFLICT(day) DO UPDATE SET
             spending_cents = spending_cents + excluded.spending_cents,
             rent_cents = rent_cents + excluded.rent_cents""",
        (day, spending, rent))


# --- rent -------------------------------------------------------------------


def collect_rent(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Weekly rent, split across a household's working-age adults."""
    day = day_of(tick)
    if day % 7 != 0:
        return {"collected": 0, "missed": 0, "downsized": 0}

    collected = missed = downsized = 0
    households = conn.execute("SELECT * FROM households").fetchall()
    for h in households:
        adults = conn.execute(
            """SELECT a.id, s.money_cents FROM agents a
               JOIN agent_state s ON s.agent_id=a.id
               WHERE a.household_id=? AND a.alive=1 AND a.is_child=0""",
            (h["id"],)).fetchall()
        if not adults:
            continue
        rent = rent_for(conn, h["home_place_id"])
        purse = sum(a["money_cents"] for a in adults)
        if purse < rent:
            missed += 1
            _note_arrears(conn, h, tick, seed)
            downsized += _maybe_downsize(conn, h, tick, seed, len(adults))
            continue

        share = rent // len(adults)
        remainder = rent - share * len(adults)
        for i, a in enumerate(adults):
            due = share + (remainder if i == 0 else 0)
            conn.execute(
                "UPDATE agent_state SET money_cents=money_cents-? WHERE agent_id=?",
                (due, a["id"]))
        conn.execute("DELETE FROM rent_arrears WHERE household_id=?", (h["id"],))
        collected += rent

    if collected:
        _bump_day(conn, day, rent=collected)
    conn.commit()
    return {"collected": collected, "missed": missed, "downsized": downsized}


def weekly_levy(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Tax business reserves; spend most of it on the town, rebate the rest.

    Without this, money paid to a shop or a tavern is gone for good: wages are
    minted and rent is destroyed, so the town would slowly bleed dry.
    """
    day = day_of(tick)
    if day % 7 != 0 or day == 0:
        return {"levied": 0, "dividend": 0, "residents": 0}

    levied = 0
    for b in conn.execute(
            "SELECT place_id, balance_cents FROM businesses WHERE balance_cents > 0"
    ).fetchall():
        take = int(b["balance_cents"] * BUSINESS_TAX_RATE)
        if take <= 0:
            continue
        conn.execute("UPDATE businesses SET balance_cents=balance_cents-? WHERE place_id=?",
                     (take, b["place_id"]))
        levied += take
    if levied <= 0:
        return {"levied": 0, "dividend": 0, "residents": 0}

    # the rest of the levy is what the town runs on — the hospital, the school,
    # the town hall — so it leaves circulation here
    pot = int(levied * LEVY_DIVIDEND_SHARE)
    adults = conn.execute(
        "SELECT id FROM agents WHERE alive=1 AND is_child=0 ORDER BY id").fetchall()
    if pot <= 0 or not adults:
        conn.commit()
        return {"levied": levied, "dividend": 0, "residents": len(adults)}

    share = pot // len(adults)
    remainder = pot - share * len(adults)
    for i, a in enumerate(adults):
        conn.execute("UPDATE agent_state SET money_cents=money_cents+? WHERE agent_id=?",
                     (share + (remainder if i == 0 else 0), a["id"]))
    emit(conn, tick, "town_event", importance=MINOR,
         text=f"the town levied ${levied / 100:,.0f} on local businesses and paid "
              f"every resident a ${share / 100:,.0f} dividend",
         tag="dividend")
    conn.commit()
    return {"levied": levied, "dividend": share, "residents": len(adults)}


def _note_arrears(conn: sqlite3.Connection, h: sqlite3.Row, tick: int, seed: str) -> None:
    row = conn.execute("SELECT * FROM rent_arrears WHERE household_id=?",
                       (h["id"],)).fetchone()
    n = (row["missed_payments"] if row else 0) + 1
    conn.execute(
        """INSERT INTO rent_arrears(household_id, missed_payments, last_missed_day)
           VALUES(?,?,?) ON CONFLICT(household_id) DO UPDATE SET
             missed_payments=excluded.missed_payments,
             last_missed_day=excluded.last_missed_day""",
        (h["id"], n, day_of(tick)))
    emit(conn, tick, "life_event", importance=MINOR,
         text=f"fell behind on rent ({h['name']})", tag="rent_distress")


def _maybe_downsize(conn: sqlite3.Connection, h: sqlite3.Row, tick: int,
                    seed: str, n_adults: int) -> int:
    """After two missed payments the household moves to the cheapest district."""
    row = conn.execute("SELECT * FROM rent_arrears WHERE household_id=?",
                       (h["id"],)).fetchone()
    if not row or row["missed_payments"] < 2:
        return 0
    r = rng_for(seed, "downsize", h["id"], day_of(tick))
    cheap = conn.execute(
        "SELECT id FROM places WHERE kind='home' AND district='The Flats' "
        "ORDER BY id").fetchall()
    if not cheap:
        return 0
    new_home = r.choice(cheap)["id"]
    conn.execute("UPDATE households SET home_place_id=? WHERE id=?", (new_home, h["id"]))
    conn.execute(
        """UPDATE agents SET home_place_id=? WHERE household_id=?
           AND alive=1 AND is_child=0""",
        (new_home, h["id"]))
    conn.execute("DELETE FROM rent_arrears WHERE household_id=?", (h["id"],))
    members = conn.execute(
        "SELECT id FROM agents WHERE household_id=? AND alive=1", (h["id"],)).fetchall()
    emit(conn, tick, "life_event", a=members[0]["id"] if members else None,
         importance=MAJOR,
         text=f"could not keep up with rent and moved to The Flats with "
              f"{max(0, len(members) - 1)} other(s)", tag="downsize")
    return 1


# --- daily business settlement ---------------------------------------------


def settle_businesses(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Close the books on the day that just ended and act on the results."""
    day = day_of(tick) - 1
    if day < 0:
        return {"settled": 0, "closed": 0, "reopened": 0}

    ensure_businesses(conn)
    closed = reopened = settled = 0
    rows = conn.execute(
        """SELECT b.*, p.name, p.tags, p.kind FROM businesses b
           JOIN places p ON p.id=b.place_id WHERE b.last_settled_day < ?""",
        (day,)).fetchall()

    for b in rows:
        tags = set(json.loads(b["tags"] or "[]"))
        public = bool(tags & PUBLIC_TAGS) or b["kind"] == "civic"
        staffed = conn.execute("SELECT COUNT(*) n FROM jobs WHERE place_id=?",
                               (b["place_id"],)).fetchone()["n"]

        revenue = b["revenue_today"]
        payroll = b["payroll_today"]
        if public and staffed:
            revenue = payroll                    # funded by the town
        elif not staffed:
            revenue = b["revenue_today"]         # owner-operated: no failure
        balance = b["balance_cents"] + revenue - payroll

        # traffic EMA is what "customers stopped coming" is measured against
        ema = b["ema_traffic"] * 0.9 + b["traffic_today"] * 0.1
        price_index = b["price_index"]
        if not public and staffed:
            # bleeding venues put prices up; comfortable ones are undercut by
            # the competition next door and drift back down
            if balance < 0:
                price_index = min(PRICE_INDEX_MAX, price_index + 0.02)
            else:
                price_index = max(PRICE_INDEX_MIN, price_index - 0.005)

        conn.execute(
            """UPDATE businesses SET balance_cents=?, ema_traffic=?,
                   price_index=?, revenue_today=0, payroll_today=0,
                   traffic_today=0, last_settled_day=? WHERE place_id=?""",
            (balance, ema, price_index, day, b["place_id"]))
        settled += 1

        if b["status"] == "open" and not public and staffed:
            if balance < FAIL_THRESHOLD_CENTS:
                closed += _close_business(conn, b, tick, balance)
        elif b["status"] == "closed":
            # a shock-set reopen_day overrides the market's 21-day cooldown;
            # settle runs at midnight for the day that ended, so the venue is
            # open in time for that day's plans
            if b["reopen_day"] is not None:
                if day_of(tick) >= b["reopen_day"]:
                    reopened += _reopen_business(conn, b, tick)
            else:
                closed_at = b["closed_tick"] if b["closed_tick"] is not None else tick
                if tick - closed_at >= REOPEN_AFTER_DAYS * TICKS_PER_DAY:
                    reopened += _reopen_business(conn, b, tick)

    conn.commit()
    return {"settled": settled, "closed": closed, "reopened": reopened}


def _close_business(conn: sqlite3.Connection, b: sqlite3.Row, tick: int,
                    balance: int) -> int:
    staff = conn.execute(
        """SELECT j.agent_id, a.name FROM jobs j JOIN agents a ON a.id=j.agent_id
           WHERE j.place_id=?""", (b["place_id"],)).fetchall()
    conn.execute("DELETE FROM jobs WHERE place_id=?", (b["place_id"],))
    conn.execute("UPDATE agents SET work_place_id=NULL WHERE work_place_id=?",
                 (b["place_id"],))
    conn.execute(
        """UPDATE businesses SET status='closed', closed_tick=?, balance_cents=0,
               price_index=1.0, reopen_day=NULL WHERE place_id=?""",
        (tick, b["place_id"]))
    emit(conn, tick, "town_event", place_id=b["place_id"], importance=MAJOR,
         text=f"{b['name']} has closed after months of losses; "
              f"{len(staff)} people lost their jobs",
         tag="business_closed", balance=balance)
    return 1


def _reopen_business(conn: sqlite3.Connection, b: sqlite3.Row, tick: int) -> int:
    conn.execute(
        """UPDATE businesses SET status='open', closed_tick=NULL,
               balance_cents=0, ema_traffic=0, price_index=1.0, reopen_day=NULL
           WHERE place_id=?""", (b["place_id"],))
    emit(conn, tick, "town_event", place_id=b["place_id"], importance=NOTABLE,
         text=f"{b['name']} has reopened under new management", tag="business_reopened")
    return 1


def open_workplaces(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Workplaces a resident could actually be hired at."""
    return conn.execute(
        """SELECT p.id, p.name, p.tags FROM places p
           LEFT JOIN businesses b ON b.place_id=p.id
           WHERE p.kind='workplace' AND COALESCE(b.status,'open')='open'""").fetchall()


# --- labour market ----------------------------------------------------------


def unemployment(conn: sqlite3.Connection) -> float:
    adults = conn.execute(
        "SELECT COUNT(*) c FROM agents WHERE alive=1 AND is_child=0").fetchone()["c"]
    if not adults:
        return 0.0
    employed = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    return max(0.0, (adults - employed) / adults)


def wage_dynamics(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Weekly drift: wages fall when labour is abundant, rise when it is scarce."""
    day = day_of(tick)
    if day % 7 != 0 or day == 0:
        return {"index": wage_index(conn), "changed": False}

    idx = wage_index(conn)
    u = unemployment(conn)
    if u > UNEMPLOYMENT_HIGH:
        idx *= (1 - WAGE_STEP)
    elif u < UNEMPLOYMENT_LOW:
        idx *= (1 + WAGE_STEP)
    idx = max(WAGE_INDEX_MIN, min(WAGE_INDEX_MAX, idx))
    set_meta(conn, "wage_index", f"{idx:.4f}")

    announced = float(get_meta(conn, "wage_index_announced", "1.0") or 1.0)
    changed = False
    if abs(idx - announced) >= 0.02:
        direction = "risen" if idx > announced else "fallen"
        emit(conn, tick, "town_event", importance=MINOR,
             text=f"wages across Miniville have {direction} to "
                  f"{idx * 100:.0f}% of their spring level "
                  f"(unemployment {u * 100:.0f}%)",
             tag="wage_change")
        set_meta(conn, "wage_index_announced", f"{idx:.4f}")
        changed = True
    conn.commit()
    return {"index": idx, "changed": changed, "unemployment": u}


# --- reporting --------------------------------------------------------------


def economy_stats(conn: sqlite3.Connection) -> dict:
    money = conn.execute(
        "SELECT COALESCE(SUM(money_cents),0) s FROM agent_state").fetchone()["s"]
    balances = [r["money_cents"] for r in
                conn.execute("SELECT money_cents FROM agent_state").fetchall()]
    balances.sort()
    n = len(balances)
    median = balances[n // 2] if n else 0
    mean = int(money / n) if n else 0
    broke = sum(1 for b in balances if b < 0)
    businesses = conn.execute(
        """SELECT status, COUNT(*) n FROM businesses GROUP BY status""").fetchall()
    counts = {r["status"]: r["n"] for r in businesses}
    # a row for the in-progress day exists as soon as anyone spends, so only
    # completed days (which have a money supply) count as "the last day"
    day_row = conn.execute(
        "SELECT * FROM economy_days WHERE money_supply_cents > 0 "
        "ORDER BY day DESC LIMIT 1").fetchone()
    return {
        "money_supply_cents": money,
        "median_balance_cents": median,
        "mean_balance_cents": mean,
        "in_debt": broke,
        "unemployment": unemployment(conn),
        "wage_index": wage_index(conn),
        "businesses_open": counts.get("open", 0),
        "businesses_closed": counts.get("closed", 0),
        "last_day": dict(day_row) if day_row else None,
    }


def record_day(conn: sqlite3.Connection, tick: int) -> dict:
    """Write the finished day into the economy time series.

    Must run *before* `settle_businesses`, which zeroes the daily counters.
    """
    day = day_of(tick) - 1
    if day < 0:
        return {}
    existing = conn.execute(
        "SELECT * FROM economy_days WHERE day=?", (day,)).fetchone()
    prev = conn.execute(
        "SELECT * FROM economy_days WHERE day=?", (day - 1,)).fetchone()
    money = conn.execute(
        "SELECT COALESCE(SUM(money_cents),0) s FROM agent_state").fetchone()["s"]
    flows = conn.execute(
        "SELECT COALESCE(SUM(revenue_today),0) r, COALESCE(SUM(payroll_today),0) p "
        "FROM businesses").fetchone()
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM businesses GROUP BY status")}
    stats = {
        "day": day,
        "revenue_cents": flows["r"],
        "payroll_cents": flows["p"],
        "rent_cents": existing["rent_cents"] if existing else 0,
        "spending_cents": existing["spending_cents"] if existing else 0,
        "money_supply_cents": money,
        "unemployment_bp": int(unemployment(conn) * 10000),
        "businesses_open": counts.get("open", 0),
        "businesses_closed": counts.get("closed", 0),
        "wage_index": wage_index(conn),
    }
    if prev:
        stats["money_supply_delta"] = money - prev["money_supply_cents"]
    conn.execute(
        """INSERT INTO economy_days(day,revenue_cents,payroll_cents,rent_cents,
               spending_cents,money_supply_cents,unemployment_bp,businesses_open,
               businesses_closed,wage_index)
           VALUES(:day,:revenue_cents,:payroll_cents,:rent_cents,:spending_cents,
                  :money_supply_cents,:unemployment_bp,:businesses_open,
                  :businesses_closed,:wage_index)
           ON CONFLICT(day) DO UPDATE SET
             revenue_cents=excluded.revenue_cents,
             payroll_cents=excluded.payroll_cents,
             money_supply_cents=excluded.money_supply_cents,
             unemployment_bp=excluded.unemployment_bp,
             businesses_open=excluded.businesses_open,
             businesses_closed=excluded.businesses_closed,
             wage_index=excluded.wage_index""",
        stats)
    conn.commit()
    return stats


def economy_line(conn: sqlite3.Connection) -> str:
    """One-line summary for the chronicle / status output."""
    s = economy_stats(conn)
    return (f"money supply ${s['money_supply_cents'] / 100:,.0f} · "
            f"median wallet ${s['median_balance_cents'] / 100:,.0f} · "
            f"unemployment {s['unemployment'] * 100:.1f}% · "
            f"businesses {s['businesses_open']} open / {s['businesses_closed']} closed · "
            f"wages {s['wage_index'] * 100:.0f}%")
