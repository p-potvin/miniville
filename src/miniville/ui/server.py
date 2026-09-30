"""FastAPI read-only observer API + static dashboard.

Never writes to the DB. Opened per-request via miniville.db.connect on the
resolved path so the dashboard follows MINIVILLE_DB / --db like the CLI.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import db as dbmod
from ..events import describe
from ..timekeeper import TICKS_PER_DAY, day_of, fmt_tick

STATIC = Path(__file__).parent / "static"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


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
                "SELECT mood, COUNT(*) c FROM agent_state GROUP BY mood")}
            pop = c.execute(
                "SELECT COUNT(*) n FROM agents WHERE alive=1").fetchone()["n"]
            return {"tick": tick, "day": day_of(tick) + 1,
                    "time": fmt_tick(tick), "population": pop, "moods": moods}
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
            for e in rows:
                e["text"] = describe(c, e)
                e["tick_of_day"] = e["tick"] % TICKS_PER_DAY
            return rows
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
            return {"agent": dict(a), "state": dict(st) if st else {},
                    "job": dict(job) if job else None,
                    "debts": {"owes": owed_by, "owed": owed_to},
                    "relationships": rels, "recent": recent}
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

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def serve(db_path: str | None, host: str, port: int) -> None:
    import uvicorn
    uvicorn.run(create_app(db_path), host=host, port=port, log_level="warning")
