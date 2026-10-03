"""Copy the live DB, inject a fire + festival + scheduled closure, run days."""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

src = Path(r"C:\Users\Administrator\Desktop\Github Repos\miniville\data\miniville.db")
tmp = Path(tempfile.mkdtemp()) / "shock_smoke.db"

s = sqlite3.connect(src)
d = sqlite3.connect(tmp)
s.backup(d)
s.close()
d.close()

from miniville import db as mvdb, engine, shocks

conn = mvdb.connect(tmp)
seed = mvdb.get_meta(conn, "seed", "miniville")
tick = int(mvdb.get_meta(conn, "tick", "0"))
day = tick // 48
print(f"copied world: tick={tick} day={day}")

cols = {r["name"] for r in conn.execute("PRAGMA table_info(businesses)")}
print("reopen_day column:", "reopen_day" in cols)
print("shocks table:", bool(conn.execute(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shocks'").fetchone()))

# fire the diner today
print("\n-- inject fire @ Riverside Diner --")
print(shocks.inject(conn, "fire", "Riverside Diner", seed)["message"])
# festival the day after tomorrow
print("\n-- inject festival @ Lush Meadow Park, day+2 --")
print(shocks.inject(conn, "festival", "Lush Meadow Park", seed, day=day + 2)["message"])
# scheduled closure at the tavern in 3 days
print("\n-- inject closure @ Quaint Corner Tavern, day+3, days=10 --")
print(shocks.inject(conn, "closure", "Quaint Corner Tavern", seed,
                    day=day + 3, days=10)["message"])

print("\n-- shocks table --")
for r in shocks.list_shocks(conn):
    print(" ", r["id"], r["kind"], r["venue"], "day", r["day"], "applied", r["applied"])

diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
staff_lost = conn.execute(
    "SELECT COUNT(*) n FROM jobs WHERE place_id=?", (diner,)).fetchone()["n"]
biz = conn.execute("SELECT * FROM businesses WHERE place_id=?", (diner,)).fetchone()
print(f"\ndiner: status={biz['status']} reopen_day={biz['reopen_day']} staff_now={staff_lost}")
hurt = conn.execute("SELECT COUNT(*) n FROM conditions WHERE kind='sick'").fetchone()["n"]
print(f"sick/injured residents: {hurt}")

# run 4 days
for i in range(4):
    engine.run(conn, 48, seed, verbose=True)
tick = int(mvdb.get_meta(conn, "tick", "0"))
print(f"\nafter run: tick={tick} day={tick // 48}")

biz = conn.execute("SELECT * FROM businesses WHERE place_id=?", (diner,)).fetchone()
print(f"diner now: status={biz['status']} reopen_day={biz['reopen_day']}")
tav = conn.execute("SELECT id FROM places WHERE name='The Quaint Corner Tavern'").fetchone()["id"]
tb = conn.execute("SELECT * FROM businesses WHERE place_id=?", (tav,)).fetchone()
print(f"tavern now: status={tb['status']} reopen_day={tb['reopen_day']}")

fest_events = conn.execute(
    "SELECT tick, data FROM events WHERE kind='town_event' AND data LIKE '%festival%' "
    "ORDER BY id").fetchall()
print("\nfestival/fire events:")
import json
for e in fest_events:
    print("  tick", e["tick"], json.loads(e["data"]).get("tag"), "-",
          json.loads(e["data"]).get("text", "")[:100])

crowd = conn.execute(
    """SELECT COUNT(*) n FROM events WHERE kind='encounter'""").fetchone()["n"]
print(f"\ntotal encounters so far: {crowd}")

sh = conn.execute("SELECT id,kind,day,applied FROM shocks ORDER BY id").fetchall()
print("shocks:", [dict(r) for r in sh])
conn.close()
print("\nsmoke db:", tmp)
