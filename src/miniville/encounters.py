"""Co-presence -> interactions -> relationship graph updates + events."""
from __future__ import annotations

import json
import sqlite3

from .events import MINOR, NOTABLE, TRIVIAL, emit
from .favors import maybe_affair, maybe_ask_favor, maybe_repay_debt
from .life import dating_arc_check, romance_allowed
from .rng import rng_for
from .seasons import holiday_for

REL_THRESHOLDS = [
    (0, "stranger"), (3, "acquaintance"), (10, "familiar"), (25, "friend"),
    (60, "close_friend"),
]

# Labels owned by the dating arc / mortality / betrayal — _rel_label must never
# demote them back to a threshold label (a partner is not "sweetheart" again
# at the next encounter, which would re-fire cohabitation every time).
ARC_LABELS = {"partner", "spouse", "widowed", "estranged"}

HOLIDAY_P_INTERACT = 0.5


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


def _interaction_tone(r, affinity: float, standing: float = 0.0) -> tuple[str, float]:
    """Returns (tone, affinity_delta).

    `standing` is the pair's combined reputation: people are a little warmer
    to someone the town already thinks well of, and warier of someone it does
    not, so a reputation is felt before it is explained.
    """
    roll = r.random()
    if affinity < -20:
        table = [("hostile", -3, 0.20), ("tense", -1.5, 0.35),
                 ("awkward", -0.5, 0.25), ("civil", +0.8, 0.20)]
    elif affinity > 30:
        table = [("warm", +2.5, 0.45), ("friendly", +1.5, 0.35),
                 ("delightful", +4, 0.08), ("routine", +0.5, 0.12)]
    else:
        # familiarity can also breed contempt: the longer two people have been
        # thrown together, the more room there is for a bad afternoon
        table = [("pleasant", +1.5, 0.40), ("routine", +0.5, 0.30),
                 ("awkward", -0.8, 0.15), ("engaging", +3, 0.15),
                 ("friction", -1.5, 0.05)]
    acc = 0.0
    for tone, delta, p in table:
        acc += p
        if roll < acc:
            return tone, delta * (1 + max(-0.6, min(0.6, standing / 200)))
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

    tone, d_aff = _interaction_tone(
        r, aff, float((a["standing"] or 0) + (b["standing"] or 0)))
    # A slight that is never repeated is forgotten; a slight between people who
    # already dislike each other compounds. Without this the town's worst
    # relationship sat at affinity -2.7: the negative tones only trigger below
    # -20, which nothing could ever reach, so the whole town liked everybody
    # and there was nothing for a rivalry, a boycott or a slander to be about.
    if aff < 0 and d_aff < 0:
        d_aff *= 1 + min(3.0, abs(aff) / 10)
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
    if label in ARC_LABELS:
        new_label = label
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


# How many conversations a venue hosts in half an hour, and how many people
# are even considered. Real contact is scarce and chosen: you talk to two or
# three of the thirty people in the room, and you gravitate to the ones you
# know and to your own. The old version shuffled everyone present and paired
# them at random up to twelve pairs, every tick, at every venue — ~9,800
# pair-encounters a day, which mixed the whole town into a fog of one-off
# meetings (92% of all relationships sat at familiarity 1-3) and made clubs,
# congregations and even workplaces a rounding error in the social graph.
PAIRS_PER_PLACE = 4
CANDIDATES = 14


def _affinity_ctx(conn: sqlite3.Connection) -> dict:
    """Who belongs to what — one pass, so pairing needs no per-pair queries."""
    ctx: dict = {"groups": {}, "faith": {}, "hobbies": {}, "age": {}}
    for r in conn.execute("SELECT agent_id, group_id FROM memberships"):
        ctx["groups"].setdefault(r["agent_id"], set()).add(r["group_id"])
    for r in conn.execute("SELECT id, faith, age, hobbies_json FROM agents "
                          "WHERE alive=1"):
        ctx["faith"][r["id"]] = r["faith"]
        ctx["age"][r["id"]] = r["age"]
        try:
            ctx["hobbies"][r["id"]] = set(json.loads(r["hobbies_json"] or "[]"))
        except (TypeError, ValueError):
            ctx["hobbies"][r["id"]] = set()
    return ctx


def _known_pairs(conn: sqlite3.Connection, ids: list[int]) -> set[tuple[int, int]]:
    """Which of these people already know each other — one query, not one per pair."""
    if len(ids) < 2:
        return set()
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT a_id, b_id FROM relationships
            WHERE a_id IN ({ph}) AND b_id IN ({ph})""", (*ids, *ids)).fetchall()
    return {(r["a_id"], r["b_id"]) for r in rows}


def _pair_score(ctx: dict, known: set[tuple[int, int]], a: int, b: int,
                jitter: float) -> float:
    """Why these two would talk: they know each other, or they are alike."""
    score = jitter
    if (min(a, b), max(a, b)) in known:      # repeat contact: the strongest pull
        score += 3.0
    if ctx["groups"].get(a, set()) & ctx["groups"].get(b, set()):
        score += 2.0                         # same club or congregation
    fa, fb = ctx["faith"].get(a), ctx["faith"].get(b)
    if fa and fa == fb:
        score += 1.0
    if abs((ctx["age"].get(a) or 0) - (ctx["age"].get(b) or 0)) <= 8:
        score += 0.75
    if ctx["hobbies"].get(a, set()) & ctx["hobbies"].get(b, set()):
        score += 0.5
    return score


def run_encounters(conn: sqlite3.Connection, tick: int, seed: str,
                   max_pairs_per_place: int = PAIRS_PER_PLACE) -> int:
    """Pair up co-present agents at public venues. Returns # interactions."""
    tick_of_day = tick % 48
    holiday = holiday_for(conn, tick // 48)
    holiday_place_id = None
    if (holiday and holiday.venue
            and holiday.start_tick <= tick_of_day < holiday.end_tick):
        place = conn.execute(
            "SELECT id FROM places WHERE name=?", (holiday.venue,)).fetchone()
        holiday_place_id = place["id"] if place else None
    places = conn.execute(
        "SELECT id, name, capacity FROM places WHERE kind IN ('public','civic','workplace')"
    ).fetchall()
    ctx = _affinity_ctx(conn)
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
        holiday_venue_active = (
            holiday_place_id is not None and p["id"] == holiday_place_id
        )
        agents = list(rows)
        if holiday_venue_active:
            # a festival is the one time the whole room talks to everybody
            r.shuffle(agents)
            pairs = [(agents[i], agents[i + 1])
                     for i in range(0, len(agents) - 1, 2)]
        else:
            r.shuffle(agents)
            pool = agents[:CANDIDATES]
            known = _known_pairs(conn, [x["agent_id"] for x in pool])
            scored = []
            for i in range(len(pool)):
                for j in range(i + 1, len(pool)):
                    a, b = pool[i], pool[j]
                    scored.append(
                        (_pair_score(ctx, known, a["agent_id"], b["agent_id"],
                                     r.random()), i, j))
            scored.sort(key=lambda t: (-t[0], t[1], t[2]))
            # greedily take the best pairs, each person talking once
            used, pairs = set(), []
            for _s, i, j in scored:
                if i in used or j in used:
                    continue
                used.add(i)
                used.add(j)
                pairs.append((pool[i], pool[j]))
                if len(pairs) >= max_pairs_per_place:
                    break
        for a, b in pairs:
            # interaction probability: strangers lower, acquaintances higher
            rel = _get_rel(conn, a["agent_id"], b["agent_id"])
            p_int = 0.28 if not rel else min(0.9, 0.3 + rel["familiarity"] / 40)
            if holiday_venue_active:
                p_int = max(p_int, HOLIDAY_P_INTERACT)
            if r.random() < p_int:
                ra = conn.execute("SELECT * FROM agents WHERE id=?", (a["agent_id"],)).fetchone()
                rb = conn.execute("SELECT * FROM agents WHERE id=?", (b["agent_id"],)).fetchone()
                interact(conn, ra, rb, p["id"], tick, seed)
                n_interactions += 1
    return n_interactions
