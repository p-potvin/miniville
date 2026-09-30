"""Snapshot + export: rotate DB backups and dump chronicle/narratives to JSON."""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

KEEP_BACKUPS = 10


def _db_path(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()["file"])


def backup_db(conn: sqlite3.Connection, keep: int = KEEP_BACKUPS) -> Path:
    """Copy the live DB file into backups/ and prune old snapshots."""
    src = _db_path(conn)
    tick = conn.execute(
        "SELECT value FROM meta WHERE key='tick'").fetchone()["value"]
    out_dir = src.parent.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"miniville-t{int(tick):06d}.db"
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    shutil.copy2(src, dest)
    snaps = sorted(out_dir.glob("miniville-t*.db"))
    for old in snaps[:-keep]:
        old.unlink()
    return dest


def export_days(conn: sqlite3.Connection, out_dir: Path | None = None) -> int:
    """Write export/day-NNN.json for every chronicled day. Returns count."""
    src = _db_path(conn)
    out = out_dir or (src.parent.parent / "export")
    out.mkdir(parents=True, exist_ok=True)
    days = [r["day"] for r in conn.execute("SELECT day FROM chronicle ORDER BY day")]
    for day in days:
        events = [dict(r, data=json.loads(r["data"])) for r in conn.execute(
            "SELECT tick,kind,place_id,a_id,b_id,importance,data FROM events WHERE day=?",
            (day,))]
        chron = conn.execute(
            "SELECT text FROM chronicle WHERE day=?", (day,)).fetchone()
        narrs = [dict(r) for r in conn.execute(
            "SELECT source, text FROM narratives WHERE day=?", (day,))]
        payload = {
            "day": day,
            "chronicle": chron["text"] if chron else None,
            "narratives": narrs,
            "events": events,
        }
        (out / f"day-{day + 1:03d}.json").write_text(
            json.dumps(payload, indent=1), encoding="utf-8")
    return len(days)
