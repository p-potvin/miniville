"""Peer-backed Town Credit Union & Personal/Business Loans (v0.51).

Every dollar loaned moves strictly between resident wallets and business
tills so `total_money_cents` (`wallets + tills + purse`) stays 100% conserved.
Wealthy residents (`money_cents > $3,000`) extend 0%-interest community mutual
loans either to struggling business proprietors (`place_id` set, capitalizing
negative tills) or to employed residents facing rent distress / thin wallets.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

MIN_LENDER_WALLET_CENTS = 300_000
MAX_LENDER_ACTIVE_LOANS = 2
MAX_WEEKLY_ORIGINATIONS = 3
DEFAULT_AFTER_MISSED_WEEKS = 6


def _bump_rel(conn: sqlite3.Connection, a_id: int, b_id: int, tick: int,
              fam_delta: float, aff_delta: float) -> None:
    lo, hi = min(a_id, b_id), max(a_id, b_id)
    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
               label,interactions,last_met_tick)
           VALUES(?, ?, ?, ?, 0.0, 'friend', 1, ?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity = MAX(0.0, MIN(100.0, relationships.familiarity + ?)),
             affinity = MAX(-100.0, MIN(100.0, relationships.affinity + ?)),
             interactions = relationships.interactions + 1,
             last_met_tick = excluded.last_met_tick""",
        (lo, hi, max(0.0, fam_delta), aff_delta, tick, fam_delta, aff_delta),
    )


def collect_payments(conn: sqlite3.Connection, tick: int, seed: str) -> dict[str, int]:
    """Process weekly repayments, pay-offs, and defaults on active loans."""
    rows = conn.execute(
        """SELECT l.*,
                  la.name AS lender_name, la.alive AS lender_alive,
                  ba.name AS borrower_name, ba.alive AS borrower_alive,
                  bs.money_cents AS borrower_wallet,
                  COALESCE(b.balance_cents, 0) AS biz_balance,
                  COALESCE(p.name, '') AS place_name
           FROM loans l
           JOIN agents la ON la.id = l.lender_id
           JOIN agents ba ON ba.id = l.borrower_id
           JOIN agent_state bs ON bs.agent_id = l.borrower_id
           LEFT JOIN businesses b ON b.place_id = l.place_id
           LEFT JOIN places p ON p.id = l.place_id
           WHERE l.status = 'active'
           ORDER BY l.id"""
    ).fetchall()

    collected_cents = 0
    repaid_n = 0
    defaulted_n = 0

    for row in rows:
        lid = int(row["id"])
        lender_id = int(row["lender_id"])
        borrower_id = int(row["borrower_id"])
        place_id = row["place_id"]

        if not row["lender_alive"]:
            conn.execute(
                "UPDATE loans SET status='cancelled', closed_tick=? WHERE id=?",
                (tick, lid),
            )
            continue
        if not row["borrower_alive"]:
            conn.execute(
                "UPDATE loans SET status='defaulted', closed_tick=? WHERE id=?",
                (tick, lid),
            )
            defaulted_n += 1
            continue

        remaining = int(row["remaining_cents"])
        scheduled = int(row["weekly_payment_cents"])
        available_biz = max(0, int(row["biz_balance"])) if place_id else 0
        available_wallet = max(0, int(row["borrower_wallet"]))

        if place_id and available_biz > 0:
            pay = min(scheduled, remaining, available_biz)
            conn.execute(
                "UPDATE businesses SET balance_cents = balance_cents - ? WHERE place_id = ?",
                (pay, place_id),
            )
        elif available_wallet > 0:
            pay = min(scheduled, remaining, available_wallet)
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                (pay, borrower_id),
            )
        else:
            pay = 0

        if pay > 0:
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                (pay, lender_id),
            )
            collected_cents += pay
            new_rem = remaining - pay
            if new_rem <= 0:
                conn.execute(
                    "UPDATE loans SET remaining_cents = 0, missed_weeks = 0, "
                    "status = 'repaid', closed_tick = ? WHERE id = ?",
                    (tick, lid),
                )
                _bump_rel(conn, borrower_id, lender_id, tick, 5.0, 8.0)
                scope = row["place_name"] or "personal expenses"
                emit(
                    conn, tick, "life_event", place_id=place_id,
                    a=borrower_id, b=lender_id, importance=NOTABLE,
                    text=f"paid off their Credit Union loan to {row['lender_name']} ({scope})",
                    tag="loan_repaid", amount_cents=int(row["principal_cents"]),
                )
                remember(
                    conn, borrower_id, tick,
                    f"I paid off my loan from {row['lender_name']} in full.",
                    kind="personal", importance=3,
                )
                repaid_n += 1
            else:
                conn.execute(
                    "UPDATE loans SET remaining_cents = ?, missed_weeks = 0 WHERE id = ?",
                    (new_rem, lid),
                )
        else:
            missed = int(row["missed_weeks"] or 0) + 1
            if missed >= DEFAULT_AFTER_MISSED_WEEKS:
                conn.execute(
                    "UPDATE loans SET missed_weeks = ?, status = 'defaulted', closed_tick = ? WHERE id = ?",
                    (missed, tick, lid),
                )
                _bump_rel(conn, borrower_id, lender_id, tick, 2.0, -15.0)
                emit(
                    conn, tick, "life_event", place_id=place_id,
                    a=borrower_id, b=lender_id, importance=NOTABLE,
                    text=f"defaulted on a Credit Union loan from {row['lender_name']}",
                    tag="loan_default", remaining_cents=remaining,
                )
                defaulted_n += 1
            else:
                conn.execute(
                    "UPDATE loans SET missed_weeks = ? WHERE id = ?",
                    (missed, lid),
                )

    return {"collected_cents": collected_cents, "repaid": repaid_n, "defaulted": defaulted_n}


def originate_loans(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Match struggling residents or businesses with wealthy peer lenders."""
    day = day_of(tick)
    r = rng_for(seed, "credit_union", day)

    active_borrowers = {
        int(x["borrower_id"])
        for x in conn.execute("SELECT borrower_id FROM loans WHERE status = 'active'").fetchall()
    }
    lent_counts: dict[int, int] = {
        int(x["lender_id"]): int(x["n"])
        for x in conn.execute(
            "SELECT lender_id, COUNT(*) n FROM loans WHERE status = 'active' GROUP BY lender_id"
        ).fetchall()
    }
    lenders = [
        dict(row)
        for row in conn.execute(
            """SELECT a.id, a.name, s.money_cents
               FROM agents a
               JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0 AND s.money_cents > ?
               ORDER BY s.money_cents DESC, a.id ASC LIMIT 30""",
            (MIN_LENDER_WALLET_CENTS,),
        ).fetchall()
        if lent_counts.get(int(row["id"]), 0) < MAX_LENDER_ACTIVE_LOANS
    ]
    if not lenders:
        return 0

    rivals: set[tuple[int, int]] = {
        (min(int(x["a_id"]), int(x["b_id"])), max(int(x["a_id"]), int(x["b_id"])))
        for x in conn.execute("SELECT a_id, b_id FROM relationships WHERE affinity <= -20").fetchall()
    }

    # Priority 1: Open business owners whose till is negative
    biz_needs = conn.execute(
        """SELECT a.id AS borrower_id, a.name AS borrower_name,
                  b.place_id, b.balance_cents, p.name AS place_name
           FROM businesses b
           JOIN places p ON p.id = b.place_id
           JOIN agents a ON a.id = b.owner_id
           WHERE b.status = 'open' AND b.balance_cents < -5000 AND a.alive = 1
           ORDER BY b.balance_cents ASC LIMIT 8"""
    ).fetchall()

    # Priority 2: Employed residents with rent arrears or thin wallets
    personal_needs = conn.execute(
        """SELECT a.id AS borrower_id, a.name AS borrower_name, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           JOIN jobs j ON j.agent_id = a.id
           LEFT JOIN rent_arrears ra ON ra.household_id = a.household_id
           WHERE a.alive = 1 AND a.is_child = 0
             AND (ra.missed_payments > 0 OR s.money_cents < 25000)
           ORDER BY COALESCE(ra.missed_payments, 0) DESC, s.money_cents ASC
           LIMIT 12"""
    ).fetchall()

    requests: list[tuple[int, str, int | None, str | None, int]] = []
    for b in biz_needs:
        bid = int(b["borrower_id"])
        if bid not in active_borrowers:
            amt = min(150_000, max(50_000, -int(b["balance_cents"]) // 2))
            amt = (amt // 5000) * 5000
            requests.append((bid, b["borrower_name"], int(b["place_id"]), b["place_name"], amt))
    for p in personal_needs:
        pid = int(p["borrower_id"])
        if pid not in active_borrowers and all(x[0] != pid for x in requests):
            requests.append((pid, p["borrower_name"], None, None, 35_000))

    issued = 0
    for borrower_id, borrower_name, place_id, place_name, principal in requests:
        if issued >= MAX_WEEKLY_ORIGINATIONS:
            break
        eligible_lenders = [
            ld for ld in lenders
            if ld["id"] != borrower_id
            and ld["money_cents"] >= principal + 150_000
            and lent_counts.get(ld["id"], 0) < MAX_LENDER_ACTIVE_LOANS
            and (min(ld["id"], borrower_id), max(ld["id"], borrower_id)) not in rivals
        ]
        if not eligible_lenders:
            continue
        lender = r.choice(eligible_lenders[:5])
        lender_id = int(lender["id"])
        lender["money_cents"] -= principal
        lent_counts[lender_id] = lent_counts.get(lender_id, 0) + 1
        active_borrowers.add(borrower_id)

        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
            (principal, lender_id),
        )
        if place_id is not None:
            conn.execute(
                "UPDATE businesses SET balance_cents = balance_cents + ? WHERE place_id = ?",
                (principal, place_id),
            )
            purpose = place_name or "their business"
        else:
            conn.execute(
                "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
                (principal, borrower_id),
            )
            purpose = "household expenses"

        weekly_pay = max(2500, (principal + 7) // 8)
        conn.execute(
            """INSERT INTO loans(lender_id, borrower_id, place_id, principal_cents,
                   remaining_cents, weekly_payment_cents, started_tick, status)
               VALUES(?, ?, ?, ?, ?, ?, ?, 'active')""",
            (lender_id, borrower_id, place_id, principal, principal, weekly_pay, tick),
        )
        _bump_rel(conn, borrower_id, lender_id, tick, 4.0, 5.0)
        emit(
            conn, tick, "life_event", place_id=place_id,
            a=lender_id, b=borrower_id, importance=MINOR,
            text=f"extended a ${principal / 100:,.0f} Credit Union loan to {borrower_name} for {purpose}",
            tag="loan_issued", principal_cents=principal,
        )
        issued += 1

    return issued


def due(conn: sqlite3.Connection, tick: int, seed: str = "miniville") -> dict[str, int]:
    """Weekly Credit Union settlement and loan origination (Mondays)."""
    day = day_of(tick)
    if day % 7 != 0 or day == 0:
        return {"collected_cents": 0, "repaid": 0, "defaulted": 0, "issued": 0}
    col = collect_payments(conn, tick, seed)
    issued = originate_loans(conn, tick, seed)
    return {**col, "issued": issued}


def list_loans(conn: sqlite3.Connection, status: str = "active",
               limit: int = 50) -> list[dict[str, Any]]:
    where = "" if status == "all" else "WHERE l.status = ?"
    params: tuple = (limit,) if status == "all" else (status, limit)
    rows = conn.execute(
        f"""SELECT l.*,
                   la.name AS lender_name,
                   ba.name AS borrower_name,
                   COALESCE(p.name, '') AS place_name
            FROM loans l
            JOIN agents la ON la.id = l.lender_id
            JOIN agents ba ON ba.id = l.borrower_id
            LEFT JOIN places p ON p.id = l.place_id
            {where}
            ORDER BY CASE WHEN l.status='active' THEN 0 ELSE 1 END, l.id DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def loans_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        r["status"]: {"count": int(r["n"]), "principal_cents": int(r["p"]), "remaining_cents": int(r["rem"])}
        for r in conn.execute(
            """SELECT status, COUNT(*) n,
                      COALESCE(SUM(principal_cents), 0) p,
                      COALESCE(SUM(remaining_cents), 0) rem
               FROM loans GROUP BY status"""
        ).fetchall()
    }
    return {
        "counts": counts,
        "loans": list_loans(conn, status="all", limit=50),
    }
