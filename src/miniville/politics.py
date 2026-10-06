"""The town council: seats, elections, and motions that change real numbers.

A council that debates and does not move anything is decoration. Every policy
here is a number `economy.py` actually reads, so an election has consequences
a resident can feel:

| policy | what it does | bounds |
| --- | --- | --- |
| `levy_rate` | the weekly tax on business reserves | 0.01 - 0.12 |
| `dividend_share` | how much of the levy goes back out as the dividend | 0.10 - 0.90 |
| `rent_multiplier` | scales every household's weekly rent | 0.60 - 1.40 |
| `min_wage` | a floor under every wage paid | 0 - 1.0 (share of the wage band) |

Residents vote for the candidate most like them — same congregation, same
district, similar work — and a councillor's record of *who* elected them
decides how they vote on a motion. A town whose poorest district turns out
elects councillors who vote to hold rent down; a town whose employers turn
out elects councillors who vote to hold the levy down. The left-right axis
is not written into the code; it falls out of who lives where and who works.
"""
from __future__ import annotations

import json
import sqlite3

from .db import get_meta, set_meta
from .events import HISTORIC, NOTABLE, emit
from .rng import rng_for
from .timekeeper import day_of

SEATS = 5
TERM_DAYS = 730                     # a two-year term
MIN_STANDING_TO_STAND = 8           # you need a name to stand at all
MAX_CANDIDATES = 12

# policy -> (default, low, high, step)
POLICIES: dict[str, tuple[float, float, float, float]] = {
    "levy_rate": (0.05, 0.01, 0.12, 0.01),
    "dividend_share": (0.35, 0.10, 0.90, 0.05),
    "rent_multiplier": (1.0, 0.60, 1.40, 0.05),
    "min_wage": (0.0, 0.0, 1.0, 0.10),
}


def policy(conn: sqlite3.Connection, name: str) -> float:
    """The value the economy should use, or the default if never touched."""
    default = POLICIES[name][0]
    raw = get_meta(conn, f"policy_{name}", None)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _clamp(name: str, value: float) -> float:
    _default, low, high, _step = POLICIES[name]
    return max(low, min(high, value))


# --- elections -----------------------------------------------------------------


def next_election_day(conn: sqlite3.Connection) -> int:
    raw = get_meta(conn, "next_election_day", None)
    if raw is None:
        return TERM_DAYS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return TERM_DAYS


def hold_election(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Stand candidates, count votes, seat the winners."""
    day = day_of(tick)
    r = rng_for(seed, "election", day)
    voters = conn.execute(
        """SELECT id, age, household_id, occupation, standing FROM agents
           WHERE alive=1 AND is_child=0 AND age >= 18""").fetchall()
    if len(voters) < SEATS:
        return {"turnout": 0, "seated": []}

    # One seat per district, not a town-wide top five. A town-wide vote elects
    # the best-known residents, who turn out to be the better-off ones: the
    # first council seated five members whose backers were all above the
    # median wallet, so a motion to lower rent lost 2-3 every single time and
    # the lever was dead. Districts give the poor half of town a voice, and
    # the votes start splitting the way the town is actually split.
    districts = [r["district"] for r in conn.execute(
        "SELECT DISTINCT district FROM places WHERE district IS NOT NULL ORDER BY district")]
    if len(districts) < SEATS:
        districts = districts + ["Downtown"] * (SEATS - len(districts))
    candidates: list[dict] = []
    for d in districts[:SEATS]:
        rows = conn.execute(
            f"""SELECT a.id, a.name, a.occupation, a.standing, p.district
                FROM agents a LEFT JOIN places p ON p.id = a.home_place_id
                WHERE a.alive=1 AND a.is_child=0 AND a.age >= 18
                  AND p.district = ? AND COALESCE(a.standing,0) >= ?
                ORDER BY a.standing DESC, a.id LIMIT ?""",
            (d, MIN_STANDING_TO_STAND, 3)).fetchall()
        candidates.extend(dict(c) for c in rows)
    if len(candidates) < SEATS:
        # a district with nobody willing to stand: fall back to the town
        rows = conn.execute(
            f"""SELECT id, name, occupation, standing FROM agents
                WHERE alive=1 AND is_child=0 AND age >= 18
                  AND COALESCE(standing,0) >= ?
                ORDER BY standing DESC, id LIMIT ?""",
            (MIN_STANDING_TO_STAND, MAX_CANDIDATES)).fetchall()
        candidates = [dict(c) for c in rows]
    if len(candidates) < SEATS:
        return {"turnout": 0, "seated": []}

    # what each voter has in common with each candidate
    faith = {r["id"]: r["faith"] for r in conn.execute(
        "SELECT id, faith FROM agents WHERE alive=1")}
    groups_of: dict[int, set[int]] = {}
    for row in conn.execute("SELECT agent_id, group_id FROM memberships"):
        groups_of.setdefault(row["agent_id"], set()).add(row["group_id"])
    district = {r["id"]: r["district"] for r in conn.execute(
        """SELECT a.id, p.district FROM agents a
           LEFT JOIN places p ON p.id = a.home_place_id WHERE a.alive=1""")}

    from .conflict import influence_of
    # influence compounds: a councillor who already has money, a seat and a
    # flock is harder to unseat than a well-liked newcomer
    infl = {c["id"]: influence_of(conn, c["id"]) for c in candidates}
    tally: dict[int, list[int]] = {c["id"]: [] for c in candidates}
    turnout = 0
    for v in voters:
        best, best_score = None, -1.0
        for c in candidates:
            if c["id"] == v["id"]:
                continue
            score = 0.5 + c["standing"] / 40.0 + infl[c["id"]] / 20.0
            if groups_of.get(v["id"], set()) & groups_of.get(c["id"], set()):
                score += 2.0                      # same congregation or club
            if faith.get(v["id"]) and faith.get(v["id"]) == faith.get(c["id"]):
                score += 0.75
            if district.get(v["id"]) and district.get(v["id"]) == district.get(c["id"]):
                score += 1.0                      # the neighbour
            if (v["occupation"] or "") and v["occupation"] == c["occupation"]:
                score += 0.5
            score += r.random() * 0.8             # the unpredictable voter
            if score > best_score:
                best, best_score = c["id"], score
        if best:
            tally[best].append(v["id"])
            turnout += 1

    # each district seats its own winner, so every part of town is represented
    conn.execute("DELETE FROM council")
    by_id = {c["id"]: c for c in candidates}
    ranked = []
    for d in districts[:SEATS]:
        mine = [(cid, b) for cid, b in tally.items()
                if by_id[cid].get("district") == d]
        if mine:
            ranked.append(max(mine, key=lambda kv: (len(kv[1]), -kv[0])))
    if len(ranked) < SEATS:
        for cid, backers in sorted(tally.items(), key=lambda kv: -len(kv[1])):
            if all(cid != r[0] for r in ranked):
                ranked.append((cid, backers))
            if len(ranked) >= SEATS:
                break
    town_wallet = conn.execute(
        """SELECT s.money_cents m FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1 AND a.is_child=0""").fetchall()
    median = sorted(x["m"] for x in town_wallet)[len(town_wallet) // 2] if town_wallet else 0
    seated = []
    for seat, (cid, backers) in enumerate(ranked[:SEATS], start=1):
        if not backers:
            continue
        c = by_id[cid]
        # a district councillor carries their district's lot, not their own
        # voters': the backers of the first council all sat above the town's
        # median wallet even in The Flats, which made every councillor vote
        # like the comfortable and left the rent lever dead
        district_row = conn.execute(
            """SELECT s.money_cents m FROM agent_state s JOIN agents a ON a.id=s.agent_id
               LEFT JOIN places p ON p.id=a.home_place_id
               WHERE a.alive=1 AND a.is_child=0 AND p.district=?""",
            (c.get("district"),)).fetchall()
        wallets = sorted(x["m"] for x in district_row)
        med = wallets[len(wallets) // 2] if wallets else median
        district_adults = conn.execute(
            """SELECT COUNT(*) n FROM agents a LEFT JOIN places p ON p.id=a.home_place_id
               WHERE a.alive=1 AND a.is_child=0 AND p.district=?""",
            (c.get("district"),)).fetchone()["n"]
        out_of_work = conn.execute(
            """SELECT COUNT(*) n FROM agents a LEFT JOIN places p ON p.id=a.home_place_id
               WHERE a.alive=1 AND a.is_child=0 AND p.district=?
                 AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.agent_id=a.id)""",
            (c.get("district"),)).fetchone()["n"]
        conn.execute(
            """INSERT INTO council(seat, agent_id, elected_tick, district,
                   backers, backers_wallet, backers_unemployed)
               VALUES(?,?,?,?,?,?,?)""",
            (seat, cid, tick, c.get("district") or district.get(cid), len(backers),
             med, out_of_work / max(1, district_adults)))
        seated.append({"seat": seat, "name": c["name"], "votes": len(backers),
                       "district": district.get(cid)})

    # the runners-up do not forget: a lost election is the town's most
    # reliable source of a lasting grudge
    for d in districts[:SEATS]:
        mine = [(cid, b) for cid, b in tally.items() if by_id[cid].get("district") == d]
        if len(mine) < 2:
            continue
        mine.sort(key=lambda kv: -len(kv[1]))
        winner, loser = mine[0][0], mine[1][0]
        lo, hi = min(winner, loser), max(winner, loser)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label)
               VALUES(?,?,?,?,0,'rival')
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 affinity = MIN(-25.0, relationships.affinity - 15.0),
                 label = 'rival'""", (lo, hi, 20.0, -25.0))

    set_meta(conn, "next_election_day", str(day + TERM_DAYS))
    summary = "; ".join(f"{s['name']} ({s['votes']} votes)" for s in seated)
    conn.execute(
        "INSERT INTO elections(tick,day,turnout,summary) VALUES(?,?,?,?)",
        (tick, day, turnout, summary))
    # short enough to read in a narrative; the tally lives in the data
    emit(conn, tick, "town_event", importance=HISTORIC,
         text=(f"the town went to the polls and returned {len(seated)} "
               f"councillors, {seated[0]['name']} leading with "
               f"{seated[0]['votes']} votes"),
         tag="election", turnout=turnout, seats=seated, summary=summary)
    return {"turnout": turnout, "seated": seated}


# --- motions -------------------------------------------------------------------


def council(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT c.seat, c.agent_id, c.district, c.backers, c.backers_wallet,
                  c.backers_unemployed, c.elected_tick, a.name
           FROM council c LEFT JOIN agents a ON a.id=c.agent_id
           ORDER BY c.seat""")]


def _votes_yes(motion_policy: str, direction: int, member: dict,
               median_wallet: int, town_unemployment: float) -> bool:
    """How a councillor votes, given the district that sent them.

    One axis, and it falls out of the town's own books: rent and the business
    levy *are* the town's income — they pay the public payroll and fund the
    civic dividend — so a district that leans on the town wants them up, and
    a district that pays its own way wants them down. Wages are the other
    half: a poorer district wants the floor raised.

    (The first version of this said "whoever is not poor wants rent up",
    which is perverse — it had the comfortable districts voting themselves a
    rent rise. Rent is not a price here, it is a tax base.)
    """
    poor = member["backers_wallet"] < median_wallet
    dependent = member["backers_unemployed"] > town_unemployment
    if motion_policy == "rent_multiplier":
        return (direction > 0) == dependent
    if motion_policy == "dividend_share":
        return (direction > 0) == dependent
    if motion_policy == "levy_rate":
        return (direction > 0) == dependent
    if motion_policy == "min_wage":
        return (direction > 0) == poor
    return False


def consider_motion(conn: sqlite3.Connection, tick: int, seed: str) -> dict | None:
    """One motion a month, decided by the seated council."""
    members = council(conn)
    if not members:
        return None
    day = day_of(tick)
    r = rng_for(seed, "motion", day)
    name = r.choice(sorted(POLICIES))
    _default, low, high, step = POLICIES[name]
    direction = r.choice([-1, 1])
    value = _clamp(name, policy(conn, name) + direction * step)
    if value == policy(conn, name):
        return None                              # already at the rail

    wallets = conn.execute(
        """SELECT s.money_cents m FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1 AND a.is_child=0""").fetchall()
    median_wallet = (sorted(x["m"] for x in wallets)[len(wallets) // 2]
                     if wallets else 0)
    total = conn.execute(
        "SELECT COUNT(*) n FROM agents WHERE alive=1 AND is_child=0 AND age>=18"
    ).fetchone()["n"]
    employed = conn.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
    town_unemployment = max(0.0, (total - employed) / max(1, total))

    yes = [m for m in members
           if _votes_yes(name, direction, m, median_wallet, town_unemployment)]
    passed = len(yes) * 2 > len(members)
    if passed:
        set_meta(conn, f"policy_{name}", f"{value:.4f}")
    verb = "raised" if direction > 0 else "lowered"
    text = {"levy_rate": f"the business levy to {value * 100:.0f}%",
            "dividend_share": f"the civic dividend to {value * 100:.0f}% of the levy",
            "rent_multiplier": f"rent to {value * 100:.0f}% of its rate",
            "min_wage": f"the wage floor to {value * 100:.0f}%"}[name]
    emit(conn, tick, "town_event", importance=NOTABLE if passed else 1,
         text=(f"the council {verb} {text}" if passed
               else f"the council rejected a motion to {verb[:-1]} {text}"),
         tag="motion_passed" if passed else "motion_rejected",
         policy=name, direction=direction, passed=passed, value=value, votes_for=len(yes),
         votes_against=len(members) - len(yes))
    conn.execute(
        """INSERT INTO motions(tick,day,policy,direction,value,passed,
               votes_for,votes_against) VALUES(?,?,?,?,?,?,?,?)""",
        (tick, day, name, direction, value, int(passed), len(yes),
         len(members) - len(yes)))
    return {"policy": name, "direction": direction, "value": value,
            "passed": passed, "for": len(yes), "against": len(members) - len(yes)}


def due(conn: sqlite3.Connection, tick: int) -> dict:
    """Run whatever the calendar says is due: elections, then motions."""
    out: dict = {}
    day = day_of(tick)
    if day >= next_election_day(conn):
        out["election"] = hold_election(conn, tick, seed_for(conn))
        return out
    # a councillor who dies leaves a seat; the town fills it rather than
    # keeping a seat warm for a corpse until the next scheduled election
    vacant = conn.execute(
        """SELECT c.seat, a.name FROM council c
           LEFT JOIN agents a ON a.id=c.agent_id
           WHERE a.id IS NULL OR a.alive=0""").fetchall()
    if vacant:
        names = ", ".join(r["name"] or "the late member" for r in vacant)
        conn.execute("DELETE FROM council WHERE seat IN "
                     f"({','.join('?' * len(vacant))})", [r["seat"] for r in vacant])
        emit(conn, tick, "town_event", importance=NOTABLE,
             text=f"{names} left the council; the town will vote again",
             tag="seat_vacated", seats=len(vacant))
        out["election"] = hold_election(conn, tick, seed_for(conn))
        return out
    if day % 30 == 0:
        m = consider_motion(conn, tick, seed_for(conn))
        if m:
            out["motion"] = m
    return out


def seed_for(conn: sqlite3.Connection) -> str:
    return get_meta(conn, "seed", "miniville")
