r"""Quick DB inspection for debugging/verification.
Usage: .venv\Scripts\python.exe scripts\inspect_db.py
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from miniville.db import connect  # noqa: E402


def main() -> None:
    conn = connect()
    print("== event kinds ==")
    for r in conn.execute("SELECT kind, COUNT(*) c FROM events GROUP BY kind ORDER BY c DESC"):
        print(f"  {r['kind']}: {r['c']}")
    print("== life events (top 25 by importance) ==")
    for r in conn.execute(
            "SELECT data FROM events WHERE kind='life_event' "
            "ORDER BY importance DESC, id LIMIT 25"):
        d = json.loads(r["data"])
        print(f"  [{d.get('tag','?'):<12}] {d.get('text','')}")
    print("== relationship labels ==")
    for r in conn.execute("SELECT label, COUNT(*) c FROM relationships GROUP BY label ORDER BY c DESC"):
        print(f"  {r['label']}: {r['c']}")
    print("== gossip sample ==")
    for r in conn.execute("SELECT data FROM events WHERE kind='gossip' ORDER BY id LIMIT 5"):
        print(f"  {json.loads(r['data']).get('summary','')[:100]}")
    print("== current deviation activities ==")
    for r in conn.execute(
            "SELECT activity, COUNT(*) c FROM agent_state "
            "WHERE activity IN ('social_call','wallow','resting') GROUP BY activity"):
        print(f"  {r['activity']}: {r['c']}")
    print("== moods ==")
    for r in conn.execute("SELECT mood, COUNT(*) c FROM agent_state GROUP BY mood ORDER BY c DESC"):
        print(f"  {r['mood']}: {r['c']}")


if __name__ == "__main__":
    main()
