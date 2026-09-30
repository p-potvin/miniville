"""Favors, debts, and bounded drama. Hooks called from encounters.interact().

Favors: on a warm interaction, a resident may ask a favor; acceptance depends
on mood + relationship. Accepted favors mint a debts row; the next friendly
reunion gives the debtor a chance to repay (event + affinity bump).

Drama (meta.drama_enabled=1, default on): a partnered resident flirting with
someone they have high non-spouse romance with can trigger an affair event,
which witnesses may gossip about; the spouse may discover it (betrayal event,
mood hit, separation).
"""
from __future__ import annotations

import sqlite3

from .db import get_meta
from .events import MAJOR, MINOR, NOTABLE, emit
from .rng import rng_for

P_FAVOR_ASK = 0.08
P_REPAY = 0.35
P_AFFAIR = 0.02
P_DISCOVERY = 0.15

FAVOR_KINDS = [
    "borrowed a tool", "gave a lift into town", "watched the kids",
    "loaned a few dollars", "covered a shift", "helped fix a fence",
    "brought over a casserole",
]


def _mood(conn: sqlite3.Connection, agent_id: int) -> str:
    r = conn.execute(
        "SELECT mood FROM agent_state WHERE agent_id=?", (agent_id,)).fetchone()
    return r["mood"] if r else "content"


def _aff(conn: sqlite3.Connection, agent_id: int, delta: float) -> None:
    conn.execute(
        "UPDATE agent_state SET social=max(0,min(100,social+?)) WHERE agent_id=?",
        (delta, agent_id))


def drama_enabled(conn: sqlite3.Connection) -> bool:
    return get_meta(conn, "drama_enabled", "1") == "1"


def maybe_ask_favor(conn: sqlite3.Connection, a: sqlite3.Row, b: sqlite3.Row,
                    place_id: int, tick: int, tone: str, r) -> None:
    """Warm chat -> favor ask; accept mints a debt, decline sours slightly."""
    if tone not in ("warm", "delightful", "engaging", "pleasant"):
        return
    if r.random() >= P_FAVOR_ASK:
        return
    asker, target = (a, b) if r.random() < 0.5 else (b, a)
    kind = r.choice(FAVOR_KINDS)
    mood = _mood(conn, target["id"])
    accept_p = {"delighted": 0.9, "content": 0.7, "bored": 0.45,
                "lonely": 0.55, "miserable": 0.15}.get(mood, 0.5)
    if r.random() < accept_p:
        conn.execute(
            "INSERT INTO debts(debtor_id,creditor_id,kind,created_tick) VALUES(?,?,?,?)",
            (asker["id"], target["id"], kind, tick))
        emit(conn, tick, "favor", place_id=place_id, a=target["id"], b=asker["id"],
             importance=MINOR, favor=kind)
        _aff(conn, target["id"], 2)
    else:
        emit(conn, tick, "favor", place_id=place_id, a=asker["id"], b=target["id"],
             importance=MINOR, favor=kind, declined=True)


def maybe_repay_debt(conn: sqlite3.Connection, a_id: int, b_id: int,
                     place_id: int, tick: int, tone: str, r) -> None:
    """If either party owes the other, a friendly reunion may settle it."""
    if tone in ("hostile", "tense"):
        return
    for debtor, creditor in ((a_id, b_id), (b_id, a_id)):
        debt = conn.execute(
            """SELECT id, kind FROM debts
               WHERE debtor_id=? AND creditor_id=? AND repaid_tick IS NULL
               ORDER BY created_tick LIMIT 1""",
            (debtor, creditor)).fetchone()
        if debt and r.random() < P_REPAY:
            conn.execute("UPDATE debts SET repaid_tick=? WHERE id=?",
                         (tick, debt["id"]))
            emit(conn, tick, "favor_repaid", place_id=place_id,
                 a=debtor, b=creditor, importance=MINOR, favor=debt["kind"])
            _aff(conn, creditor, 3)


def _spouse_of(conn: sqlite3.Connection, agent_id: int) -> int | None:
    row = conn.execute(
        """SELECT CASE WHEN a_id=? THEN b_id ELSE a_id END s
           FROM relationships WHERE (a_id=? OR b_id=?) AND label='spouse'""",
        (agent_id, agent_id, agent_id)).fetchone()
    return row["s"] if row else None


def _marital_status(conn: sqlite3.Connection, agent_id: int) -> str:
    r = conn.execute(
        "SELECT marital_status FROM agents WHERE id=?", (agent_id,)).fetchone()
    return r["marital_status"] if r else ""


def maybe_affair(conn: sqlite3.Connection, a: sqlite3.Row, b: sqlite3.Row,
                 rel_label: str, affinity: float, familiarity: float,
                 place_id: int, tick: int, r, tone: str) -> None:
    """Partnered agent + emotionally close non-spouse -> rare affair event.

    Uses affinity/familiarity (not romance — romance growth is suppressed for
    married agents by design, which would make a romance trigger unreachable).
    Only warm/delightful tones count. Discovery happens in spouse_discovery().
    """
    if (not drama_enabled(conn) or rel_label in ("spouse", "rival")
            or tone not in ("warm", "delightful")
            or affinity < 40 or familiarity < 10):
        return
    a_married = _marital_status(conn, a["id"]) in (
        "married_present", "married", "married_spouse_present") or _spouse_of(
        conn, a["id"]) is not None
    if not a_married:
        return
    if r.random() < P_AFFAIR:
        emit(conn, tick, "affair", place_id=place_id, a=a["id"], b=b["id"],
             importance=NOTABLE)


def spouse_discovery(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """Daily pass: spouses may discover recent affairs -> betrayal + split.

    Called once per day from the life lottery. Returns # of discoveries."""
    if not drama_enabled(conn):
        return 0
    day = tick // 48
    affairs = conn.execute(
        "SELECT id, a_id, b_id, place_id FROM events WHERE kind='affair' AND day=?",
        (day - 1,)).fetchall()  # yesterday's affairs surface today
    n = 0
    for ev in affairs:
        for cheater, fling in ((ev["a_id"], ev["b_id"]), (ev["b_id"], ev["a_id"])):
            spouse = _spouse_of(conn, cheater)
            if not spouse:
                continue
            r = rng_for(seed, "betrayal", ev["id"], cheater, day)
            if r.random() >= P_DISCOVERY:
                continue
            emit(conn, tick, "betrayal", a=spouse, b=cheater,
                 importance=MAJOR, with_id=fling)
            conn.execute(
                "UPDATE agent_state SET mood='miserable', stress=min(100,stress+40) "
                "WHERE agent_id=?", (spouse,))
            if r.random() < 0.5:
                lo, hi = min(cheater, spouse), max(cheater, spouse)
                conn.execute(
                    "UPDATE relationships SET label='estranged', romance=0 "
                    "WHERE a_id=? AND b_id=?", (lo, hi))
                for aid in (cheater, spouse):
                    conn.execute(
                        "UPDATE agents SET marital_status='separated' WHERE id=?",
                        (aid,))
                emit(conn, tick, "life_event", a=cheater, b=spouse,
                     importance=MAJOR, text="separated after the affair came out",
                     tag="separation")
            n += 1
    return n
