"""The town labour market: staffing targets, hiring, turnover and retirement.

Bootstrap hands every adult a job and nothing else ever hires. Every other
path only *destroys* jobs — a business that closes, a venue shock, a life
event, a death — so the town's employment rate could only fall: a laid-off
resident stayed unemployed forever, an 18th birthday produced an unemployed
adult, and a reopened business never rehired. This module keeps the market
turning over:

* every venue wants a number of staff derived from its capacity and its
  customer traffic (a busy grocer needs a real crew; the town hall needs a
  handful of clerks),
* a weekly hiring pass fills vacancies from the unemployed, best occupation
  match first, at a bounded rate,
* a little voluntary turnover drains overstaffed venues so the distribution
  drifts toward the targets rather than snapping to them,
* residents retire in their late sixties and free their posts.

Everything is seeded and deterministic; the rng streams are per pass so the
existing draws (encounters, births, mortality) are untouched.
"""
from __future__ import annotations

import json
import sqlite3

from .economy import WAGE_INDEX_MIN, WAGE_MAX_CENTS, WAGE_MIN_CENTS, wage_index
from .events import MINOR, NOTABLE, emit
from .rng import rng_for
from .timekeeper import day_of
from .world import workplace_tags_for

# a venue's crew, scaled by what it has to do
COMMERCIAL_TAGS = {"food", "drink", "retail", "coffee", "arts", "nightlife", "fitness"}
STAFF_MAX = 60
EMPLOYMENT_RATE = 0.92       # share of working-age adults the town can employ

# market churn
WEEKLY_HIRE_SHARE = 0.05     # base share of the workforce hired per week
SEPARATION_MARGIN = 1.5      # hiring must out-pace the town's firing rate, or
                             # the market sheds jobs faster than it refills them
EXCESS_SHED_SHARE = 0.03     # share of an overstaffed venue's surplus that leaves
MIN_WAGE_FLOOR = 0.8         # never pay below this fraction of the baseline band

# retirement
RETIRE_AGE = 65
RETIRE_ANNUAL = 0.30         # chance per year at 65-69
RETIRE_ANNUAL_OLD = 0.55     # ...and at 70+


def venue_weight(capacity: int, traffic: float, tags: set[str]) -> float:
    """How much of the town's work a venue carries: a busy shop carries a lot,
    a town hall very little."""
    if tags & COMMERCIAL_TAGS:
        return 3 + capacity / 3 + traffic / 10
    return 3 + capacity / 10


def venue_targets(conn: sqlite3.Connection) -> dict[int, int]:
    """place_id -> target headcount for every staffable (non-home) venue.

    Weights come from capacity and customer traffic, then they are scaled so
    the town's posts add up to `EMPLOYMENT_RATE` of its working-age adults:
    the *rate* is a property of the town (bootstrap hires 92% of adults) while
    the *distribution* follows demand. Letting the weights set the total too
    would have shrunk a 636-person town to 317 jobs — 36% unemployment — the
    moment the town hall's bootstrap pile-up was corrected.
    """
    rows = conn.execute(
        """SELECT p.id, p.capacity, p.tags, COALESCE(b.ema_traffic, 0) traffic
           FROM places p LEFT JOIN businesses b ON b.place_id = p.id
           WHERE p.kind != 'home'""").fetchall()
    adults = conn.execute(
        "SELECT COUNT(*) n FROM agents WHERE alive=1 AND is_child=0 AND age < ?",
        (RETIRE_AGE,)).fetchone()["n"]
    weights = {r["id"]: venue_weight(r["capacity"], r["traffic"],
                                     set(json.loads(r["tags"] or "[]")))
               for r in rows}
    total_w = sum(weights.values()) or 1.0
    budget = max(len(weights) * 2, int(adults * EMPLOYMENT_RATE))
    scale = budget / total_w
    return {pid: max(2, min(STAFF_MAX, round(w * scale)))
            for pid, w in weights.items()}


def vacancies(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """(place_id, open_slots) for open venues below their target."""
    targets = venue_targets(conn)
    staff: dict[int, int] = {}
    for r in conn.execute("SELECT place_id, COUNT(*) n FROM jobs GROUP BY place_id"):
        staff[r["place_id"]] = r["n"]
    open_places = {r["place_id"] for r in conn.execute(
        """SELECT p.id place_id FROM places p LEFT JOIN businesses b ON b.place_id=p.id
           WHERE p.kind != 'home' AND COALESCE(b.status,'open')='open'""")}
    out = []
    for pid, target in sorted(targets.items()):
        if pid not in open_places:
            continue
        slots = target - staff.get(pid, 0)
        if slots > 0:
            out.append((pid, slots))
    return out


def _wage_for(conn: sqlite3.Connection, r) -> int:
    idx = max(WAGE_INDEX_MIN, min(1.6, wage_index(conn)))
    base = r.randint(WAGE_MIN_CENTS, WAGE_MAX_CENTS)
    return max(int(WAGE_MIN_CENTS * MIN_WAGE_FLOOR), int(base * idx))


def _hire(conn: sqlite3.Connection, agent_id: int, place_id: int, occupation: str,
          r, tick: int) -> None:
    shift_start = r.choice([12, 14, 16, 18])
    conn.execute(
        "INSERT OR REPLACE INTO jobs(agent_id,place_id,role,wage_cents,"
        "shift_start,shift_end,work_days,started_tick,rank) VALUES(?,?,?,?,?,?,62,?,0)",
        (agent_id, place_id, occupation or "worker", _wage_for(conn, r),
         shift_start, min(shift_start + r.randint(14, 18), 44), tick))
    conn.execute("UPDATE agents SET work_place_id=? WHERE id=?", (place_id, agent_id))


def turnover(conn: sqlite3.Connection, tick: int, seed: str) -> int:
    """A little voluntary churn, concentrated where a venue is overstaffed."""
    targets = venue_targets(conn)
    staff: dict[int, list[sqlite3.Row]] = {}
    for row in conn.execute(
            """SELECT j.agent_id, j.place_id, p.name, a.age
               FROM jobs j JOIN places p ON p.id=j.place_id
               JOIN agents a ON a.id=j.agent_id WHERE a.alive=1"""):
        staff.setdefault(row["place_id"], []).append(row)
    r = rng_for(seed, "turnover", tick)
    left = 0
    for pid, crew in sorted(staff.items()):
        excess = len(crew) - targets.get(pid, len(crew))
        if excess <= 0:
            continue
        n = max(1, int(excess * EXCESS_SHED_SHARE))
        for row in r.sample(crew, min(n, len(crew))):
            conn.execute("DELETE FROM jobs WHERE agent_id=?", (row["agent_id"],))
            conn.execute("UPDATE agents SET work_place_id=NULL WHERE id=?",
                         (row["agent_id"],))
            emit(conn, tick, "life_event", a=row["agent_id"], place_id=pid,
                 importance=MINOR, text=f"left their job at {row['name']}",
                 tag="quit")
            left += 1
    return left


def hiring_pass(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Fill open posts from the unemployed; runs weekly at day start."""
    slots = vacancies(conn)
    if not slots:
        return {"hired": 0, "left": 0, "vacancies": 0}

    # candidate pool: unemployed working-age adults, never the retired
    candidates = conn.execute(
        """SELECT a.id, a.occupation, COALESCE(a.standing,0) standing FROM agents a
           WHERE a.alive=1 AND a.is_child=0 AND a.age >= 18 AND a.age < ?
             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.agent_id=a.id)
           ORDER BY a.id""", (RETIRE_AGE,)).fetchall()
    total_open = sum(n for _, n in slots)
    if not candidates:
        return {"hired": 0, "left": 0, "vacancies": total_open}

    workforce = conn.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
    # the budget has to clear the separations the life lottery creates each
    # week (P_FIRE per employed resident per day), otherwise the town sheds
    # jobs faster than it fills them: a 3% cap against a 3.4%/week firing
    # rate drove 260 posts down to 189 in five months
    from .life import P_FIRE
    separations = workforce * P_FIRE * 7
    budget = max(3, int(workforce * WEEKLY_HIRE_SHARE),
                 int(separations * SEPARATION_MARGIN))
    r = rng_for(seed, "hire", tick)

    places = {r2["id"]: (r2["name"], set(json.loads(r2["tags"] or "[]")))
              for r2 in conn.execute("SELECT id, name, tags FROM places")}
    tags_by_place = {pid: tags for pid, (_, tags) in places.items()}
    pool = {pid: n for pid, n in slots}
    order = list(candidates)
    r.shuffle(order)
    # a good name gets you in the door: the shuffle decides the rest
    order.sort(key=lambda c: -c["standing"])

    hired = []
    for cand in order:
        if budget <= 0:
            break
        want = set(workplace_tags_for(cand["occupation"] or ""))
        scored = [(len(want & tags_by_place.get(pid, set())), pid)
                  for pid, n in pool.items() if n > 0]
        if not scored:
            break
        best = max(s for s, _ in scored)
        picks = [pid for s, pid in scored if s == best]
        pid = r.choice(sorted(picks))
        _hire(conn, cand["id"], pid, cand["occupation"] or "", r, tick)
        emit(conn, tick, "life_event", a=cand["id"], place_id=pid, importance=MINOR,
             text=f"hired as {cand['occupation'] or 'staff'} at {places[pid][0]}",
             tag="hired")
        pool[pid] -= 1
        budget -= 1
        hired.append((cand["id"], pid))

    return {"hired": len(hired), "left": 0, "vacancies": total_open - len(hired)}


# careers
RANK_NAMES = {0: "", 1: "senior ", 2: "head "}
PROMOTE_ANNUAL = 0.55        # chance a year's tenure turns into a promotion
PROMOTE_ANNUAL_DEGREE = 0.75  # ...if they have a degree
PROMOTE_RAISE = 0.12         # pay bump on promotion
SENIORITY_ANNUAL = 0.02      # everyone's pay drifts up with tenure
SENIORITY_MAX = 0.40         # ...up to this much above the post's start


def careers(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Tenure earns rank and money; runs weekly.

    A town where a post is just a post has no careers in it — the same person
    holds the same job at the same wage until they die or are fired. Tenure now
    buys a yearly raise and, at most one step a year, a promotion: worker ->
    senior -> head of the venue. Education decides who moves faster, which is
    the first thing in the sim that a persona's `education_level` actually
    does.
    """
    day = day_of(tick)
    rows = conn.execute(
        """SELECT j.agent_id, j.place_id, j.role, j.wage_cents, j.rank,
                  j.started_tick, a.name aname, a.education_level, p.name pname
           FROM jobs j JOIN agents a ON a.id=j.agent_id
           JOIN places p ON p.id=j.place_id
           WHERE a.alive=1""").fetchall()
    if not rows:
        return {"raised": 0, "promoted": 0}
    r = rng_for(seed, "career", tick)
    raised = promoted = 0
    for row in rows:
        started = row["started_tick"]
        if started is None:
            continue
        years = (day - day_of(int(started))) // 365
        if years < 1:
            continue
        # one raise a year, tracked by rank thresholds so a re-run is a no-op
        rank = int(row["rank"] or 0)
        if rank >= 2 and rank >= years + 1:
            continue
        degree = bool((row["education_level"] or "").lower() in
                      ("bachelors", "masters", "doctorate", "phd", "professional"))
        p = PROMOTE_ANNUAL_DEGREE if degree else PROMOTE_ANNUAL
        if rank < min(2, years) and r.random() < p:
            rank += 1
            wage = int(row["wage_cents"] * (1 + PROMOTE_RAISE))
            conn.execute("UPDATE jobs SET rank=?, wage_cents=? WHERE agent_id=?",
                         (rank, wage, row["agent_id"]))
            emit(conn, tick, "life_event", a=row["agent_id"], place_id=row["place_id"],
                 importance=NOTABLE,
                 text=f"was made {RANK_NAMES[rank]}{row['role']} at {row['pname']} "
                      f"after {years} year{'s' if years > 1 else ''}",
                 tag="promoted")
            promoted += 1
        elif r.random() < SENIORITY_ANNUAL:
            wage = int(row["wage_cents"] * (1 + SENIORITY_ANNUAL))
            conn.execute("UPDATE jobs SET wage_cents=? WHERE agent_id=?",
                         (wage, row["agent_id"]))
            raised += 1
    return {"raised": raised, "promoted": promoted}


def fix_minor_flags(conn: sqlite3.Connection) -> int:
    """Residents under 18 who are flagged as adults become children again.

    Immigration used to insert personas without `is_child`, so a nine-year-old
    arrived as a job-holding head of household (115 of them in the live world).
    Their jobs are released; they keep their household and home.
    """
    rows = conn.execute(
        "SELECT id FROM agents WHERE alive=1 AND age < 18 AND is_child=0").fetchall()
    for row in rows:
        conn.execute("DELETE FROM jobs WHERE agent_id=?", (row["id"],))
        conn.execute(
            "UPDATE agents SET is_child=1, work_place_id=NULL, occupation='student' "
            "WHERE id=?", (row["id"],))
    return len(rows)


def rebalance(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """One-time correction: move surplus jobs onto the venues that need them.

    Bootstrap used to funnel almost every unmatched occupation into one venue
    (348 of 435 jobs at Town Hall in the live world) because the generic
    fallback tags match the town hall best. The weekly turnover pass would fix
    it in a year; this does it in one pass for an existing town. Nothing is
    destroyed: the surplus staff are released and immediately offered to the
    venues with vacancies, best occupation match first.
    """
    targets = venue_targets(conn)
    staff: dict[int, list[sqlite3.Row]] = {}
    for row in conn.execute(
            """SELECT j.agent_id, j.place_id, a.occupation, a.age
               FROM jobs j JOIN agents a ON a.id=j.agent_id WHERE a.alive=1"""):
        staff.setdefault(row["place_id"], []).append(row)
    r = rng_for(seed, "rebalance", tick)

    released = []
    for pid, crew in sorted(staff.items()):
        excess = len(crew) - targets.get(pid, len(crew))
        if excess <= 0:
            continue
        # release the surplus; keep the best occupation matches for the venue
        tags = set(json.loads(conn.execute(
            "SELECT tags FROM places WHERE id=?", (pid,)).fetchone()["tags"] or "[]"))
        crew = sorted(crew, key=lambda x: -len(
            set(workplace_tags_for(x["occupation"] or "")) & tags))
        for row in crew[len(crew) - excess:]:
            conn.execute("DELETE FROM jobs WHERE agent_id=?", (row["agent_id"],))
            conn.execute("UPDATE agents SET work_place_id=NULL WHERE id=?",
                         (row["agent_id"],))
            released.append(row)

    # hand them to the venues with room, best match first
    pool = {pid: n for pid, n in vacancies(conn)}
    tags_by_place = {r2["id"]: set(json.loads(r2["tags"] or "[]")) for r2 in
                     conn.execute("SELECT id, tags FROM places")}
    moved = retired = 0
    for row in sorted(released, key=lambda x: x["agent_id"]):
        if row["age"] >= RETIRE_AGE:
            # the posts belong to the working-age population; a pensioner who
            # loses theirs retires rather than competing for a vacancy
            conn.execute("UPDATE agents SET occupation='Retired' WHERE id=?",
                         (row["agent_id"],))
            retired += 1
            continue
        want = set(workplace_tags_for(row["occupation"] or ""))
        scored = [(len(want & tags_by_place.get(pid, set())), pid)
                  for pid, n in pool.items() if n > 0]
        if not scored:
            break
        best = max(s for s, _ in scored)
        pid = r.choice(sorted(pid for s, pid in scored if s == best))
        _hire(conn, row["agent_id"], pid, row["occupation"] or "", r, tick)
        pool[pid] -= 1
        moved += 1

    if moved or released:
        emit(conn, tick, "town_event", importance=NOTABLE,
             text=f"the town's employers rebalanced: {moved} residents took a "
                  f"post at a venue that needed them", tag="jobs_rebalanced")
    return {"released": len(released), "rehired": moved, "retired": retired,
            "still_unemployed": len(released) - moved - retired}


def retirements(conn: sqlite3.Connection, tick: int, seed: str) -> list[dict]:
    """Seniors leave the workforce; their posts become vacancies.

    When the town has idle working-age hands, the old step aside sooner: a
    pensioner holding a post while a quarter of the working age is out of
    work is how the town kept 44 posts in the hands of the over-65s.
    """
    rows = conn.execute(
        """SELECT j.agent_id, j.place_id, p.name, a.name aname, a.age
           FROM jobs j JOIN places p ON p.id=j.place_id JOIN agents a ON a.id=j.agent_id
           WHERE a.alive=1 AND a.age >= ?""", (RETIRE_AGE,)).fetchall()
    if not rows:
        return []
    from .economy import unemployment
    pressure = 1 + unemployment(conn) * 12     # 20% idle -> 3.4x the base rate
    r = rng_for(seed, "retire", tick)
    out = []
    for row in rows:
        annual = min(0.95, (RETIRE_ANNUAL if row["age"] < 70
                            else RETIRE_ANNUAL_OLD) * pressure)
        daily = 1 - (1 - annual) ** (1 / 365)
        if r.random() >= daily:
            continue
        conn.execute("DELETE FROM jobs WHERE agent_id=?", (row["agent_id"],))
        conn.execute(
            "UPDATE agents SET work_place_id=NULL, occupation='Retired' WHERE id=?",
            (row["agent_id"],))
        emit(conn, tick, "life_event", a=row["agent_id"], place_id=row["place_id"],
             importance=NOTABLE,
             text=f"retired from {row['name']} at {row['age']}", tag="retired")
        out.append({"agent_id": row["agent_id"], "name": row["aname"],
                    "place": row["name"], "age": row["age"]})
    return out
