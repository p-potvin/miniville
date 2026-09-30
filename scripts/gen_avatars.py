r"""Avatar manifest generator / backfill applier.

--manifest FILE : write JSON list of residents needing portraits
--apply DIR     : set agents.avatar_path from PNGs named a{id}.png in DIR

Generation itself runs on the GPU host (see docs/AVATARS.md).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from miniville import db  # noqa: E402


def manifest(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, sex, age, occupation, persona FROM agents
           WHERE alive=1 AND avatar_path IS NULL ORDER BY id""").fetchall()
    return [dict(r) for r in rows]


def apply_dir(conn, d: Path) -> int:
    n = 0
    for png in d.glob("a*.png"):
        aid = int(png.stem[1:])
        cur = conn.execute("UPDATE agents SET avatar_path=? WHERE id=?",
                           (f"assets/avatars/{png.name}", aid))
        n += cur.rowcount
    conn.commit()
    return n


if __name__ == "__main__":
    conn = db.connect()
    if "--manifest" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--manifest") + 1])
        rows = manifest(conn)
        out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"manifest: {len(rows)} residents -> {out}")
    elif "--apply" in sys.argv:
        d = Path(sys.argv[sys.argv.index("--apply") + 1])
        print(f"applied {apply_dir(conn, d)} avatar paths")
    else:
        print(__doc__)
