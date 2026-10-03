import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
conn = sqlite3.connect(r"C:\Users\Administrator\Desktop\Github Repos\miniville\data\miniville.db")
conn.row_factory = sqlite3.Row

tick = int(conn.execute("SELECT value FROM meta WHERE key='tick'").fetchone()["value"])
print("tick", tick, "day(0-based)", tick // 48, "tod", tick % 48)

park = conn.execute("SELECT id FROM places WHERE name='Lush Meadow Park'").fetchone()["id"]
occ = conn.execute(
    """SELECT s.activity, COUNT(*) n FROM agent_state s
       JOIN agents a ON a.id=s.agent_id
       WHERE s.place_id=? AND a.alive=1 GROUP BY s.activity""", (park,)).fetchall()
print("Lush Meadow Park now:", [dict(r) for r in occ])

diner = conn.execute("SELECT id FROM places WHERE name='Riverside Diner'").fetchone()["id"]
biz = conn.execute("SELECT status, reopen_day FROM businesses WHERE place_id=?", (diner,)).fetchone()
print("diner:", dict(biz))
hurt = conn.execute(
    "SELECT a.name, c.until_tick FROM conditions c JOIN agents a ON a.id=c.agent_id").fetchall()
print("sick/injured:", [dict(r) for r in hurt])

ev = conn.execute(
    "SELECT tick, data FROM events WHERE kind='town_event' AND tick>=1776 ORDER BY id").fetchall()
for e in ev:
    d = json.loads(e["data"])
    print("  t%d" % e["tick"], d.get("tag"), "-", d.get("text", "")[:110])
inj = conn.execute(
    "SELECT a.name, e.data FROM events e JOIN agents a ON a.id=e.a_id "
    "WHERE e.tick>=1776 AND e.data LIKE '%injured%'").fetchall()
for e in inj:
    print("  injured:", e["name"])
