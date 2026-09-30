"""miniville CLI: init | run | status | inspect | chronicle | narrate."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import db as dbmod


def _conn(args) -> sqlite3.Connection:
    conn = dbmod.connect(args.db)
    return conn


def cmd_init(args) -> int:
    from .ingest import populate
    conn = _conn(args)
    dbmod.init_db(conn)
    stats = populate(conn, args.dataset, args.agents, seed=args.seed)
    dbmod.set_meta(conn, "seed", args.seed)
    dbmod.set_meta(conn, "tick", "0")
    conn.commit()
    print(f"Miniville populated: {stats}")
    return 0


def cmd_run(args) -> int:
    from .engine import run
    conn = _conn(args)
    seed = dbmod.get_meta(conn, "seed", "miniville")
    assert seed is not None
    run(conn, args.ticks, seed, verbose=True)
    print(f"done. tick={dbmod.get_meta(conn,'tick')}")
    return 0


def cmd_status(args) -> int:
    conn = _conn(args)
    from .timekeeper import fmt_tick
    tick = int(dbmod.get_meta(conn, "tick", "0") or 0)
    print(f"time: {fmt_tick(tick)} (tick {tick})")
    for t in ("agents", "households", "places", "events", "relationships"):
        c = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        print(f"  {t}: {c}")
    moods = conn.execute(
        "SELECT mood, COUNT(*) c FROM agent_state GROUP BY mood ORDER BY c DESC").fetchall()
    print("  moods:", {m["mood"]: m["c"] for m in moods})
    labels = conn.execute(
        "SELECT label, COUNT(*) c FROM relationships GROUP BY label ORDER BY c DESC").fetchall()
    print("  relationships:", {l["label"]: l["c"] for l in labels})
    return 0


def cmd_inspect(args) -> int:
    conn = _conn(args)
    a = conn.execute(
        "SELECT * FROM agents WHERE name LIKE ? OR id=?", (f"%{args.who}%", args.who if str(args.who).isdigit() else -1)
    ).fetchone()
    if not a:
        print("no such agent")
        return 1
    st = conn.execute("SELECT * FROM agent_state WHERE agent_id=?", (a["id"],)).fetchone()
    print(f"{a['name']} — {a['age']} {a['sex']}, {a['occupation']}")
    print(f"  from {a['origin_city']}, {a['origin_state']}; status={a['marital_status']}")
    print(f"  persona: {(a['persona'] or '')[:240]}")
    print(f"  hobbies: {a['hobbies_json']}")
    print(f"  state: energy={st['energy']:.0f} hunger={st['hunger']:.0f} "
          f"social={st['social']:.0f} fun={st['fun']:.0f} stress={st['stress']:.0f} "
          f"mood={st['mood']} money=${st['money_cents']/100:.2f}")
    rels = conn.execute(
        """SELECT r.*, x.name FROM relationships r
           JOIN agents x ON x.id = (CASE WHEN r.a_id=? THEN r.b_id ELSE r.a_id END)
           WHERE r.a_id=? OR r.b_id=? ORDER BY r.familiarity DESC LIMIT 12""",
        (a["id"], a["id"], a["id"])).fetchall()
    for r in rels:
        print(f"  rel: {r['name']:<24} {r['label']:<14} fam={r['familiarity']:.0f} aff={r['affinity']:+.0f} rom={r['romance']:.0f}")
    evs = conn.execute(
        "SELECT * FROM events WHERE a_id=? OR b_id=? ORDER BY id DESC LIMIT 10",
        (a["id"], a["id"])).fetchall()
    from .events import describe
    for e in evs:
        print(f"  ev[{e['tick']}]: {describe(conn, e)}")
    return 0


def cmd_chronicle(args) -> int:
    conn = _conn(args)
    row = conn.execute(
        "SELECT * FROM chronicle WHERE day=?", (args.day - 1,)).fetchone()
    if row:
        print(row["text"])
    else:
        print(f"no chronicle for day {args.day}")
    return 0


def cmd_digest(args) -> int:
    from .chronicle import day_digest
    conn = _conn(args)
    print(day_digest(conn, args.day - 1, max_events=args.max))
    return 0


def cmd_narrate_write(args) -> int:
    from .chronicle import write_narrative
    conn = _conn(args)
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("empty narrative — nothing written")
        return 1
    write_narrative(conn, args.day - 1, text, source=args.source)
    print(f"narrative stored for day {args.day} (source={args.source}, {len(text)} chars)")
    return 0


def cmd_narrate(args) -> int:
    from .narrator import narrate_events, spend_report
    conn = _conn(args)
    lines = narrate_events(conn, args.day - 1, provider=args.provider,
                           hf_model=args.hf_model,
                           ollama_model=args.ollama_model,
                           max_calls=args.max)
    for l in lines:
        print(l)
        print()
    print(spend_report(conn))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="miniville")
    p.add_argument("--db", default=None, help="sqlite path (env MINIVILLE_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--dataset", default=r"E:\Nemotron-Personas-USA")
    pi.add_argument("--agents", type=int, default=500)
    pi.add_argument("--seed", default="miniville")
    pi.set_defaults(fn=cmd_init)

    pr = sub.add_parser("run")
    pr.add_argument("--ticks", type=int, default=48)
    pr.set_defaults(fn=cmd_run)

    ps = sub.add_parser("status"); ps.set_defaults(fn=cmd_status)
    pj = sub.add_parser("inspect"); pj.add_argument("who"); pj.set_defaults(fn=cmd_inspect)
    pc = sub.add_parser("chronicle"); pc.add_argument("day", type=int); pc.set_defaults(fn=cmd_chronicle)
    pd = sub.add_parser("digest")
    pd.add_argument("--day", type=int, required=True)
    pd.add_argument("--max", type=int, default=20)
    pd.set_defaults(fn=cmd_digest)
    pw = sub.add_parser("narrate-write")
    pw.add_argument("--day", type=int, required=True)
    pw.add_argument("--file", default=None, help="read prose from file instead of stdin")
    pw.add_argument("--source", default="agent")
    pw.set_defaults(fn=cmd_narrate_write)
    pn = sub.add_parser("narrate")
    pn.add_argument("--day", type=int, required=True)
    pn.add_argument("--provider", choices=["hf", "ollama", "raw"], default="hf",
                    help="hf = Hugging Face Inference (budget-capped $1.50), "
                         "ollama = local, raw = no LLM")
    pn.add_argument("--hf-model", default="openai/gpt-oss-20b:deepinfra",
                    help="HF provider model, e.g. openai/gpt-oss-20b:deepinfra")
    pn.add_argument("--ollama-model", default="gemma4:e2b-it-qat")
    pn.add_argument("--max", type=int, default=5)
    pn.set_defaults(fn=cmd_narrate)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
