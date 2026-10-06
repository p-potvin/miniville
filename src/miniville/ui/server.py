"""FastAPI read-only observer API + static dashboard.

Never writes to the DB. Opened per-request via miniville.db.connect on the
resolved path so the dashboard follows MINIVILLE_DB / --db like the CLI.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import db as dbmod
from .. import economy
from ..events import describe
from ..seasons import fmt_date, holiday_for, season_of
from ..timekeeper import TICKS_PER_DAY, day_of, fmt_tick

STATIC = Path(__file__).parent / "static"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# Map canvas is 1600x900; districts get fixed tiles so the geography reads the
# same for every spectator. Lake is up north, The Flats down south.
DISTRICT_TILES = {
    "Lakeshore":        (30,    20, 560, 300),
    "Greenhill":        (30,   340, 560, 300),
    "Downtown":         (620,  170, 430, 400),
    "Old Mill Quarter": (1070,  20, 500, 300),
    "The Flats":        (1070, 340, 500, 300),
}


def _district_layout() -> dict:
    zones = {name: {"name": name, "x": x, "y": y, "w": w, "h": h}
             for name, (x, y, w, h) in DISTRICT_TILES.items()}
    zones["_other"] = {"name": "Elsewhere", "x": 620, "y": 600, "w": 950, "h": 270}
    return zones


def _venue_xy(zone: dict, index: int) -> tuple[int, int]:
    """Grid layout inside the venue strip of a district tile (the bottom band
    is reserved for the homes block)."""
    cols = max(1, (zone["w"] - 20) // 110)
    col, row = index % cols, index // cols
    return (zone["x"] + 60 + col * 110, zone["y"] + 70 + row * 90)


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Miniville Observer", docs_url=None)

    def conn() -> sqlite3.Connection:
        return dbmod.connect(db_path)

    @app.get("/api/status")
    def status():
        c = conn()
        try:
            tick = int(dbmod.get_meta(c, "tick", "0") or 0)
            moods = {r["mood"]: r["c"] for r in c.execute(
                """SELECT s.mood, COUNT(*) c FROM agent_state s
                   JOIN agents a ON a.id=s.agent_id
                   WHERE a.alive=1 GROUP BY s.mood""")}
            pop = c.execute(
                "SELECT COUNT(*) n FROM agents WHERE alive=1").fetchone()["n"]
            day = day_of(tick)
            holiday = holiday_for(c, day)
            return {"tick": tick, "day": day + 1, "date": fmt_date(day),
                    "season": season_of(day),
                    "holiday": holiday.name if holiday else None,
                    "time": fmt_tick(tick), "population": pop, "moods": moods}
        finally:
            c.close()

    @app.get("/api/economy")
    def economy_view(days: int = Query(30, le=400)):
        c = conn()
        try:
            stats = economy.economy_stats(c)
            businesses = _rows(c,
                """SELECT p.name, p.kind, p.district, b.status, b.balance_cents,
                          b.revenue_total, b.payroll_total, b.price_index,
                          b.ema_traffic, b.closed_tick
                   FROM businesses b JOIN places p ON p.id=b.place_id
                   ORDER BY b.balance_cents DESC""")
            series = _rows(c,
                """SELECT * FROM economy_days WHERE money_supply_cents > 0
                   ORDER BY day DESC LIMIT ?""", (days,))
            series.reverse()
            return {"stats": stats, "businesses": businesses, "series": series}
        finally:
            c.close()

    @app.get("/api/venues")
    def venues():
        c = conn()
        try:
            occ = {r["place_id"]: r["n"] for r in c.execute(
                """SELECT place_id, COUNT(*) n FROM agent_state
                   WHERE place_id IS NOT NULL GROUP BY place_id""")}
            out = []
            for p in c.execute(
                    "SELECT id,name,kind,district,capacity FROM places ORDER BY kind,name"):
                out.append({**dict(p), "occupancy": occ.get(p["id"], 0)})
            return out
        finally:
            c.close()

    @app.get("/api/feed")
    def feed(day: int | None = Query(None), limit: int = Query(50, le=200)):
        c = conn()
        try:
            tick = int(dbmod.get_meta(c, "tick", "0") or 0)
            d = day - 1 if day else day_of(tick)
            rows = _rows(c,
                "SELECT * FROM events WHERE day=? ORDER BY importance DESC, id DESC LIMIT ?",
                (d, limit))
            # identical rendered lines (e.g. a pre-guard duplicate cohabitation)
            # merge into one row with a xN badge rather than repeating verbatim
            counts = {}
            for e in rows:
                e["text"] = describe(c, e)
                e["tick_of_day"] = e["tick"] % TICKS_PER_DAY
                counts[e["text"]] = counts.get(e["text"], 0) + 1
            merged, seen = [], set()
            for e in rows:
                if e["text"] in seen:
                    continue
                seen.add(e["text"])
                if counts[e["text"]] > 1:
                    e["text"] += f" (x{counts[e['text']]})"
                merged.append(e)
            return merged
        finally:
            c.close()

    @app.get("/api/residents")
    def residents(q: str = "", limit: int = Query(40, le=200)):
        c = conn()
        try:
            return _rows(c,
                """SELECT a.id, a.name, a.age, a.sex, a.occupation,
                          a.marital_status, a.avatar_path,
                          s.mood, s.activity, p.name place
                   FROM agents a
                   LEFT JOIN agent_state s ON s.agent_id=a.id
                   LEFT JOIN places p ON p.id=s.place_id
                   WHERE a.alive=1 AND a.name LIKE ?
                   ORDER BY a.name LIMIT ?""",
                (f"%{q}%", limit))
        finally:
            c.close()

    @app.get("/api/resident/{agent_id}")
    def resident(agent_id: int):
        c = conn()
        try:
            a = c.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
            if not a:
                raise HTTPException(404, "no such resident")
            st = c.execute(
                "SELECT * FROM agent_state WHERE agent_id=?", (agent_id,)).fetchone()
            rels = _rows(c,
                """SELECT r.label, r.familiarity, r.affinity, r.romance,
                          x.id, x.name
                   FROM relationships r
                   JOIN agents x ON x.id=(CASE WHEN r.a_id=? THEN r.b_id ELSE r.a_id END)
                   WHERE r.a_id=? OR r.b_id=?
                   ORDER BY r.familiarity DESC LIMIT 15""",
                (agent_id, agent_id, agent_id))
            recent = _rows(c,
                "SELECT * FROM events WHERE a_id=? OR b_id=? ORDER BY id DESC LIMIT 10",
                (agent_id, agent_id))
            for e in recent:
                e["text"] = describe(c, e)
            job = c.execute(
                """SELECT j.role, p.name place FROM jobs j
                   JOIN places p ON p.id=j.place_id WHERE j.agent_id=?""",
                (agent_id,)).fetchone()
            owed_by = _rows(c,
                """SELECT x.name creditor, d.kind FROM debts d
                   JOIN agents x ON x.id=d.creditor_id
                   WHERE d.debtor_id=? AND d.repaid_tick IS NULL""", (agent_id,))
            owed_to = _rows(c,
                """SELECT x.name debtor, d.kind FROM debts d
                   JOIN agents x ON x.id=d.debtor_id
                   WHERE d.creditor_id=? AND d.repaid_tick IS NULL""", (agent_id,))
            from ..memory import retrieve
            mems = [{"day": m["day"] + 1, "kind": m["kind"], "text": m["text"],
                     "importance": m["importance"]}
                    for m in retrieve(c, agent_id, k=8)]
            from ..groups import memberships_of
            from ..conflict import influence_of
            a = dict(a)
            a["influence"] = influence_of(c, agent_id)
            return {"agent": a, "state": dict(st) if st else {},
                    "job": dict(job) if job else None,
                    "debts": {"owes": owed_by, "owed": owed_to},
                    "relationships": rels, "recent": recent, "memories": mems,
                    "groups": memberships_of(c, agent_id)}
        finally:
            c.close()

    @app.get("/api/memories/{agent_id}")
    def memories(agent_id: int, k: int = Query(10, le=50)):
        c = conn()
        try:
            from ..memory import retrieve
            return [{"day": m["day"] + 1, "tick": m["tick"], "kind": m["kind"],
                     "text": m["text"], "importance": m["importance"]}
                    for m in retrieve(c, agent_id, k=k)]
        finally:
            c.close()

    @app.get("/api/newspaper")
    def newspaper(week: int | None = Query(None)):
        c = conn()
        try:
            if week is not None:
                row = c.execute("SELECT * FROM newspapers WHERE week=?",
                                (week - 1,)).fetchone()
                if not row:
                    raise HTTPException(404, "no edition for that week")
                return dict(row)
            rows = _rows(c, "SELECT week, created_tick FROM newspapers "
                            "ORDER BY week DESC LIMIT 20")
            for r in rows:
                r["week"] += 1
            latest = c.execute("SELECT * FROM newspapers ORDER BY week DESC "
                               "LIMIT 1").fetchone()
            return {"editions": rows,
                    "latest": dict(latest) if latest else None}
        finally:
            c.close()

    @app.get("/api/chronicle/{day}")
    def chronicle(day: int):
        c = conn()
        try:
            row = c.execute(
                "SELECT text FROM chronicle WHERE day=?", (day - 1,)).fetchone()
            narrs = _rows(c,
                "SELECT source, text FROM narratives WHERE day=?", (day - 1,))
            if not row and not narrs:
                raise HTTPException(404, "nothing written for that day")
            return {"day": day, "chronicle": row["text"] if row else None,
                    "narratives": narrs}
        finally:
            c.close()

    @app.get("/api/debts")
    def debts(open_only: bool = True, limit: int = Query(100, le=500)):
        c = conn()
        try:
            q = """SELECT d.id, d.kind, d.created_tick, d.repaid_tick,
                          x.name debtor, y.name creditor
                   FROM debts d JOIN agents x ON x.id=d.debtor_id
                   JOIN agents y ON y.id=creditor_id"""
            if open_only:
                q += " WHERE d.repaid_tick IS NULL"
            q += " ORDER BY d.created_tick DESC LIMIT ?"
            return _rows(c, q, (limit,))
        finally:
            c.close()

    @app.get("/api/map")
    def map_view():
        """Town map: district tiles, venue positions (deterministic per id),
        and every resident's current place + activity."""
        c = conn()
        try:
            districts = _district_layout()
            places = _rows(c, """
                SELECT p.id, p.name, p.kind, p.district, p.capacity,
                       p.open_tick, p.close_tick,
                       COALESCE(b.status,'open') AS bstatus
                FROM places p
                LEFT JOIN businesses b ON b.place_id=p.id
                WHERE p.kind != 'home'""")
            agents = _rows(c, """
                SELECT s.agent_id id, s.place_id place, s.activity, s.mood,
                       a.name, a.sex, hp.district AS hdist
                FROM agent_state s JOIN agents a ON a.id=s.agent_id
                LEFT JOIN places hp ON hp.id=a.home_place_id
                WHERE a.alive=1""")
            by_district = {}
            for p in places:
                by_district.setdefault(p["district"], []).append(p)
            for district, plist in by_district.items():
                zone = districts.get(district, districts["_other"])
                for i, p in enumerate(sorted(plist, key=lambda q: q["id"])):
                    p["x"], p["y"] = _venue_xy(zone, i)
                    p["closed"] = p["bstatus"] == "closed"
            home_counts = {name: c.execute(
                "SELECT COUNT(*) n FROM places WHERE kind='home' AND district=?",
                (name,)).fetchone()["n"] for name in DISTRICT_TILES}
            return {
                "districts": [dict(z, homes=home_counts.get(z["name"], 0))
                              for z in districts.values()],
                "places": places,
                "agents": agents,
            }
        finally:
            c.close()

    @app.get("/api/graph/{agent_id}")
    def graph(agent_id: int):
        """Ego relationship network: the resident, their partners (ring 1) and
        their partners' partners (ring 2). The client lays it out radially.
        Bounded to ~120 nodes / 160 edges so it stays readable."""
        c = conn()
        try:
            ego = c.execute(
                "SELECT id, name, sex FROM agents WHERE id=?",
                (agent_id,)).fetchone()
            if not ego:
                return {"error": "no such agent"}
            rels = _rows(c, """
                SELECT a_id, b_id, label, affinity, familiarity, romance
                FROM relationships
                WHERE a_id=? OR b_id=?""", (agent_id, agent_id))
            ring1 = sorted({(r["b_id"] if r["a_id"] == agent_id else r["a_id"])
                            for r in rels})
            # second ring: partners of partners, strongest first, capped
            ring2_raw = {}
            if ring1:
                ph = ",".join("?" * len(ring1))
                rows = c.execute(
                    f"""SELECT a_id, b_id, label, affinity, familiarity
                        FROM relationships
                        WHERE (a_id IN ({ph}) OR b_id IN ({ph}))
                          AND a_id != ? AND b_id != ?""",
                    (*ring1, *ring1, agent_id, agent_id)).fetchall()
                for r in rows:
                    for end in (r["a_id"], r["b_id"]):
                        if end != agent_id and end not in ring1:
                            ring2_raw[end] = max(
                                ring2_raw.get(end, 0), r["familiarity"])
                ring2_edges = [r for r in rows
                               if r["a_id"] in ring1 or r["b_id"] in ring1]
            else:
                ring2_edges = []
            ring2 = [k for k, _ in sorted(
                ring2_raw.items(), key=lambda kv: -kv[1])][:100]
            ids = [agent_id] + ring1 + ring2
            ph = ",".join("?" * len(ids))
            names = {r["id"]: r for r in c.execute(
                f"SELECT id, name, sex FROM agents WHERE id IN ({ph})", ids)}
            node_set = set(ids)
            edges = rels + ring2_edges
            # cap edges by familiarity for readability
            edges = [e for e in edges
                     if e["a_id"] in node_set and e["b_id"] in node_set]
            edges.sort(key=lambda e: -e["familiarity"])
            edges = edges[:160]
            nodes = ([{"id": agent_id, "name": ego["name"],
                       "sex": ego["sex"], "ring": 0}]
                     + [{"id": i, "name": names[i]["name"],
                         "sex": names[i]["sex"], "ring": 1}
                        for i in ring1 if i in names]
                     + [{"id": i, "name": names[i]["name"],
                         "sex": names[i]["sex"], "ring": 2}
                        for i in ring2 if i in names])
            return {"ego": dict(ego), "nodes": nodes, "edges": edges}
        finally:
            c.close()

    @app.get("/api/groups")
    def groups_view():
        """The town's affiliations, with their rosters."""
        from ..groups import roster
        c = conn()
        try:
            return {"groups": roster(c)}
        finally:
            c.close()

    @app.get("/api/council")
    def council_view():
        """Who governs, what they last decided, and what they have changed."""
        from ..politics import POLICIES, council, next_election_day, policy
        c = conn()
        try:
            return {
                "seats": council(c),
                "policies": {name: {"now": policy(c, name), "default": POLICIES[name][0],
                                    "low": POLICIES[name][1], "high": POLICIES[name][2]}
                             for name in sorted(POLICIES)},
                "next_election_day": next_election_day(c),
                "motions": _rows(c, "SELECT * FROM motions ORDER BY id DESC LIMIT 20"),
            }
        finally:
            c.close()

    @app.get("/api/shocks")
    def shocks_view():
        c = conn()
        try:
            from .. import shocks
            return shocks.list_shocks(c)
        finally:
            c.close()

    @app.get("/api/relationships")
    def relationships(label: str = "", limit: int = Query(100, le=500)):
        c = conn()
        try:
            return _rows(c,
                """SELECT r.label, r.familiarity, r.affinity, r.romance,
                          x.name a_name, y.name b_name, r.a_id, r.b_id
                   FROM relationships r
                   JOIN agents x ON x.id=r.a_id JOIN agents y ON y.id=r.b_id
                   WHERE r.label LIKE ? AND r.label != 'stranger'
                   ORDER BY r.familiarity DESC LIMIT ?""",
                (f"{label}%", limit))
        finally:
            c.close()

    # resident portraits live outside the repo (D:\miniville by default)
    av_dir = Path(os.environ.get("MINIVILLE_AVATARS_DIR", r"D:\miniville\avatars"))
    if av_dir.is_dir():
        app.mount("/avatars", StaticFiles(directory=av_dir), name="avatars")
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def serve(db_path: str | None, host: str, port: int) -> None:
    import uvicorn
    uvicorn.run(create_app(db_path), host=host, port=port, log_level="warning")
