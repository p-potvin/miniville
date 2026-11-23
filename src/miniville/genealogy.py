"""Genealogy, family trees, and town dynasties (v0.52).

Tracks parent-child lineage (`agents.parent_a_id`, `agents.parent_b_id`),
idempotently backfills pre-v0.52 lineages from `born-<a_id>-<b_id>-<tick>`
UUIDs and founding household guardians, resolves immediate kin trees, and
aggregates town dynasties by surname.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_meta, set_meta


def backfill_lineage(conn: sqlite3.Connection, force: bool = False) -> int:
    """Populate parent_a_id/parent_b_id once for existing households and births."""
    if not force and get_meta(conn, "lineage_v1"):
        return 0
    rows = conn.execute(
        """SELECT id, uuid, age, is_child, household_id, parent_a_id, parent_b_id
           FROM agents WHERE parent_a_id IS NULL AND parent_b_id IS NULL"""
    ).fetchall()
    if not rows:
        set_meta(conn, "lineage_v1", "1")
        return 0

    adults_by_hh: dict[int, list[sqlite3.Row]] = {}
    for a in conn.execute(
        """SELECT id, age, household_id FROM agents
           WHERE is_child = 0 AND household_id IS NOT NULL
           ORDER BY COALESCE(age, 30) DESC, id ASC"""
    ).fetchall():
        adults_by_hh.setdefault(int(a["household_id"]), []).append(a)

    updates: list[tuple[int | None, int | None, int]] = []
    for r in rows:
        aid = int(r["id"])
        uuid = r["uuid"] or ""
        if uuid.startswith("born-"):
            parts = uuid.split("-")
            if len(parts) >= 4 and parts[1].isdigit() and parts[2].isdigit():
                pa, pb = int(parts[1]), int(parts[2])
                if pa != aid and pb != aid:
                    updates.append((pa, pb, aid))
                    continue

        if r["is_child"] and r["household_id"] is not None:
            cand = [
                int(g["id"])
                for g in adults_by_hh.get(int(r["household_id"]), [])
                if int(g["id"]) != aid and (g["age"] is None or r["age"] is None or int(g["age"]) >= int(r["age"]) + 16)
            ]
            if cand:
                pa = cand[0]
                pb = cand[1] if len(cand) > 1 else None
                updates.append((pa, pb, aid))

    if updates:
        conn.executemany(
            "UPDATE agents SET parent_a_id = ?, parent_b_id = ? WHERE id = ?",
            updates,
        )
    set_meta(conn, "lineage_v1", "1")
    return len(updates)


def _agent_brief(row: sqlite3.Row | dict) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "age": row["age"],
        "alive": bool(row["alive"]),
        "occupation": row["occupation"],
    }


def _generation_depth(conn: sqlite3.Connection, agent_id: int) -> int:
    seen: set[int] = set()
    cur_ids = [agent_id]
    depth = 1
    while cur_ids:
        next_ids: list[int] = []
        for cid in cur_ids:
            if cid in seen:
                continue
            seen.add(cid)
            r = conn.execute(
                "SELECT parent_a_id, parent_b_id FROM agents WHERE id=?",
                (cid,),
            ).fetchone()
            if r:
                for pcol in (r["parent_a_id"], r["parent_b_id"]):
                    if pcol and int(pcol) not in seen:
                        next_ids.append(int(pcol))
        if next_ids:
            depth += 1
            cur_ids = next_ids
        else:
            break
    return depth


def family_tree(conn: sqlite3.Connection, agent_id: int) -> dict[str, Any] | None:
    """Return parents, children, siblings, spouse, generation, and tradition."""
    a = conn.execute(
        """SELECT a.*, h.tradition AS household_tradition
           FROM agents a
           LEFT JOIN households h ON h.id = a.household_id
           WHERE a.id = ?""",
        (agent_id,),
    ).fetchone()
    if not a:
        return None

    pids = [int(x) for x in (a["parent_a_id"], a["parent_b_id"]) if x is not None]
    parents: list[dict[str, Any]] = []
    if pids:
        ph = ",".join("?" * len(pids))
        for r in conn.execute(
            f"SELECT id, name, age, alive, occupation FROM agents WHERE id IN ({ph}) ORDER BY id",
            tuple(pids),
        ).fetchall():
            parents.append(_agent_brief(r))

    children = [
        _agent_brief(r)
        for r in conn.execute(
            """SELECT id, name, age, alive, occupation FROM agents
               WHERE parent_a_id = ? OR parent_b_id = ?
               ORDER BY COALESCE(birth_day, 0) ASC, id ASC""",
            (agent_id, agent_id),
        ).fetchall()
    ]

    siblings: list[dict[str, Any]] = []
    if pids:
        if len(pids) == 1:
            srows = conn.execute(
                """SELECT id, name, age, alive, occupation FROM agents
                   WHERE id != ? AND (parent_a_id = ? OR parent_b_id = ?)
                   ORDER BY COALESCE(age, 0) DESC, id ASC""",
                (agent_id, pids[0], pids[0]),
            ).fetchall()
        else:
            srows = conn.execute(
                """SELECT id, name, age, alive, occupation FROM agents
                   WHERE id != ?
                     AND (parent_a_id IN (?, ?) OR parent_b_id IN (?, ?))
                   ORDER BY COALESCE(age, 0) DESC, id ASC""",
                (agent_id, pids[0], pids[1], pids[0], pids[1]),
            ).fetchall()
        siblings = [_agent_brief(r) for r in srows]

    spouse_row = conn.execute(
        """SELECT r.label, x.id, x.name, x.age, x.alive, x.occupation
           FROM relationships r
           JOIN agents x ON x.id = (CASE WHEN r.a_id = ? THEN r.b_id ELSE r.a_id END)
           WHERE (r.a_id = ? OR r.b_id = ?) AND r.label IN ('spouse', 'partner')
           ORDER BY CASE r.label WHEN 'spouse' THEN 0 ELSE 1 END, r.familiarity DESC
           LIMIT 1""",
        (agent_id, agent_id, agent_id),
    ).fetchone()

    surname = (a["name"] or "Miniville").split()[-1]
    return {
        "id": int(a["id"]),
        "name": a["name"],
        "surname": surname,
        "generation": _generation_depth(conn, int(a["id"])),
        "household_tradition": a["household_tradition"],
        "parents": parents,
        "spouse": {**_agent_brief(spouse_row), "label": spouse_row["label"]} if spouse_row else None,
        "children": children,
        "siblings": siblings,
    }


def top_dynasties(conn: sqlite3.Connection, limit: int = 15) -> list[dict[str, Any]]:
    """Aggregate Miniville families by surname across living and deceased generations."""
    rows = conn.execute(
        """SELECT a.id, a.name, a.age, a.alive, a.is_child, a.standing,
                  a.parent_a_id, a.parent_b_id,
                  COALESCE(s.money_cents, 0) AS money_cents
           FROM agents a
           LEFT JOIN agent_state s ON s.agent_id = a.id
           ORDER BY COALESCE(a.standing, 0) DESC, COALESCE(s.money_cents, 0) DESC, a.id ASC"""
    ).fetchall()

    by_id: dict[int, sqlite3.Row] = {int(r["id"]): r for r in rows}
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        parts = (r["name"] or "").strip().split()
        if not parts:
            continue
        surname = parts[-1]
        g = groups.setdefault(surname, {
            "surname": surname,
            "living": 0,
            "deceased": 0,
            "minors": 0,
            "total_wealth_cents": 0,
            "max_standing": 0,
            "generations": 1,
            "notables": [],
        })
        if r["alive"]:
            g["living"] += 1
            g["total_wealth_cents"] += int(r["money_cents"] or 0)
            if len(g["notables"]) < 2 and not r["is_child"]:
                g["notables"].append({"id": int(r["id"]), "name": r["name"]})
        else:
            g["deceased"] += 1
        if r["is_child"]:
            g["minors"] += 1
        g["max_standing"] = max(g["max_standing"], int(r["standing"] or 0))
        if r["parent_a_id"] is not None or r["parent_b_id"] is not None:
            pa = by_id.get(int(r["parent_a_id"])) if r["parent_a_id"] else None
            pb = by_id.get(int(r["parent_b_id"])) if r["parent_b_id"] else None
            has_grand = bool(
                (pa and (pa["parent_a_id"] or pa["parent_b_id"]))
                or (pb and (pb["parent_a_id"] or pb["parent_b_id"]))
            )
            g["generations"] = max(g["generations"], 3 if has_grand else 2)

    ranked = sorted(
        groups.values(),
        key=lambda d: (-d["living"], -d["generations"], -d["total_wealth_cents"], d["surname"]),
    )
    return ranked[:limit]
