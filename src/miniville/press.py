"""Public opinion, and the paper that moves it.

The Gazette could always print a false claim, but the only cost was its own
credibility number: nothing it printed changed a vote. This module gives the
paper readers, gives the town an opinion on each lever the council can pull,
and lets the paper push that opinion.

Two things move opinion each week. The town's own experience pulls it toward
its material interest — unemployment makes the town want a higher wage floor
and a bigger dividend, an empty purse makes it want the levy up, rent arrears
make it want rent down. The Gazette pushes it toward its editorial line, and
the size of that push is its reach: readers per adult, which is penetration
times credibility. So an editorial line can carry a close motion, and a paper
that has been caught lying loses the reach to do it.

Nothing here changes what happened. It changes what the town wants to happen
next, which is what a council responds to.
"""
from __future__ import annotations

import sqlite3

# The share of adults a fully credible paper reaches. A town of 300 adults and
# a paper nobody doubts has about 165 readers.
PENETRATION = 0.55
# The weekly opinion push per unit of reach (reach is readers/adults, 0..0.55).
# At full reach this is 0.19/week, which against the material drift below lets
# the paper hold opinion ~0.77 past where the town's own experience would take
# it — enough to swing a councillor whose district leans the other way.
PRESS_PUSH = 0.35
# Fraction of the gap to the material interest the town closes each week.
MATERIAL_DRIFT = 0.25
# How strongly a councillor weighs town opinion against their district's own
# interest. Opinion only flips a member when |direction * support| > 1/this.
OPINION_WEIGHT = 1.5
OPINION_MIN, OPINION_MAX = -1.0, 1.0

# What an editorial line wants done to each lever: +1 raise, -1 lower. The
# paper's agenda, not the town's. Kept here so the front page and the opinion
# model cannot drift apart.
POLICY_PREFERENCE: dict[str, dict[str, int]] = {
    "business": {"levy_rate": -1, "dividend_share": -1,
                 "rent_multiplier": -1, "min_wage": -1, "pension": -1},
    "working": {"levy_rate": 1, "dividend_share": 1,
                "rent_multiplier": -1, "min_wage": 1, "pension": 1},
    "establishment": {},
    "community": {},
}


def line_stance(line: str, policy: str) -> int:
    """What the paper's line wants done to one lever, or 0 for no opinion."""
    return POLICY_PREFERENCE.get(line, {}).get(policy, 0)


def _clamp(x: float) -> float:
    return max(OPINION_MIN, min(OPINION_MAX, x))


# --- readers -------------------------------------------------------------------


def eligible_adults(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """SELECT COUNT(*) n FROM agents
           WHERE alive=1 AND is_child=0 AND age >= 18""").fetchone()["n"]


def credibility(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT credibility FROM newspaper_profile WHERE id=1").fetchone()
    return float(row["credibility"]) if row else 1.0


def readers(conn: sqlite3.Connection) -> int:
    """How many adults read the paper: penetration scaled by its credibility."""
    share = PENETRATION * max(0.0, min(1.0, credibility(conn)))
    return round(eligible_adults(conn) * share)


def reach(conn: sqlite3.Connection) -> float:
    return readers(conn) / max(1, eligible_adults(conn))


# --- opinion -------------------------------------------------------------------


def opinion(conn: sqlite3.Connection, policy: str) -> float:
    """Support for raising this lever, in [-1, 1]. 0 is indifference."""
    row = conn.execute(
        "SELECT support FROM opinion WHERE policy=?", (policy,)).fetchone()
    return float(row["support"]) if row else 0.0


def all_opinion(conn: sqlite3.Connection) -> dict[str, float]:
    from .politics import POLICIES
    stored = {r["policy"]: float(r["support"])
              for r in conn.execute("SELECT policy, support FROM opinion")}
    return {p: stored.get(p, 0.0) for p in sorted(POLICIES)}


def _set(conn: sqlite3.Connection, policy: str, value: float) -> None:
    conn.execute(
        """INSERT INTO opinion(policy, support) VALUES(?,?)
           ON CONFLICT(policy) DO UPDATE SET support=excluded.support""",
        (policy, value))


def material_target(conn: sqlite3.Connection) -> dict[str, float]:
    """Where the town's own conditions pull opinion, per lever.

    Every term is a number the town can feel: unemployment, how many pensioners
    lean on the purse, how empty the purse is, and how many households are
    behind on rent. Deliberately legible rather than fitted.
    """
    from . import economy
    u = economy.unemployment(conn)
    adults = eligible_adults(conn)
    pensioners = conn.execute(
        """SELECT COUNT(*) n FROM agents a
           WHERE a.alive=1 AND a.is_child=0 AND a.age >= 65
             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.agent_id=a.id)""").fetchone()["n"]
    senior_share = pensioners / max(1, adults)
    buffer = economy.PURSE_BUFFER_WEEKS * (
        economy.public_payroll_week(conn) + economy.pension_bill_week(conn))
    purse_ratio = economy.town_balance(conn) / max(1, buffer)
    arrears = conn.execute("SELECT COUNT(*) n FROM rent_arrears").fetchone()["n"]
    households = conn.execute("SELECT COUNT(*) n FROM households").fetchone()["n"]
    arrears_share = arrears / max(1, households)
    return {
        "min_wage": _clamp((u - 0.08) / 0.10),
        "dividend_share": _clamp((u - 0.05) / 0.12),
        "pension": _clamp(senior_share / 0.30),
        "levy_rate": _clamp(1.0 - purse_ratio),
        "rent_multiplier": _clamp((0.02 - arrears_share) / 0.05),
    }


def press_push(conn: sqlite3.Connection, line: str) -> dict[str, float]:
    """The paper's weekly push on opinion: its line, scaled by its reach."""
    r = reach(conn)
    return {p: r * PRESS_PUSH * line_stance(line, p)
            for p in POLICY_PREFERENCE.get(line, {})}


def update(conn: sqlite3.Connection, tick: int) -> dict[str, float]:
    """One week: the town's experience pulls opinion, the paper pushes it."""
    from .politics import POLICIES
    profile = conn.execute(
        "SELECT editorial_line FROM newspaper_profile WHERE id=1").fetchone()
    line = profile["editorial_line"] if profile else "community"
    target = material_target(conn)
    push = press_push(conn, line)
    out: dict[str, float] = {}
    for policy in POLICIES:
        cur = opinion(conn, policy)
        want = target.get(policy, 0.0)
        nxt = _clamp(cur + MATERIAL_DRIFT * (want - cur) + push.get(policy, 0.0))
        _set(conn, policy, nxt)
        out[policy] = round(nxt, 4)
    return out
