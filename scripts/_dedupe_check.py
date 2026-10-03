"""Verify the duplicate cohabitation lines on day 37 render merged + named."""
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\Administrator\Desktop\Github Repos\miniville\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from miniville import db as mvdb
from miniville.events import describe, describe_many

conn = mvdb.connect(r"C:\Users\Administrator\Desktop\Github Repos\miniville\data\miniville.db")

rows = conn.execute(
    """SELECT * FROM events WHERE kind='life_event'
       AND json_extract(data,'$.tag')='cohabitation'
       ORDER BY id DESC LIMIT 10""").fetchall()
print("raw rows:")
for r in rows:
    print("  ", describe(conn, r))
print("\ndeduped:")
for t in describe_many(conn, rows):
    print("  ", t)

print("\ndays with cohabitation events:")
for r in conn.execute(
        """SELECT day, COUNT(*) n FROM events
           WHERE kind='life_event' AND json_extract(data,'$.tag')='cohabitation'
           GROUP BY day ORDER BY n DESC LIMIT 5""").fetchall():
    print("  ", dict(r))

print("\nday-38 cohabitations (post-guard):")
rows = conn.execute(
    """SELECT * FROM events WHERE kind='life_event' AND day=38
       AND json_extract(data,'$.tag')='cohabitation'""").fetchall()
for r in rows:
    print("  ", describe(conn, r))
conn.close()
