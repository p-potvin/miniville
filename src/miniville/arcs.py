"""Emergent Multi-Day Story Arcs (v0.53).

Threads discrete ledger events into multi-day town storylines (`feud`,
`romance`, `turnaround`, `mentorship`, `civic_drama`) persisted in
`story_arcs`. Capped at `MAX_ACTIVE_ARCS = 8` active threads and resolved
either on a natural climax (marriage, graduation, shop closure, loan payoff)
or after `STALE_AFTER_DAYS = 21` quiet days.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .events import describe
from .timekeeper import day_of

MAX_ACTIVE_ARCS = 8
STALE_AFTER_DAYS = 21

_TAG_CLASS: dict[str, tuple[str, int, bool]] = {
    # tag -> (arc_kind, heat_delta, resolves_arc)
    "betrayal":                ("feud",        3, False),
    "separation":              ("feud",        3, True),
    "slander":                 ("feud",        2, False),
    "boycott":                 ("feud",        2, False),
    "schism":                  ("feud",        3, False),
    "cohabitation":            ("romance",     2, False),
    "marriage":                ("romance",     3, True),
    "birth":                   ("romance",     2, False),
    "mentorship_started":      ("mentorship",  1, False),
    "apprenticeship_completed":("mentorship",  3, True),
    "loan_issued":             ("turnaround",  2, False),
    "loan_repaid":             ("turnaround",  3, True),
    "business_founded":        ("turnaround",  2, False),
    "business_reopened":       ("turnaround",  2, False),
    "business_closed":         ("turnaround",  3, True),
    "shock_fire":              ("turnaround",  3, False),
    "shock_closure":           ("turnaround",  2, False),
    "election":                ("civic_drama", 3, True),
    "by_election":             ("civic_drama", 2, False),
    "press_influence":         ("civic_drama", 3, False),
    "weather_alert":           ("civic_drama", 2, False),
    "civic_project_completed": ("civic_drama", 2, True),
}


def _classify(ev: sqlite3.Row) -> tuple[str, int, bool] | None:
    kind = ev["kind"]
    try:
        data = json.loads(ev["data"] or "{}")
    except ValueError:
        data = {}
    tag = str(data.get("tag") or "")
    if tag in _TAG_CLASS:
        return _TAG_CLASS[tag]
    if kind in ("betrayal", "affair"):
        return ("feud", 2, False)
    if kind == "conflict":
        return ("feud", 2, False)
    if kind == "relationship":
        lbl = str(data.get("label") or "")
        if lbl in ("rival", "estranged"):
            return ("feud", 2, False)
        if lbl in ("sweetheart", "partner", "spouse"):
            return ("romance", 2, lbl == "spouse")
    return None


def _default_title(conn: sqlite3.Connection, arc_kind: str,
                   a_id: int | None, b_id: int | None, p_id: int | None) -> str:
    def _aname(aid: int | None) -> str:
        if not aid:
            return ""
        r = conn.execute("SELECT name FROM agents WHERE id=?", (aid,)).fetchone()
        return r["name"] if r else f"Resident #{aid}"

    def _pname(pid: int | None) -> str:
        if not pid:
            return ""
        r = conn.execute("SELECT name FROM places WHERE id=?", (pid,)).fetchone()
        return r["name"] if r else f"Venue #{pid}"

    na, nb, np = _aname(a_id), _aname(b_id), _pname(p_id)
    if arc_kind == "feud":
        return f"Rising Friction: {na} & {nb}" if (na and nb) else f"Discord at {np or 'Miniville'}"
    if arc_kind == "romance":
        return f"Courtship: {na} & {nb}" if (na and nb) else f"Hearts in {np or 'Miniville'}"
    if arc_kind == "mentorship":
        return f"Passing the Torch: {na} -> {nb}" if (na and nb) else f"Apprenticeship of {na or nb}"
    if arc_kind == "turnaround":
        return f"Main Street Stakes: {np}" if np else f"Enterprise Watch: {na or 'Miniville'}"
    return f"Town Hall & Civic Pulse"


def _matches(arc: sqlite3.Row, arc_kind: str,
             a_id: int | None, b_id: int | None, p_id: int | None) -> bool:
    if arc["kind"] != arc_kind:
        return False
    arc_agents = {x for x in (arc["primary_agent_id"], arc["secondary_agent_id"]) if x is not None}
    ev_agents = {x for x in (a_id, b_id) if x is not None}
    if arc_agents and ev_agents and (arc_agents & ev_agents):
        return True
    if p_id is not None and arc["place_id"] is not None and int(p_id) == int(arc["place_id"]):
        return True
    if arc_kind == "civic_drama" and not ev_agents and not p_id:
        return True
    if arc_kind == "civic_drama" and not arc_agents and not arc["place_id"]:
        return True
    return False


def due(conn: sqlite3.Connection, tick: int, seed: str = "miniville") -> dict[str, int]:
    """Scan recent notable events to open, progress, and resolve multi-day arcs."""
    day = day_of(tick)
    scan_from = max(0, day - 1)
    evs = conn.execute(
        """SELECT * FROM events
           WHERE day BETWEEN ? AND ? AND importance >= 2
           ORDER BY id ASC LIMIT 250""",
        (scan_from, day),
    ).fetchall()

    opened = 0
    progressed = 0
    resolved = 0

    for ev in evs:
        cls = _classify(ev)
        if not cls:
            continue
        arc_kind, heat_delta, resolves = cls
        a_id = ev["a_id"]
        b_id = ev["b_id"]
        p_id = ev["place_id"]
        ev_day = int(ev["day"])
        chap_text = describe(conn, ev)
        chap_key = f"d{ev_day}:{chap_text}"

        active_rows = conn.execute(
            "SELECT * FROM story_arcs WHERE status = 'active' ORDER BY last_day DESC, id DESC"
        ).fetchall()
        target = next((ar for ar in active_rows if _matches(ar, arc_kind, a_id, b_id, p_id)), None)

        if target is not None:
            chapters = json.loads(target["chapters_json"] or "[]")
            seen_keys = {f"d{int(c.get('day', 0)) - 1}:{c.get('text', '')}" for c in chapters}
            seen_keys |= {f"d{c.get('raw_day', -1)}:{c.get('text', '')}" for c in chapters}
            if chap_key in seen_keys:
                continue
            chapters.append({"day": ev_day + 1, "raw_day": ev_day, "text": chap_text})
            chapters = chapters[-10:]
            new_heat = min(50, int(target["heat"] or 1) + heat_delta)
            new_status = "resolved" if resolves else "active"
            conn.execute(
                """UPDATE story_arcs
                   SET last_day = MAX(last_day, ?),
                       chapters_json = ?,
                       heat = ?,
                       status = ?
                   WHERE id = ?""",
                (ev_day, json.dumps(chapters), new_heat, new_status, target["id"]),
            )
            if resolves:
                resolved += 1
            else:
                progressed += 1
        elif not resolves or arc_kind in ("romance", "turnaround", "civic_drama"):
            title = _default_title(conn, arc_kind, a_id, b_id, p_id)
            chapters = [{"day": ev_day + 1, "raw_day": ev_day, "text": chap_text}]
            status = "resolved" if resolves else "active"
            conn.execute(
                """INSERT INTO story_arcs(
                       kind, title, primary_agent_id, secondary_agent_id,
                       place_id, started_day, last_day, status, chapters_json, heat)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (arc_kind, title, a_id, b_id, p_id, ev_day, ev_day,
                 status, json.dumps(chapters), max(1, heat_delta)),
            )
            if status == "active":
                opened += 1
            else:
                resolved += 1

    # Retire stale arcs
    cur_stale = conn.execute(
        "UPDATE story_arcs SET status = 'resolved' WHERE status = 'active' AND last_day < ?",
        (day - STALE_AFTER_DAYS,),
    )
    resolved += int(cur_stale.rowcount or 0)

    # Cap active arcs at MAX_ACTIVE_ARCS
    active_ids = [
        int(r["id"])
        for r in conn.execute(
            "SELECT id FROM story_arcs WHERE status = 'active' "
            "ORDER BY heat DESC, last_day DESC, id DESC"
        ).fetchall()
    ]
    if len(active_ids) > MAX_ACTIVE_ARCS:
        trim = active_ids[MAX_ACTIVE_ARCS:]
        ph = ",".join("?" * len(trim))
        conn.execute(
            f"UPDATE story_arcs SET status = 'resolved' WHERE id IN ({ph})",
            tuple(trim),
        )
        resolved += len(trim)

    return {"opened": opened, "progressed": progressed, "resolved": resolved}


def list_arcs(conn: sqlite3.Connection, status: str = "active",
              limit: int = 15) -> list[dict[str, Any]]:
    where = "" if status == "all" else "WHERE sa.status = ?"
    params: tuple = (limit,) if status == "all" else (status, limit)
    rows = conn.execute(
        f"""SELECT sa.*,
                   pa.name AS primary_name,
                   pb.name AS secondary_name,
                   pl.name AS place_name
            FROM story_arcs sa
            LEFT JOIN agents pa ON pa.id = sa.primary_agent_id
            LEFT JOIN agents pb ON pb.id = sa.secondary_agent_id
            LEFT JOIN places pl ON pl.id = sa.place_id
            {where}
            ORDER BY CASE WHEN sa.status='active' THEN 0 ELSE 1 END,
                     sa.heat DESC, sa.last_day DESC, sa.id DESC
            LIMIT ?""",
        params,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["chapters"] = json.loads(d.pop("chapters_json", "[]") or "[]")
        except ValueError:
            d["chapters"] = []
        out.append(d)
    return out
