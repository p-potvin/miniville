import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
conn = sqlite3.connect(r"C:\Users\Administrator\Desktop\Github Repos\miniville\data\miniville.db")
conn.row_factory = sqlite3.Row
for r in conn.execute(
        """SELECT district, kind, COUNT(*) n FROM places
           GROUP BY district, kind ORDER BY district, kind"""):
    print(dict(r))
print("\nvenues:")
for r in conn.execute(
        """SELECT name, kind, district, capacity FROM places
           WHERE kind != 'home' ORDER BY district, name"""):
    print(" ", dict(r))
print("\nagents per activity right now:")
for r in conn.execute(
        """SELECT s.activity, COUNT(*) n FROM agent_state s
           JOIN agents a ON a.id=s.agent_id WHERE a.alive=1
           GROUP BY s.activity ORDER BY n DESC"""):
    print(" ", dict(r))
