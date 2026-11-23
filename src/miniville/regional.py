"""Neighboring Sister Towns, Tourism Waves & Conserved Regional Trade (v0.61).

Simulates daily regional contact with three neighboring sister towns:
  - Oakhaven (timber, orchard cider, handmade furniture)
  - Port Meridian (sea salt, coastal textiles, marine hardware)
  - Ironforge (cast ironwork, precision tools, stoneware)

Tourism waves respond to active town festivals, fair weather, and recent
museum exhibits. Bilateral caravan trade clears strictly inside Miniville's
3-way balance sheet (`agent_state.money_cents <-> businesses.balance_cents <->
town_account`), guaranteeing 100% exact money conservation to the single cent.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .economy import town_credit
from .events import NOTABLE, emit
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY
from .weather import today as weather_today

SISTER_TOWNS: dict[str, dict[str, Any]] = {
    "Oakhaven": {
        "exports": ("carved walnut chairs", "Lakeshore watercolours", "artisan loaves"),
        "imports": ("seasoned cedar planks", "orchard apple cider", "hand-thrown stoneware"),
    },
    "Port Meridian": {
        "exports": ("woollen harbour shawls", "clockwork mechanisms", "bound chronicles"),
        "imports": ("smoked sea salt", "sailcloth & brass fittings", "coastal spices"),
    },
    "Ironforge": {
        "exports": ("glazed pottery", "maple cabinetry", "preserved orchard jams"),
        "imports": ("forged garden shears", "cast-iron skillets", "precision calipers"),
    },
}
SISTER_TOWN_NAMES = tuple(SISTER_TOWNS.keys())

EVENT_COOLDOWN_DAYS = 5
MIN_BUYER_WALLET_CENTS = 25_000
MIN_BUSINESS_EXPORT_RESERVE_CENTS = 10_000


def ensure_regional_schema(conn: sqlite3.Connection) -> None:
    """Create regional_days table if it does not yet exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS regional_days (
               day INTEGER PRIMARY KEY,
               sister_town TEXT NOT NULL,
               visitors_count INTEGER NOT NULL DEFAULT 0,
               trade_volume_cents INTEGER NOT NULL DEFAULT 0,
               customs_cents INTEGER NOT NULL DEFAULT 0,
               top_export TEXT NOT NULL,
               top_import TEXT NOT NULL,
               envoy_agent_id INTEGER REFERENCES agents(id),
               summary TEXT NOT NULL
           )"""
    )


def _has_active_festival(conn: sqlite3.Connection, day: int) -> bool:
    try:
        row = conn.execute(
            """SELECT 1 FROM festivals
               WHERE status = 'active' AND ? BETWEEN start_day AND end_day
               LIMIT 1""",
            (int(day),),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def _has_recent_exhibit(conn: sqlite3.Connection, day: int) -> bool:
    try:
        row = conn.execute(
            """SELECT 1 FROM museum_exhibits
               WHERE day_curated BETWEEN ? AND ?
               LIMIT 1""",
            (max(0, int(day) - 7), int(day)),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def daily_regional(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Simulate one day of sister-town tourism and 3-way conserved caravan trade.

    Idempotent per `day`: if `regional_days` already has a row for `day`,
    returns the stored record without duplicating transfers or events.
    """
    ensure_regional_schema(conn)
    day = int(day)

    existing = conn.execute(
        "SELECT * FROM regional_days WHERE day = ?", (day,)
    ).fetchone()
    if existing:
        out = dict(existing)
        out["already_ran"] = True
        return out

    r = rng_for(seed, "regional", day)
    sister_town = r.choice(SISTER_TOWN_NAMES)
    catalog = SISTER_TOWNS[sister_town]
    top_export = r.choice(catalog["exports"])
    top_import = r.choice(catalog["imports"])

    # Tourism wave calculation: weather + festival + recent museum exhibit
    w = weather_today(conn, day)
    cond = str(w.get("condition", "clear"))
    sev = int(w.get("severity", 0))
    has_fest = _has_active_festival(conn, day)
    has_exhibit = _has_recent_exhibit(conn, day)

    base_visitors = r.randint(6, 14)
    if cond in ("clear", "breezy"):
        base_visitors += r.randint(4, 9)
    elif sev >= 2:
        base_visitors = max(1, base_visitors // 3)
    elif cond in ("rain", "snow", "fog"):
        base_visitors = max(2, int(base_visitors * 0.65))

    if has_fest:
        base_visitors += r.randint(12, 22)
    if has_exhibit:
        base_visitors += r.randint(3, 7)

    visitors_count = int(base_visitors)

    # Pick envoy (living adult with highest standing among top 6)
    envoy_rows = conn.execute(
        """SELECT id, name FROM agents
           WHERE alive = 1 AND is_child = 0
           ORDER BY COALESCE(standing, 0) DESC, id ASC
           LIMIT 6"""
    ).fetchall()
    envoy_id: int | None = None
    envoy_name = "Town Envoy"
    if envoy_rows:
        chosen_envoy = r.choice(envoy_rows)
        envoy_id = int(chosen_envoy["id"])
        envoy_name = str(chosen_envoy["name"])

    # 3-way strictly conserved trade clearing:
    # 1) Local import buyers (agent_state.money_cents) -> open commercial businesses
    #    (businesses.balance_cents) + customs duty -> town_account
    # 2) Open businesses with surplus reserves -> export craft bonuses to artisans
    #    (agent_state.money_cents)
    trade_volume_cents = 0
    customs_cents = 0

    open_biz = conn.execute(
        """SELECT b.place_id, b.balance_cents, p.name
           FROM businesses b
           JOIN places p ON p.id = b.place_id
           WHERE b.status = 'open'
           ORDER BY b.ema_traffic DESC, b.place_id ASC
           LIMIT 8"""
    ).fetchall()

    buyers = conn.execute(
        """SELECT a.id, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents >= ?
           ORDER BY s.money_cents DESC, a.id ASC
           LIMIT 12""",
        (MIN_BUYER_WALLET_CENTS,),
    ).fetchall()

    if open_biz and buyers:
        n_orders = min(len(buyers), r.randint(2, 4))
        picked_buyers = r.sample(list(buyers), n_orders)
        for buyer in picked_buyers:
            bid = int(buyer["id"])
            wallet = int(buyer["money_cents"])
            gross = r.randint(600, 1800)
            if wallet < gross:
                continue
            duty = max(50, gross // 10)
            net_biz = gross - duty
            biz = r.choice(open_biz)
            pid = int(biz["place_id"])

            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (gross, bid),
            )
            conn.execute(
                "UPDATE businesses SET balance_cents = balance_cents + ?, revenue_total = revenue_total + ? WHERE place_id = ?",
                (net_biz, net_biz, pid),
            )
            town_credit(conn, duty)
            trade_volume_cents += gross
            customs_cents += duty

    # Export craft bonus from prosperous open businesses -> local artisans
    export_biz = conn.execute(
        """SELECT place_id, balance_cents
           FROM businesses
           WHERE status = 'open' AND balance_cents >= ?
           ORDER BY balance_cents DESC, place_id ASC
           LIMIT 4""",
        (MIN_BUSINESS_EXPORT_RESERVE_CENTS,),
    ).fetchall()
    artisans = conn.execute(
        """SELECT a.id
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
           ORDER BY COALESCE(a.standing, 0) DESC, a.id ASC
           LIMIT 10"""
    ).fetchall()
    if export_biz and artisans:
        biz = r.choice(export_biz)
        artisan = r.choice(artisans)
        bonus = min(int(biz["balance_cents"]) // 4, r.randint(500, 1400))
        if bonus > 0:
            conn.execute(
                "UPDATE businesses SET balance_cents = balance_cents - ? WHERE place_id = ?",
                (bonus, int(biz["place_id"])),
            )
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                (bonus, int(artisan["id"])),
            )
            trade_volume_cents += bonus

    summary = (
        f"Caravan from {sister_town} brought {visitors_count} visitors; "
        f"traded {top_export} for {top_import} (${trade_volume_cents / 100:,.2f} volume, "
        f"${customs_cents / 100:,.2f} customs)."
    )

    conn.execute(
        """INSERT INTO regional_days(
               day, sister_town, visitors_count, trade_volume_cents,
               customs_cents, top_export, top_import, envoy_agent_id, summary
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day,
            sister_town,
            visitors_count,
            trade_volume_cents,
            customs_cents,
            top_export,
            top_import,
            envoy_id,
            summary,
        ),
    )

    # Rate-limited event logging (at most once every EVENT_COOLDOWN_DAYS)
    recent_ev = conn.execute(
        """SELECT 1 FROM events
           WHERE kind IN ('regional_caravan', 'tourism_wave')
             AND day > ?
           LIMIT 1""",
        (day - EVENT_COOLDOWN_DAYS,),
    ).fetchone()
    if not recent_ev and (has_fest or visitors_count >= 16 or trade_volume_cents >= 2500):
        tick = day * TICKS_PER_DAY + 20
        ev_kind = "tourism_wave" if (has_fest or visitors_count >= 22) else "regional_caravan"
        emit(
            conn,
            tick,
            ev_kind,
            a=envoy_id,
            importance=NOTABLE,
            sister_town=sister_town,
            visitors_count=visitors_count,
            trade_volume_cents=trade_volume_cents,
            customs_cents=customs_cents,
            top_export=top_export,
            top_import=top_import,
            text=f"{envoy_name} welcomed {sister_town} caravan ({visitors_count} visitors, {top_export} <-> {top_import})",
        )

    return {
        "day": day,
        "sister_town": sister_town,
        "visitors_count": visitors_count,
        "trade_volume_cents": trade_volume_cents,
        "customs_cents": customs_cents,
        "top_export": top_export,
        "top_import": top_import,
        "envoy_agent_id": envoy_id,
        "summary": summary,
        "already_ran": False,
    }


def regional_summary(conn: sqlite3.Connection, days: int = 14) -> dict[str, Any]:
    """Return recent regional trade & tourism metrics and per-sister-town totals."""
    ensure_regional_schema(conn)
    rows = conn.execute(
        """SELECT rd.*, a.name AS envoy_name
           FROM regional_days rd
           LEFT JOIN agents a ON a.id = rd.envoy_agent_id
           ORDER BY rd.day DESC
           LIMIT ?""",
        (int(days),),
    ).fetchall()
    history = [dict(r) for r in rows]
    tot = conn.execute(
        """SELECT COUNT(*) AS total_days,
                  COALESCE(SUM(visitors_count), 0) AS total_visitors,
                  COALESCE(SUM(trade_volume_cents), 0) AS total_trade_cents,
                  COALESCE(SUM(customs_cents), 0) AS total_customs_cents
           FROM regional_days"""
    ).fetchone()
    by_town_rows = conn.execute(
        """SELECT sister_town,
                  COUNT(*) AS visits,
                  COALESCE(SUM(visitors_count), 0) AS visitors,
                  COALESCE(SUM(trade_volume_cents), 0) AS trade_cents
           FROM regional_days
           GROUP BY sister_town
           ORDER BY trade_cents DESC"""
    ).fetchall()
    return {
        "today": history[0] if history else None,
        "total_days": int(tot["total_days"]),
        "total_visitors": int(tot["total_visitors"]),
        "total_trade_cents": int(tot["total_trade_cents"]),
        "total_customs_cents": int(tot["total_customs_cents"]),
        "by_sister_town": [dict(r) for r in by_town_rows],
        "history": history,
    }
