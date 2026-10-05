r"""Deep-time soak: run the town for N simulated days on a scratch copy and
record a daily demographic/economic snapshot.

Writes `soak-<tag>.csv` (one row per simulated day) and prints the trajectory
so a long run can be judged while it is still going. The source database is
copied first, so the live world is never touched.

    .\.venv\Scripts\python.exe scripts\soak.py --days 730 --tag live2y
    .\.venv\Scripts\python.exe scripts\soak.py --days 3650 --mortality-scale 8 \
        --tag generations          # accelerate deaths to watch turnover

Env: MINIVILLE_DB selects the source (default data/miniville.db).
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from miniville import db as dbmod          # noqa: E402
from miniville import economy, engine      # noqa: E402

OUT_DIR = REPO / "data"


def snapshot(conn: sqlite3.Connection, day: int) -> dict:
    q = conn.execute
    alive = q("SELECT COUNT(*) n FROM agents WHERE alive=1").fetchone()["n"]
    kids = q("SELECT COUNT(*) n FROM agents WHERE alive=1 AND is_child=1").fetchone()["n"]
    seniors = q("SELECT COUNT(*) n FROM agents WHERE alive=1 AND age>=65").fetchone()["n"]
    adults = alive - kids
    dead = q("SELECT COUNT(*) n FROM agents WHERE alive=0").fetchone()["n"]
    homes = q("SELECT COUNT(*) n FROM households").fetchone()["n"]
    rel = dict(q("SELECT label, COUNT(*) n FROM relationships GROUP BY label").fetchall())
    openb = q("SELECT COUNT(*) n FROM businesses WHERE status='open'").fetchone()["n"]
    closedb = q("SELECT COUNT(*) n FROM businesses WHERE status='closed'").fetchone()["n"]
    jobs = q("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
    events = q("SELECT COUNT(*) n FROM events").fetchone()["n"]
    money = q("SELECT COALESCE(SUM(s.money_cents),0) n FROM agent_state s "
              "JOIN agents a ON a.id=s.agent_id WHERE a.alive=1").fetchone()["n"]
    reserves = q("SELECT COALESCE(SUM(balance_cents),0) n FROM businesses").fetchone()["n"]
    working_age = q(
        "SELECT COUNT(*) n FROM agents WHERE alive=1 AND is_child=0 AND age<65"
    ).fetchone()["n"]
    retired = q(
        "SELECT COUNT(*) n FROM agents WHERE alive=1 AND occupation='Retired'"
    ).fetchone()["n"]
    return {
        "day": day, "alive": alive, "adults": adults, "children": kids,
        "seniors": seniors, "working_age": working_age, "retired": retired,
        "dead": dead, "households": homes, "jobs": jobs,
        "spouses": rel.get("spouse", 0), "partners": rel.get("partner", 0),
        "sweethearts": rel.get("sweetheart", 0),
        "close_friends": rel.get("close_friend", 0),
        "estranged": rel.get("estranged", 0), "widowed": rel.get("widowed", 0),
        "businesses_open": openb, "businesses_closed": closedb,
        "unemployment": round(economy.unemployment(conn), 4),
        "wage_index": round(economy.wage_index(conn), 4),
        "money_supply": money, "business_reserves": reserves,
        "events": events,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--tag", default="soak")
    ap.add_argument("--source", default=None, help="db to copy (default: MINIVILLE_DB)")
    ap.add_argument("--mortality-scale", type=float, default=None,
                    help="MINIVILLE_MORTALITY_SCALE for this run")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.mortality_scale is not None:
        import os
        os.environ["MINIVILLE_MORTALITY_SCALE"] = str(a.mortality_scale)

    src = Path(a.source) if a.source else Path(
        __import__("os").environ.get("MINIVILLE_DB") or REPO / "data" / "miniville.db")
    if not src.is_file():
        print(f"no source db at {src}")
        return 1
    work = OUT_DIR / f"soak-{a.tag}.db"
    shutil.copy2(src, work)
    conn = dbmod.connect(work)
    seed = dbmod.get_meta(conn, "seed", "miniville")
    start_tick = int(dbmod.get_meta(conn, "tick", "0") or 0)
    start_day = start_tick // 48

    print(f"soak '{a.tag}': {a.days} days from tick {start_tick} (day {start_day}) "
          f"on {src.name}" +
          (f"  mortality x{a.mortality_scale}" if a.mortality_scale else ""))

    rows = []
    csv_path = OUT_DIR / f"soak-{a.tag}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = None
        t0 = time.perf_counter()
        for d in range(a.days):
            for _ in range(48):
                engine.step(conn, seed)
            if d % 5 == 0 or d == a.days - 1:
                row = snapshot(conn, start_day + d + 1)
                rows.append(row)
                if writer is None:
                    writer = csv.DictWriter(fh, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                fh.flush()
                if not a.quiet:
                    el = time.perf_counter() - t0
                    print(f"  day {row['day']:5d}  alive {row['alive']:5d} "
                          f"(+{row['children']:4d} kids, {row['seniors']:4d} 65+)  "
                          f"dead {row['dead']:4d}  homes {row['households']:4d}  "
                          f"spouse {row['spouses']:3d}  biz {row['businesses_open']:3d}/"
                          f"{row['businesses_closed']:2d}  unemp {row['unemployment']*100:5.1f}%  "
                          f"${row['money_supply']/100:,.0f}  [{el:.0f}s]")

    first, last = rows[0], rows[-1]
    print(f"\n=== {a.days} days in {time.perf_counter()-t0:.0f}s ===")
    for k in ("alive", "children", "seniors", "dead", "households", "jobs",
              "spouses", "partners", "close_friends", "widowed", "estranged",
              "businesses_open", "businesses_closed"):
        print(f"  {k:18s} {first[k]:6d} -> {last[k]:6d}")
    for k in ("unemployment", "wage_index"):
        print(f"  {k:18s} {first[k]:6.3f} -> {last[k]:6.3f}")
    for k in ("money_supply", "business_reserves"):
        print(f"  {k:18s} ${first[k]/100:,.0f} -> ${last[k]/100:,.0f}")
    print(f"csv -> {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
