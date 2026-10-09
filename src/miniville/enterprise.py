"""Enterprise: residents own the town's businesses, and found new ones.

Until now a business belonged to nobody. Its profits piled up in a reserve
that only the weekly levy could touch, a closed venue "reopened under new
management" that did not exist, and the town's set of shops was fixed the day
it was founded. A town of 600 people in which nobody ever opens a bakery is
not a town.

So a commercial business has an **owner** — a resident:

* **proprietors** — on an existing world the longest-serving senior hand at
  each commercial venue becomes its proprietor (`enterprise_v1`);
* **draws** — once a week an owner takes a share of the reserve above a few
  weeks' payroll cushion. Profit leaves the till for a wallet, which is what
  makes an owner rich, and rich is influence;
* **buyers** — a venue that failed is bought, after its cooldown, by the
  resident who can best afford it; their capital becomes its opening reserve.
  An original venue with no buyer reopens town-run as before (the town will
  not lose its grocer); a founded one stays dark until someone buys it;
* **founders** — when a kind of custom is crowding the venues that serve it,
  a resident with savings opens a new one in their own district, staffs it
  through the ordinary labour market, and lives or dies by the same books as
  everyone else. Their capital is at stake: a failed venture loses it;
* **inheritance** — an owner's business passes with their estate to the
  surviving spouse or household; with nobody to take it on it is unowned.

Money only moves (wallet -> reserve on founding or purchase, reserve ->
wallet on a draw); nothing here mints or destroys it. Deterministic, like
everything else.
"""
from __future__ import annotations

import json
import sqlite3

from .db import get_meta, set_meta
from .economy import PUBLIC_TAGS
from .events import MAJOR, MINOR, NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

CAPITAL_CENTS = 2_000_000        # $20k to open a venue
BUY_CENTS = 1_200_000            # $12k to take on a failed one
CUSHION_AFTER_CENTS = 300_000    # a founder keeps $3k to live on
DRAW_SHARE = 0.10                # weekly share of the reserve above the cushion
CUSHION_WEEKS = 4                # weeks of payroll a business keeps in hand
FOUND_P_WEEKLY = 0.35            # chance a week that a ready town sees a founding
CROWDING_TO_FOUND = 6.0          # 14-day mean visitors per seat of capacity
MAX_FOUNDED_OPEN = 6             # founded venues the town can carry at once
MAX_FOUNDED_EVER = 14            # places rows are never deleted; bound the table
FOUNDER_MIN_AGE, FOUNDER_MAX_AGE = 23, 64

# concept -> (name patterns, kind, tags, capacity, open_tick, close_tick,
#             occupation/hobby words that make a resident want to run one)
# Only commercial tags: anything in economy.PUBLIC_TAGS would make the venue
# town-funded, and a bakery is not a public service.
CONCEPTS: dict[str, tuple] = {
    "bakery": (["{s}'s Bakery", "{d} Bakehouse"], "public",
               ["food", "coffee"], 20, 12, 36, ["bak", "cook", "chef", "pastry"]),
    "kitchen": (["{s}'s Kitchen", "The {d} Table"], "workplace",
                ["food"], 30, 22, 44, ["cook", "chef", "food", "restaurant", "grill"]),
    "bar": (["The {s} Arms", "{d} Taproom"], "public",
            ["drink", "nightlife", "food"], 35, 32, 47, ["bartend", "brew", "wine"]),
    "cafe": (["{s}'s Café", "{d} Coffee House"], "public",
             ["coffee", "food"], 18, 12, 38, ["barista", "coffee", "read", "poetry"]),
    "boutique": (["{s} & Co.", "{d} Goods"], "workplace",
                 ["retail"], 25, 18, 40, ["retail", "sales", "fashion", "sew", "craft"]),
    "workshop": (["{s} Repairs", "{d} Hardware"], "workplace",
                 ["trades", "retail"], 15, 14, 38,
                 ["mechanic", "carpent", "electric", "plumb", "repair", "woodwork"]),
    "studio": (["{s} Fitness", "{d} Athletic Club"], "public",
               ["fitness", "sport"], 25, 10, 44,
               ["coach", "trainer", "fitness", "running", "cycling", "sport"]),
    "stage": (["The {s} Playhouse", "{d} Music Hall"], "public",
              ["arts", "nightlife"], 40, 36, 47,
              ["artist", "music", "guitar", "sing", "actor", "paint", "photograph"]),
}


# --- schema ------------------------------------------------------------------


def migrate(conn: sqlite3.Connection) -> None:
    """Guarded columns on `businesses`; called from db._migrate."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(businesses)")}
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE businesses ADD COLUMN owner_id INTEGER")
    if "founded_tick" not in cols:
        # NULL for the town's original venues; set when a resident founds one
        conn.execute("ALTER TABLE businesses ADD COLUMN founded_tick INTEGER")
    if "concept" not in cols:
        conn.execute("ALTER TABLE businesses ADD COLUMN concept TEXT")
    if "capital_cents" not in cols:
        conn.execute("ALTER TABLE businesses ADD COLUMN capital_cents "
                     "INTEGER NOT NULL DEFAULT 0")
    if "draws_total" not in cols:
        conn.execute("ALTER TABLE businesses ADD COLUMN draws_total "
                     "INTEGER NOT NULL DEFAULT 0")


def is_commercial(tags: set[str], kind: str) -> bool:
    return not (tags & PUBLIC_TAGS) and kind != "civic"


def _commercial_rows(conn: sqlite3.Connection, status: str | None = None):
    rows = conn.execute(
        """SELECT b.*, p.name, p.tags, p.kind, p.district, p.capacity
           FROM businesses b JOIN places p ON p.id=b.place_id
           ORDER BY b.place_id""").fetchall()
    out = []
    for r in rows:
        if not is_commercial(set(json.loads(r["tags"] or "[]")), r["kind"]):
            continue
        if status and r["status"] != status:
            continue
        out.append(r)
    return out


def _money(conn: sqlite3.Connection, aid: int) -> int:
    row = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=?",
                       (aid,)).fetchone()
    return int(row["money_cents"]) if row else 0


def _move_money(conn: sqlite3.Connection, aid: int, place_id: int, cents: int) -> None:
    """Positive cents: wallet -> reserve. Negative: reserve -> wallet."""
    conn.execute("UPDATE agent_state SET money_cents=money_cents-? WHERE agent_id=?",
                 (cents, aid))
    conn.execute("UPDATE businesses SET balance_cents=balance_cents+? WHERE place_id=?",
                 (cents, place_id))


def owned_by(conn: sqlite3.Connection, aid: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT b.place_id, b.status, b.founded_tick, p.name FROM businesses b
           JOIN places p ON p.id=b.place_id WHERE b.owner_id=? ORDER BY b.place_id""",
        (aid,)).fetchall()


# --- proprietors (one-time, existing worlds) ---------------------------------


def assign_proprietors(conn: sqlite3.Connection) -> int:
    """Give every unowned open commercial venue an owner from its own staff:
    the highest rank, then the longest tenure. Returns owners appointed."""
    n = 0
    for b in _commercial_rows(conn, "open"):
        if b["owner_id"] is not None:
            continue
        row = conn.execute(
            """SELECT j.agent_id FROM jobs j JOIN agents a ON a.id=j.agent_id
               WHERE j.place_id=? AND a.alive=1
                 AND NOT EXISTS (SELECT 1 FROM businesses o WHERE o.owner_id=j.agent_id)
               ORDER BY j.rank DESC, COALESCE(j.started_tick, 0), j.agent_id
               LIMIT 1""", (b["place_id"],)).fetchone()
        if row:
            conn.execute("UPDATE businesses SET owner_id=? WHERE place_id=?",
                         (row["agent_id"], b["place_id"]))
            n += 1
    return n


# --- draws ---------------------------------------------------------------------


def _payroll_week(conn: sqlite3.Connection, place_id: int) -> int:
    row = conn.execute("SELECT COALESCE(SUM(wage_cents),0) w FROM jobs WHERE place_id=?",
                       (place_id,)).fetchone()
    return int(row["w"]) * 5


def owner_draws(conn: sqlite3.Connection, tick: int) -> dict:
    """Weekly: each owner takes DRAW_SHARE of the reserve above the cushion."""
    if day_of(tick) % 7 != 0 or day_of(tick) == 0:
        return {"owners": 0, "drawn": 0}
    owners = drawn = 0
    for b in _commercial_rows(conn, "open"):
        if b["owner_id"] is None:
            continue
        cushion = CUSHION_WEEKS * _payroll_week(conn, b["place_id"])
        take = int(max(0, b["balance_cents"] - cushion) * DRAW_SHARE)
        if take <= 0:
            continue
        _move_money(conn, b["owner_id"], b["place_id"], -take)
        conn.execute("UPDATE businesses SET draws_total=draws_total+? WHERE place_id=?",
                     (take, b["place_id"]))
        owners += 1
        drawn += take
    return {"owners": owners, "drawn": drawn}


# --- buyers ------------------------------------------------------------------


def _candidates(conn: sqlite3.Connection, need: int) -> list[sqlite3.Row]:
    """Adults of working age who could put `need` down and still live, and
    who do not already own something."""
    return conn.execute(
        """SELECT a.id, a.name, a.occupation, a.hobbies_json, a.home_place_id,
                  s.money_cents FROM agents a JOIN agent_state s ON s.agent_id=a.id
           WHERE a.alive=1 AND a.is_child=0 AND a.age BETWEEN ? AND ?
             AND s.money_cents >= ?
             AND NOT EXISTS (SELECT 1 FROM businesses b WHERE b.owner_id=a.id)
           ORDER BY a.id""",
        (FOUNDER_MIN_AGE, FOUNDER_MAX_AGE, need + CUSHION_AFTER_CENTS)).fetchall()


def find_buyer(conn: sqlite3.Connection, b: sqlite3.Row, tick: int, seed: str) -> int | None:
    """The resident who takes on a closed venue, or None. Deterministic: the
    likeliest buyer is whoever has the most to spare, with a nudge for anyone
    who knows the trade."""
    pool = _candidates(conn, BUY_CENTS)
    if not pool:
        return None
    concept = b["concept"]
    r = rng_for(seed, "buyer", b["place_id"], day_of(tick))
    best, best_score = None, -1.0
    for c in pool:
        score = c["money_cents"] / 100_000 + r.random() * 10
        if concept and _affinity(c, concept):
            score *= 1.5
        if score > best_score:
            best, best_score = c, score
    return best["id"] if best else None


def take_over(conn: sqlite3.Connection, place_id: int, buyer: int, tick: int) -> None:
    """The buyer's capital becomes the reopened venue's reserve."""
    _move_money(conn, buyer, place_id, BUY_CENTS)
    conn.execute("UPDATE businesses SET owner_id=?, capital_cents=? WHERE place_id=?",
                 (buyer, BUY_CENTS, place_id))
    name = conn.execute("SELECT name FROM places WHERE id=?", (place_id,)).fetchone()["name"]
    remember(conn, buyer, tick, f"I bought {name} and reopened it.",
             kind="enterprise", importance=4)


# --- founders ----------------------------------------------------------------


def _affinity(person: sqlite3.Row, concept: str) -> bool:
    words = CONCEPTS[concept][6]
    text = (person["occupation"] or "").lower() + " " + (person["hobbies_json"] or "").lower()
    return any(w in text for w in words)


def crowding(conn: sqlite3.Connection) -> dict[str, float]:
    """concept -> how hard its kind of custom presses on the open venues that
    serve it (14-day mean visitors per seat). A concept nobody serves yet
    reads as the town-wide mean, so it can be founded but is not favoured."""
    venues = conn.execute(
        """SELECT p.tags, p.capacity, b.ema_traffic FROM places p
           JOIN businesses b ON b.place_id=p.id
           WHERE p.kind != 'home' AND b.status='open'""").fetchall()
    seats: dict[str, float] = {}
    load: dict[str, float] = {}
    for v in venues:
        tags = set(json.loads(v["tags"] or "[]"))
        if not is_commercial(tags, "public"):
            continue
        for t in tags:
            seats[t] = seats.get(t, 0.0) + v["capacity"]
            load[t] = load.get(t, 0.0) + v["ema_traffic"]
    mean = (sum(load.values()) / sum(seats.values())) if seats else 0.0
    out = {}
    for concept, spec in CONCEPTS.items():
        tags = spec[2]
        s = sum(seats.get(t, 0.0) for t in tags)
        out[concept] = (sum(load.get(t, 0.0) for t in tags) / s) if s else mean
    return out


def _venue_name(conn: sqlite3.Connection, concept: str, surname: str,
                district: str, r) -> str:
    short = district.replace("The ", "").replace(" Quarter", "")
    patterns = CONCEPTS[concept][0][:]
    r.shuffle(patterns)
    taken = {row["name"] for row in conn.execute("SELECT name FROM places")}
    for p in patterns:
        name = p.format(s=surname, d=short)
        if name not in taken:
            return name
    return f"{surname}'s {concept.title()} No. {len(taken)}"


def found(conn: sqlite3.Connection, founder: sqlite3.Row, concept: str,
          tick: int, r) -> int:
    """Open a new venue. Returns its place id."""
    name_kind_tags = CONCEPTS[concept]
    _patterns, kind, tags, cap, open_t, close_t, _words = name_kind_tags
    home = conn.execute("SELECT district FROM places WHERE id=?",
                        (founder["home_place_id"],)).fetchone()
    district = home["district"] if home else "Downtown"
    surname = (founder["name"] or "Miniville").split()[-1]
    name = _venue_name(conn, concept, surname, district, r)
    cur = conn.execute(
        """INSERT INTO places(name,kind,district,capacity,open_tick,close_tick,tags)
           VALUES(?,?,?,?,?,?,?)""",
        (name, kind, district, cap, open_t, close_t, json.dumps(tags)))
    pid = cur.lastrowid
    conn.execute(
        """INSERT INTO businesses(place_id, opened_tick, owner_id, founded_tick,
               concept, capital_cents) VALUES(?,?,?,?,?,?)""",
        (pid, tick, founder["id"], tick, concept, CAPITAL_CENTS))
    _move_money(conn, founder["id"], pid, CAPITAL_CENTS)
    # the founder runs it: they leave their post and head the new venue
    old = conn.execute("SELECT * FROM jobs WHERE agent_id=?", (founder["id"],)).fetchone()
    wage = int(old["wage_cents"]) if old else 15_000
    conn.execute("DELETE FROM jobs WHERE agent_id=?", (founder["id"],))
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
               work_days,started_tick,rank,base_wage_cents)
           VALUES(?,?,'proprietor',?,?,?,62,?,2,?)""",
        (founder["id"], pid, wage, max(open_t, 12), min(close_t, open_t + 16),
         tick, wage))
    conn.execute("UPDATE agents SET work_place_id=? WHERE id=?", (pid, founder["id"]))
    emit(conn, tick, "town_event", a=founder["id"], place_id=pid, importance=MAJOR,
         text=f"opened {name}, a new {concept} in {district}, "
              f"putting ${CAPITAL_CENTS / 100:,.0f} of their savings into it",
         tag="business_founded", concept=concept)
    remember(conn, founder["id"], tick,
             f"I opened {name}. Everything I saved is in it now.",
             kind="enterprise", importance=5)
    return pid


def founding_pass(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Weekly: if some kind of custom is crowding its venues and someone can
    afford to answer it, a new venue opens."""
    if day_of(tick) % 7 != 3:          # mid-week, away from the levy day
        return {"founded": 0}
    founded_open = sum(1 for b in _commercial_rows(conn, "open")
                       if b["founded_tick"] is not None)
    founded_ever = conn.execute(
        "SELECT COUNT(*) n FROM businesses WHERE founded_tick IS NOT NULL").fetchone()["n"]
    if founded_open >= MAX_FOUNDED_OPEN or founded_ever >= MAX_FOUNDED_EVER:
        return {"founded": 0}
    r = rng_for(seed, "found", day_of(tick))
    if r.random() >= FOUND_P_WEEKLY:
        return {"founded": 0}
    press = crowding(conn)
    ready = {c: v for c, v in press.items() if v >= CROWDING_TO_FOUND}
    if not ready:
        return {"founded": 0}
    pool = _candidates(conn, CAPITAL_CENTS)
    if not pool:
        return {"founded": 0}
    # the founder: savings and a trade that matches an unmet demand
    best = None
    for c in pool:
        for concept, p in sorted(ready.items()):
            score = p * (2.0 if _affinity(c, concept) else 1.0) \
                + c["money_cents"] / 1_000_000 + r.random()
            if best is None or score > best[0]:
                best = (score, c, concept)
    _score, founder, concept = best
    pid = found(conn, founder, concept, tick, r)
    return {"founded": 1, "place_id": pid, "concept": concept}


# --- reopening (called by economy.settle_businesses) -------------------------


def reopen_or_wait(conn: sqlite3.Connection, b: sqlite3.Row, tick: int,
                   seed: str) -> str:
    """A closed venue past its cooldown: 'bought', 'town' (an original venue
    reopens town-run when nobody buys it), or 'dark' (a founded venue waits)."""
    tags = set(json.loads(b["tags"] or "[]"))
    if not is_commercial(tags, b["kind"]):
        return "town"
    buyer = find_buyer(conn, b, tick, seed)
    if buyer is not None:
        take_over(conn, b["place_id"], buyer, tick)
        return "bought"
    return "dark" if b["founded_tick"] is not None else "town"


def lose_venture(conn: sqlite3.Connection, place_id: int, tick: int) -> None:
    """On closure the owner loses the venue (and whatever they put in)."""
    b = conn.execute("SELECT owner_id, capital_cents FROM businesses WHERE place_id=?",
                     (place_id,)).fetchone()
    if not b or b["owner_id"] is None:
        return
    name = conn.execute("SELECT name FROM places WHERE id=?", (place_id,)).fetchone()["name"]
    remember(conn, b["owner_id"], tick, f"{name} failed. I lost the business.",
             kind="enterprise", importance=5)
    conn.execute("UPDATE businesses SET owner_id=NULL, capital_cents=0 WHERE place_id=?",
                 (place_id,))


# --- inheritance (called by mortality) ---------------------------------------


def pass_on(conn: sqlite3.Connection, deceased_id: int, heir: int | None,
            tick: int) -> int:
    """Hand the deceased's businesses to their heir. Returns how many."""
    rows = owned_by(conn, deceased_id)
    for b in rows:
        conn.execute("UPDATE businesses SET owner_id=? WHERE place_id=?",
                     (heir, b["place_id"]))
        if heir is not None:
            remember(conn, heir, tick, f"I inherited {b['name']}.",
                     kind="enterprise", importance=4)
            emit(conn, tick, "town_event", a=heir, place_id=b["place_id"],
                 importance=NOTABLE, text=f"inherited {b['name']}", tag="inheritance")
    return len(rows)


# --- daily entry point -------------------------------------------------------


def due(conn: sqlite3.Connection, tick: int, seed: str) -> dict:
    """Day-start hook: proprietors once, draws on the levy day, a founding
    chance mid-week."""
    if not get_meta(conn, "enterprise_v1"):
        assign_proprietors(conn)
        set_meta(conn, "enterprise_v1", "1")
    out = {"draws": owner_draws(conn, tick)}
    out.update(founding_pass(conn, tick, seed))
    if out["draws"]["drawn"]:
        emit(conn, tick, "town_event", importance=MINOR,
             text=f"{out['draws']['owners']} business owners drew "
                  f"${out['draws']['drawn'] / 100:,.0f} in profits",
             tag="owner_draws")
    conn.commit()
    return out


__all__ = ["CONCEPTS", "assign_proprietors", "crowding", "find_buyer", "found",
           "founding_pass", "lose_venture", "migrate", "owned_by", "owner_draws",
           "pass_on", "reopen_or_wait", "take_over", "due"]
