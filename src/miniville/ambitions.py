"""Personal ambitions: forward-looking life goals that steer resident behaviour."""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_meta
from .events import NOTABLE, emit
from .individual import profile_of
from .memory import remember
from .rng import rng_for
from .timekeeper import TICKS_PER_DAY, day_of

COOLDOWN_DAYS = 14
NEST_EGG_DELTA_CENTS = 120_000   # save +$1,200 above baseline wallet
STANDING_DELTA_GOAL = 6          # gain +6 standing

AMBITION_LABELS = {
    "find_a_job": "Find steady work",
    "save_nest_egg": "Build a $1,200 nest egg",
    "climb_the_ladder": "Earn a promotion or master their craft",
    "find_true_love": "Find a sweetheart",
    "pillar_of_the_community": "Become a pillar of the community",
    "make_peace": "Bury an old grudge",
}


def _choose_ambition(agent: sqlite3.Row, prof: dict[str, Any],
                     grudges: dict[int, int], coupled: set[int],
                     r) -> tuple[str, int, int] | None:
    aid = int(agent["id"])
    age = int(agent["age"] or 30)
    standing = int(agent["standing"] or 0)
    wallet = int(agent["money_cents"] or 0)
    has_job = agent["work_place_id"] is not None
    rank = int(agent["job_rank"] if agent["job_rank"] is not None else 0)

    if age < 65 and not has_job:
        return ("find_a_job", 0, 1)

    if float(prof.get("resilience", 0.0)) >= 0.0 or float(prof.get("sociability", 0.0)) >= 0.1:
        other = grudges.get(aid)
        if other is not None and r.random() < 0.35:
            return ("make_peace", other, 1)

    ms = agent["marital_status"] or "never_married"
    if ms in ("never_married", "divorced", "widowed") and aid not in coupled:
        if r.random() < 0.45:
            return ("find_true_love", 0, 1)

    if has_job and rank < 2 and float(prof.get("industriousness", 0.0)) >= -0.1:
        if r.random() < 0.50:
            return ("climb_the_ladder", rank, rank + 1)

    if float(prof.get("thrift", 0.0)) >= 0.0 or wallet < 700_000:
        if r.random() < 0.55:
            return ("save_nest_egg", wallet, wallet + NEST_EGG_DELTA_CENTS)

    if standing < 35:
        return ("pillar_of_the_community", standing, standing + STANDING_DELTA_GOAL)

    return ("save_nest_egg", wallet, wallet + NEST_EGG_DELTA_CENTS)


def assign_ambitions(conn: sqlite3.Connection, tick: int,
                     seed: str = "miniville") -> int:
    day = day_of(tick)
    min_completed_tick = tick - COOLDOWN_DAYS * TICKS_PER_DAY
    rows = conn.execute(
        """SELECT a.id, a.age, a.marital_status, a.work_place_id,
                  COALESCE(a.standing, 0) AS standing,
                  s.money_cents, j.rank AS job_rank
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN jobs j ON j.agent_id = a.id
           WHERE a.alive = 1 AND a.is_child = 0
             AND NOT EXISTS (
                 SELECT 1 FROM ambitions am
                 WHERE am.agent_id = a.id
                   AND (am.status = 'active'
                        OR (am.completed_tick IS NOT NULL AND am.completed_tick > ?))
             )
           ORDER BY a.id""",
        (min_completed_tick,),
    ).fetchall()
    if not rows:
        return 0

    # Batch-preload grudges and romantic ties in two single scans
    grudges: dict[int, int] = {}
    for rel in conn.execute(
        "SELECT a_id, b_id FROM relationships WHERE affinity <= -15 ORDER BY affinity ASC"
    ).fetchall():
        grudges.setdefault(int(rel["a_id"]), int(rel["b_id"]))
        grudges.setdefault(int(rel["b_id"]), int(rel["a_id"]))

    coupled: set[int] = set()
    for rel in conn.execute(
        "SELECT a_id, b_id FROM relationships WHERE label IN ('sweetheart','partner','spouse')"
    ).fetchall():
        coupled.add(int(rel["a_id"]))
        coupled.add(int(rel["b_id"]))

    inserts = []
    for row in rows:
        aid = int(row["id"])
        r = rng_for(seed, "ambition_pick", aid, day)
        prof = profile_of(conn, aid, seed)
        picked = _choose_ambition(row, prof, grudges, coupled, r)
        if not picked:
            continue
        kind, base_val, target_val = picked
        inserts.append((aid, kind, tick, base_val, target_val, 0.0, "active"))

    if inserts:
        conn.executemany(
            """INSERT OR REPLACE INTO ambitions(
                   agent_id, kind, started_tick, baseline_value,
                   target_value, progress, status, completed_tick)
               VALUES(?, ?, ?, ?, ?, ?, ?, NULL)""",
            inserts,
        )
    return len(inserts)


def evaluate_ambitions(conn: sqlite3.Connection, tick: int,
                       seed: str = "miniville") -> int:
    rows = conn.execute(
        """SELECT am.*, a.name, a.work_place_id, COALESCE(a.standing, 0) AS standing,
                  s.money_cents, s.place_id, j.rank AS job_rank
           FROM ambitions am
           JOIN agents a ON a.id = am.agent_id
           JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN jobs j ON j.agent_id = a.id
           WHERE am.status = 'active' AND a.alive = 1
           ORDER BY am.agent_id"""
    ).fetchall()
    if not rows:
        return 0

    place_names = {
        r["id"]: r["name"]
        for r in conn.execute("SELECT id, name FROM places").fetchall()
    }
    agent_names = {
        r["id"]: r["name"]
        for r in conn.execute("SELECT id, name FROM agents WHERE alive=1").fetchall()
    }
    bt_ticks: dict[int, list[int]] = {}
    for ev in conn.execute(
        "SELECT a_id, tick FROM events WHERE kind='life_event' AND data LIKE ?", ('%"craft_breakthrough"%',)
    ).fetchall():
        if ev["a_id"]:
            bt_ticks.setdefault(int(ev["a_id"]), []).append(int(ev["tick"]))

    best_rom: dict[int, tuple[float, str, int]] = {}
    pair_aff: dict[tuple[int, int], float] = {}
    for rel in conn.execute(
        "SELECT a_id, b_id, label, romance, affinity FROM relationships"
    ).fetchall():
        a_id, b_id = int(rel["a_id"]), int(rel["b_id"])
        rom = float(rel["romance"] or 0.0)
        lbl = rel["label"]
        pair_aff[(a_id, b_id)] = float(rel["affinity"] or 0.0)
        if b_id in agent_names:
            cur = best_rom.get(a_id)
            if cur is None or (lbl in ("sweetheart", "partner", "spouse") and cur[1] not in ("sweetheart", "partner", "spouse")) or rom > cur[0]:
                best_rom[a_id] = (rom, lbl, b_id)
        if a_id in agent_names:
            cur = best_rom.get(b_id)
            if cur is None or (lbl in ("sweetheart", "partner", "spouse") and cur[1] not in ("sweetheart", "partner", "spouse")) or rom > cur[0]:
                best_rom[b_id] = (rom, lbl, a_id)

    completed = 0
    prog_updates = []
    for am in rows:
        aid = int(am["agent_id"])
        kind = am["kind"]
        base = int(am["baseline_value"] or 0)
        target = int(am["target_value"] or 1)
        prog = float(am["progress"] or 0.0)
        done = False
        summary_txt = ""

        if kind == "find_a_job":
            if am["work_place_id"] is not None:
                prog = 1.0
                done = True
                vname = place_names.get(am["work_place_id"], "town")
                summary_txt = f"fulfilled their ambition to find steady work at {vname}"
            else:
                prog = 0.25

        elif kind == "save_nest_egg":
            wallet = int(am["money_cents"] or 0)
            span = max(1, target - base)
            prog = max(0.0, min(1.0, (wallet - base) / span))
            if wallet >= target:
                done = True
                summary_txt = f"reached their personal savings goal with ${wallet / 100:,.0f} in the bank"

        elif kind == "climb_the_ladder":
            rank = int(am["job_rank"] if am["job_rank"] is not None else 0)
            started = int(am["started_tick"] or 0)
            bt_count = sum(1 for t in bt_ticks.get(aid, ()) if t >= started)
            if rank >= target or bt_count >= 2:
                prog = 1.0
                done = True
                summary_txt = "achieved their career ambition through hard work"
            else:
                prog = min(0.9, 0.45 * bt_count + 0.15)

        elif kind == "find_true_love":
            br = best_rom.get(aid)
            if br and br[1] in ("sweetheart", "partner", "spouse"):
                prog = 1.0
                done = True
                summary_txt = f"fulfilled their romantic ambition with {agent_names.get(br[2], 'their partner')}"
            elif br:
                prog = min(0.9, br[0] / 80.0)

        elif kind == "pillar_of_the_community":
            st_now = int(am["standing"] or 0)
            span = max(1, target - base)
            prog = max(0.0, min(1.0, (st_now - base) / span))
            if st_now >= target:
                done = True
                summary_txt = f"became a respected pillar of Miniville (standing {st_now:+d})"

        elif kind == "make_peace":
            other_id = base
            lo, hi = min(aid, other_id), max(aid, other_id)
            aff = pair_aff.get((lo, hi))
            if aff is None or aff > -5.0:
                prog = 1.0
                done = True
                oname = agent_names.get(other_id, "an old rival")
                summary_txt = f"made peace with {oname} and let go of an old grudge"
            else:
                prog = max(0.0, min(0.9, (aff + 40.0) / 35.0))

        if done:
            conn.execute(
                """UPDATE ambitions
                   SET progress = 1.0, status = 'completed', completed_tick = ?
                   WHERE agent_id = ?""",
                (tick, aid),
            )
            conn.execute(
                """UPDATE agent_state
                   SET fun = MIN(100, fun + 18), stress = MAX(0, stress - 14),
                       mood = 'content'
                   WHERE agent_id = ?""",
                (aid,),
            )
            emit(conn, tick, "life_event", place_id=am["place_id"], a=aid,
                 importance=NOTABLE, text=summary_txt,
                 tag="ambition_achieved", ambition=kind)
            remember(conn, aid, tick, f"I {summary_txt}!",
                     kind="reflection", importance=4)
            completed += 1
        else:
            prog_updates.append((round(prog, 3), aid))

    if prog_updates:
        conn.executemany(
            "UPDATE ambitions SET progress = ? WHERE agent_id = ?", prog_updates
        )
    return completed


def due(conn: sqlite3.Connection, tick: int, seed: str = "miniville") -> dict[str, int]:
    completed = evaluate_ambitions(conn, tick, seed)
    assigned = assign_ambitions(conn, tick, seed)
    return {"completed": completed, "assigned": assigned}


def active_savers(conn: sqlite3.Connection) -> set[int]:
    return {
        int(r["agent_id"])
        for r in conn.execute(
            "SELECT agent_id FROM ambitions WHERE status='active' AND kind='save_nest_egg'"
        ).fetchall()
    }


def active_romantics(conn: sqlite3.Connection) -> set[int]:
    return {
        int(r["agent_id"])
        for r in conn.execute(
            "SELECT agent_id FROM ambitions WHERE status='active' AND kind='find_true_love'"
        ).fetchall()
    }


def ambition_of(conn: sqlite3.Connection, agent_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM ambitions WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["label"] = AMBITION_LABELS.get(d["kind"], d["kind"])
    return d


def list_ambitions(conn: sqlite3.Connection, status: str = "active",
                   limit: int = 40) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT am.*, a.name, a.occupation, p.name AS place_name
           FROM ambitions am
           JOIN agents a ON a.id = am.agent_id
           LEFT JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN places p ON p.id = s.place_id
           WHERE a.alive = 1 AND (? = 'all' OR am.status = ?)
           ORDER BY am.progress DESC, am.agent_id LIMIT ?""",
        (status, status, limit),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["label"] = AMBITION_LABELS.get(item["kind"], item["kind"])
        out.append(item)
    return out
