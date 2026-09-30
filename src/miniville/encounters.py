"""Co-presence -> interactions -> relationship graph updates + events."""
from __future__ import annotations

import json
import sqlite3

from .events import MINOR, NOTABLE, TRIVIAL, emit
from .favors import maybe_affair, maybe_ask_favor, maybe_repay_debt
from .life import dating_arc_check, romance_allowed
from .rng import rng_for

REL_THRESHOLDS = [
    (0, "stranger"), (3, "acquaintance"), (10, "familiar"), (25, "friend"),
    (60, "close_friend"),
]


def _rel_label(familiarity: float, affinity: float, romance: float) -> str:
    if romance > 80:
        return "sweetheart"
    if affinity < -40:
        return "rival"
    if affinity < -15:
        return "friction"
    for thr, label in reversed(REL_THRESHOLDS):
        if familiarity >= thr:
            return label
    return "stranger"


def _compatible(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    return abs(a["age"] - b["age"]) <= 20 and not a["is_child"] and not b["is_child"]


def _interaction_tone(r, affinity: float) -> tuple[str, float]:
    """Returns (tone, affinity_delta)."""
    roll = r.random()
    if affinity < -20:
        table = [("hostile", -3, 0.20), ("tense", -1.5, 0.35),
                 ("awkward", -0.5, 0.25), ("civil", +0.8, 0.20)]
    elif affinity > 30:
        table = [("warm", +2.5, 0.45), ("friendly", +1.5, 0.35),
                 ("delightful", +4, 0.08), ("routine", +0.5, 0.12)]
    else:
        table = [("pleasant", +1.5, 0.40), ("routine", +0.5, 0.30),
                 ("awkward", -0.8, 0.15), ("engaging", +3, 0.15)]
    acc = 0.0
    for tone, delta, p in table:
        acc += p
        if roll < acc:
            return tone, delta
    return "routine", +0.5


def _get_rel(conn: sqlite3.Connection, a: int, b: int) -> sqlite3.Row | None:
    lo, hi = min(a, b), max(a, b)
    return conn.execute(
        "SELECT * FROM relationships WHERE a_id=? AND b_id=?", (lo, hi)).fetchone()


def _spread_gossip(conn: sqlite3.Connection, a_id: int, b_id: int,
                   tick: int, place_id: int, r) -> None:
    """One participant shares a recent notable event they're not part of."""
    from .events import describe
    day = tick // 48
    rows = conn.execute(
        """SELECT id FROM events
           WHERE day BETWEEN ? AND ? AND importance >= 3
             AND (a_id IS NULL OR a_id NOT IN (?,?))
             AND (b_id IS NULL OR b_id NOT IN (?,?))""",
        (day - 2, day, a_id, b_id, a_id, b_id)).fetchall()
    if not rows:
        return
    ev_id = r.choice(rows)["id"]
    ev = conn.execute("SELECT * FROM events WHERE id=?", (ev_id,)).fetchone()
    emitter, listener = (a_id, b_id) if r.random() < 0.5 else (b_id, a_id)
    emit(conn, tick, "gossip", place_id=place_id, a=emitter, b=listener,
         importance=MINOR, about=ev_id, summary=describe(conn, ev)[:140])


def interact(conn: sqlite3.Connection, a: sqlite3.Row, b: sqlite3.Row,
             place_id: int, tick: int, seed: str) -> None:
    lo, hi = min(a["id"], b["id"]), max(a["id"], b["id"])
    r = rng_for(seed, "interact", lo, hi, tick)
    rel = _get_rel(conn, lo, hi)
    fam = rel["familiarity"] if rel else 0.0
    aff = rel["affinity"] if rel else 0.0
    rom = rel["romance"] if rel else 0.0
    label = rel["label"] if rel else "stranger"
    n = rel["interactions"] if rel else 0

    tone, d_aff = _interaction_tone(r, aff)
    # shared hobbies spark
    ha = set(json.loads(a["hobbies_json"] or "[]"))
    hb = set(json.loads(b["hobbies_json"] or "[]"))
    shared = ha & hb
    if shared:
        d_aff += min(2.0, 0.4 * len(shared))
    fam += 1.0 + (0.5 if tone in ("warm", "delightful", "engaging") else 0)
    aff = max(-100.0, min(100.0, aff + d_aff))

    # romance spark: compatible, positive affinity, neither married to someone else
    d_rom = 0.0
    if (_compatible(a, b) and aff > 20 and rom < 95
            and romance_allowed(conn, a, b, label)):
        if r.random() < 0.06 + max(0, aff) / 1000:
            d_rom = r.uniform(2, 8)
            rom = min(100.0, rom + d_rom)
    elif rom > 0 and label != "spouse":
        rom = max(0.0, rom - 0.2)

    new_label = _rel_label(fam, aff, rom)
    importance = TRIVIAL
    if new_label != label:
        importance = NOTABLE if new_label in ("friend", "sweetheart", "rival") else MINOR
    elif tone in ("hostile", "delightful"):
        importance = MINOR

    conn.execute(
        """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,label,interactions,last_met_tick)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(a_id,b_id) DO UPDATE SET
             familiarity=excluded.familiarity, affinity=excluded.affinity,
             romance=excluded.romance, label=excluded.label,
             interactions=excluded.interactions, last_met_tick=excluded.last_met_tick""",
        (lo, hi, fam, aff, rom, new_label, n + 1, tick))

    if importance >= MINOR or r.random() < 0.15:
        emit(conn, tick, "encounter", place_id=place_id, a=a["id"], b=b["id"],
             importance=importance, tone=tone,
             shared=sorted(shared)[:3] if shared else [])
    if new_label != label:
        emit(conn, tick, "relationship", place_id=place_id, a=a["id"], b=b["id"],
             importance=NOTABLE, label=new_label, was=label)
        # coworkers who click at work become work buddies (visible in chronicle)
        if (new_label == "friend" and place_id is not None
                and a["work_place_id"] is not None
                and a["work_place_id"] == b["work_place_id"]
                and place_id == a["work_place_id"]):
            emit(conn, tick, "work_buddy", place_id=place_id, a=a["id"], b=b["id"],
                 importance=MINOR)
    # dating arc: sweetheart -> partner -> spouse (uses post-update rel values)
    updated = _get_rel(conn, lo, hi)
    if updated:
        dating_arc_check(conn, lo, hi, updated, tick, seed)
    # gossip: positive interactions spread one recent notable happening
    if tone in ("warm", "delightful", "engaging", "pleasant") and r.random() < 0.2:
        _spread_gossip(conn, a["id"], b["id"], tick, place_id, r)
    # favors: warm chats can mint/settle IOUs
    maybe_ask_favor(conn, a, b, place_id, tick, tone, r)
    maybe_repay_debt(conn, a["id"], b["id"], place_id, tick, tone, r)
    # drama: rare affair trigger when a partnered agent flirts with non-spouse
    maybe_affair(conn, a, b, new_label, aff, fam, place_id, tick, r, tone)
    maybe_affair(conn, b, a, new_label, aff, fam, place_id, tick, r, tone)
    # social needs satisfaction
    boost = 6 if tone in ("warm", "delightful", "engaging") else (2 if tone in ("pleasant", "civil", "routine") else -1)
    for aid in (a["id"], b["id"]):
        conn.execute(
            "UPDATE agent_state SET social=max(0,min(100,social+?)) WHERE agent_id=?",
            (boost, aid))


def run_encounters(conn: sqlite3.Connection, tick: int, seed: str,
                   max_pairs_per_place: int = 12) -> int:
    """Pair up co-present agents at public venues. Returns # interactions."""
    tick_of_day = tick % 48
    places = conn.execute(
        "SELECT id, capacity FROM places WHERE kind IN ('public','civic','workplace')"
    ).fetchall()
    n_interactions = 0
    for p in places:
        rows = conn.execute(
            """SELECT s.agent_id, a.name, a.age, a.is_child, a.hobbies_json, a.sex
               FROM agent_state s JOIN agents a ON a.id=s.agent_id
               WHERE s.place_id=? AND s.activity != 'sleep'""",
            (p["id"],)).fetchall()
        if len(rows) < 2:
            continue
        r = rng_for(seed, "pairing", p["id"], tick)
        agents = list(rows)
        r.shuffle(agents)
        pairs = []
        for i in range(0, len(agents) - 1, 2):
            pairs.append((agents[i], agents[i + 1]))
        for a, b in pairs[:max_pairs_per_place]:
            # interaction probability: strangers lower, acquaintances higher
            rel = _get_rel(conn, a["agent_id"], b["agent_id"])
            p_int = 0.28 if not rel else min(0.9, 0.3 + rel["familiarity"] / 40)
            if r.random() < p_int:
                ra = conn.execute("SELECT * FROM agents WHERE id=?", (a["agent_id"],)).fetchone()
                rb = conn.execute("SELECT * FROM agents WHERE id=?", (b["agent_id"],)).fetchone()
                interact(conn, ra, rb, p["id"], tick, seed)
                n_interactions += 1
    return n_interactions
