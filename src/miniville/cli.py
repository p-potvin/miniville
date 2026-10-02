"""miniville CLI: init | run | status | inspect | chronicle | narrate."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import db as dbmod

# The Windows console defaults to cp1252, and a stray non-ASCII character in
# any command's output (the `·` separators, an em dash in a chronicle) makes
# the whole write fail — silently, in a piped shell. Force UTF-8 so no command
# can lose its output to a punctuation mark.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                   # noqa: BLE001
        pass


def _conn(args) -> sqlite3.Connection:
    conn = dbmod.connect(args.db)
    return conn


def cmd_init(args) -> int:
    from . import economy
    from .ingest import populate
    conn = _conn(args)
    dbmod.init_db(conn)
    stats = populate(conn, args.dataset, args.agents, seed=args.seed)
    dbmod.set_meta(conn, "seed", args.seed)
    dbmod.set_meta(conn, "tick", "0")
    economy.ensure_businesses(conn)
    conn.commit()
    print(f"Miniville populated: {stats}")
    return 0


def cmd_economy(args) -> int:
    from . import economy
    conn = _conn(args)
    economy.ensure_businesses(conn)
    conn.commit()
    s = economy.economy_stats(conn)
    print(f"money supply:    ${s['money_supply_cents'] / 100:,.0f}")
    print(f"median wallet:   ${s['median_balance_cents'] / 100:,.0f}   "
          f"mean ${s['mean_balance_cents'] / 100:,.0f}")
    print(f"residents in debt: {s['in_debt']}")
    print(f"unemployment:    {s['unemployment'] * 100:.1f}%")
    print(f"wage index:      {s['wage_index'] * 100:.0f}% of baseline")
    print(f"businesses:      {s['businesses_open']} open, "
          f"{s['businesses_closed']} closed")
    print()
    print(f"{'business':<30} {'status':<7} {'balance':>12} {'rev/day':>10} "
          f"{'pay/day':>10} {'px':>5}")
    for r in conn.execute(
            """SELECT p.name, p.kind, b.status, b.balance_cents, b.revenue_total,
                      b.payroll_total, b.price_index, b.last_settled_day,
                      b.revenue_today, b.payroll_today
               FROM businesses b JOIN places p ON p.id=b.place_id
               ORDER BY b.balance_cents"""):
        settled = r["last_settled_day"]
        print(f"{r['name']:<30} {r['status']:<7} "
              f"${r['balance_cents'] / 100:>11,.0f} "
              f"${r['revenue_today'] / 100:>9,.0f} ${r['payroll_today'] / 100:>9,.0f} "
              f"{r['price_index']:>5.2f}")
    if args.days:
        print()
        print(f"{'day':>4} {'revenue':>12} {'payroll':>12} {'rent':>10} "
              f"{'spending':>10} {'supply':>14} {'unemp':>7} {'closed':>7}")
        for r in conn.execute(
                "SELECT * FROM economy_days WHERE money_supply_cents > 0 "
                "ORDER BY day DESC LIMIT ?", (args.days,)):
            print(f"{r['day']:>4} ${r['revenue_cents'] / 100:>11,.0f} "
                  f"${r['payroll_cents'] / 100:>11,.0f} "
                  f"${r['rent_cents'] / 100:>9,.0f} "
                  f"${r['spending_cents'] / 100:>9,.0f} "
                  f"${r['money_supply_cents'] / 100:>13,.0f} "
                  f"{r['unemployment_bp'] / 100:>6.1f}% {r['businesses_closed']:>7}")
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
    from .seasons import fmt_date, holiday_on, season_of
    from .timekeeper import day_of
    from .timekeeper import fmt_tick
    tick = int(dbmod.get_meta(conn, "tick", "0") or 0)
    print(f"time: {fmt_tick(tick)} (tick {tick})")
    day = day_of(tick)
    print(f"date: {fmt_date(day)} ({season_of(day)})")
    holiday = holiday_on(day)
    if holiday:
        print(f"holiday: {holiday.name}")
    alive = conn.execute("SELECT COUNT(*) c FROM agents WHERE alive=1").fetchone()["c"]
    dead = conn.execute("SELECT COUNT(*) c FROM agents WHERE alive=0").fetchone()["c"]
    print(f"  residents: {alive} alive, {dead} deceased")
    for t in ("households", "places", "events", "relationships"):
        c = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        print(f"  {t}: {c}")
    moods = conn.execute(
        """SELECT s.mood, COUNT(*) c FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1 GROUP BY s.mood ORDER BY c DESC""").fetchall()
    print("  moods:", {m["mood"]: m["c"] for m in moods})
    labels = conn.execute(
        "SELECT label, COUNT(*) c FROM relationships GROUP BY label ORDER BY c DESC").fetchall()
    print("  relationships:", {l["label"]: l["c"] for l in labels})
    from . import economy
    print("  economy:", economy.economy_line(conn))
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
    from .memory import memory_digest
    print("  memories:")
    print(memory_digest(conn, a["id"]))
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
                           vw_model=args.vw_model,
                           max_calls=args.max)
    for l in lines:
        print(l)
        print()
    print(spend_report(conn))
    return 0


def cmd_immigrate(args) -> int:
    from .db import get_meta
    from .growth import immigrate
    conn = _conn(args)
    seed = get_meta(conn, "seed", "miniville")
    tick = int(get_meta(conn, "tick", "0") or 0)
    n = immigrate(conn, args.n, tick, seed, dataset_dir=args.dataset)
    total = conn.execute("SELECT COUNT(*) c FROM agents").fetchone()["c"]
    print(f"{n} newcomer(s) arrived; population now {total}")
    return 0


def cmd_newspaper(args) -> int:
    from .db import get_meta
    from .newspaper import latest, publish_week, week_of
    conn = _conn(args)
    if args.week is not None:
        text = publish_week(conn, args.week - 1, get_meta(conn, "seed", "miniville"))
    else:
        row = latest(conn)
        if not row:
            tick = int(get_meta(conn, "tick", "0") or 0)
            text = publish_week(conn, week_of(tick // 48),
                                get_meta(conn, "seed", "miniville"))
        else:
            text = row["text"]
    print(text)
    return 0


def cmd_reflect(args) -> int:
    from .db import get_meta
    from .memory import reflect_all
    conn = _conn(args)
    tick = int(get_meta(conn, "tick", "0") or 0)
    n = reflect_all(conn, tick // 48, limit=args.limit)
    print(f"{n} resident(s) reflected")
    return 0


def cmd_backup(args) -> int:
    from .backup import backup_db, export_days
    conn = _conn(args)
    dest = backup_db(conn)
    n = export_days(conn)
    print(f"snapshot: {dest} (+chronicle export of {n} day(s) to export/)")
    return 0


def cmd_daily(args) -> int:
    """Advance N days, digest each, narrate via provider chain, snapshot."""
    from . import engine
    from .backup import backup_db, export_days
    from .chronicle import write_narrative
    from .db import get_meta
    from .narrator import narrate_events, spend_report
    from .timekeeper import TICKS_PER_DAY
    conn = _conn(args)
    seed = get_meta(conn, "seed", "miniville")
    start_day = int(get_meta(conn, "tick", "0") or 0) // TICKS_PER_DAY
    for d in range(start_day, start_day + args.days):
        print(f"=== Day {d + 1} ===")
        engine.run(conn, TICKS_PER_DAY, seed, verbose=True)
        # leave a digest file so the next agent session can write prose first
        dig_dir = Path("digest")
        dig_dir.mkdir(exist_ok=True)
        from .chronicle import day_digest
        (dig_dir / f"day-{d + 1:03d}.md").write_text(
            day_digest(conn, d), encoding="utf-8")
        if not conn.execute(
                "SELECT 1 FROM narratives WHERE day=?", (d,)).fetchone():
            print(f"(day {d + 1} has no narrative — agent can digest "
                  f"digest/day-{d + 1:03d}.md and narrate-write)")
        if not args.skip_narrate:
            lines = narrate_events(conn, d, provider=args.provider,
                                   max_calls=args.max)
            for l in lines:
                print(l)
            if args.write and lines:
                prose = "\n".join(l for l in lines)
                write_narrative(conn, d, prose, source=f"narrate-{args.provider}")
                print(f"(narrative stored for day {d + 1})")
            print(spend_report(conn))
    snap = backup_db(conn)
    export_days(conn)
    print(f"backup: {snap}")
    return 0


def cmd_shock(args) -> int:
    from . import shocks
    conn = _conn(args)
    seed = dbmod.get_meta(conn, "seed", "miniville")
    out = shocks.inject(conn, args.kind, args.venue, seed or "miniville",
                        day=None if args.day is None else args.day - 1,
                        days=args.days)
    print(out["message"])
    return 0 if out["ok"] else 1


def cmd_shocks(args) -> int:
    from . import shocks
    from .seasons import fmt_date
    conn = _conn(args)
    rows = shocks.list_shocks(conn)
    if not rows:
        print("no shocks on record")
        return 0
    for r in rows:
        detail = json.loads(r["detail"] or "{}")
        reopen = (f"reopens day {detail['reopen_day'] + 1}"
                  if detail.get("reopen_day") is not None else "")
        state = "applied" if r["applied"] else "scheduled"
        print(f"#{r['id']:<3} {r['kind']:<8} {r['venue'] or '?':<28} "
              f"day {r['day'] + 1:<4} {fmt_date(r['day']):<14} {state:<9} {reopen}")
    return 0


def cmd_serve(args) -> int:
    from .ui.server import serve
    serve(args.db, args.host, args.port)
    return 0


def cmd_benchmark(args) -> int:
    """Clone the live DB to a temp file and time N days of ticks."""
    import shutil
    import tempfile
    import time
    from . import engine
    src = Path(args.db) if args.db else dbmod.DEFAULT_DB
    tmp = Path(tempfile.mkdtemp()) / "bench.db"
    shutil.copy2(src, tmp)
    conn = dbmod.connect(tmp)
    seed = dbmod.get_meta(conn, "seed", "miniville")
    times = []
    total = args.days * 48
    for _ in range(total):
        t0 = time.perf_counter()
        engine.step(conn, seed)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    n = len(times)
    print(f"benchmark: {total} ticks on {src.name}")
    print(f"  mean={sum(times)/n:.1f}ms  p50={times[n//2]:.1f}ms  "
          f"p99={times[int(n*0.99)]:.1f}ms  max={times[-1]:.1f}ms")
    shutil.rmtree(tmp.parent, ignore_errors=True)
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
    pn.add_argument("--provider", choices=["vw", "hf", "ollama", "raw"], default="vw",
                    help="vw = vault-inference gateway (preferred), "
                         "hf = Hugging Face Inference direct (budget-capped $1.50), "
                         "ollama = local, raw = no LLM")
    pn.add_argument("--hf-model", default="openai/gpt-oss-20b:deepinfra",
                    help="HF provider model, e.g. openai/gpt-oss-20b:deepinfra")
    pn.add_argument("--ollama-model", default="gemma4:e2b-it-qat")
    pn.add_argument("--vw-model", default="",
                    help="vault-inference model id (empty = gateway default)")
    pn.add_argument("--max", type=int, default=5)
    pn.set_defaults(fn=cmd_narrate)
    pb = sub.add_parser("backup"); pb.set_defaults(fn=cmd_backup)
    pdy = sub.add_parser("daily", help="advance days + narrate + snapshot")
    pdy.add_argument("--days", type=int, default=1)
    pdy.add_argument("--provider", choices=["vw", "hf", "ollama", "raw"], default="vw")
    pdy.add_argument("--max", type=int, default=5,
                     help="max narration calls per day")
    pdy.add_argument("--write", action="store_true",
                     help="store narrated blurbs into narratives table")
    pdy.add_argument("--skip-narrate", action="store_true")
    pdy.set_defaults(fn=cmd_daily)
    pv = sub.add_parser("serve", help="read-only observer UI")
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--port", type=int, default=8787)
    pv.set_defaults(fn=cmd_serve)
    pm = sub.add_parser("benchmark", help="time ticks on a temp copy of the DB")
    pm.add_argument("--days", type=int, default=1)
    pm.set_defaults(fn=cmd_benchmark)
    pg = sub.add_parser("immigrate", help="move unused dataset personas into town")
    pg.add_argument("--n", type=int, default=25)
    pg.add_argument("--dataset", default=r"E:\Nemotron-Personas-USA")
    pg.set_defaults(fn=cmd_immigrate)
    pnp = sub.add_parser("newspaper", help="print or publish a weekly Gazette edition")
    pnp.add_argument("--week", type=int, default=None,
                     help="1-based week to (re)publish; omit for the latest")
    pnp.set_defaults(fn=cmd_newspaper)
    prf = sub.add_parser("reflect", help="distill residents' memories into reflections")
    prf.add_argument("--limit", type=int, default=0)
    prf.set_defaults(fn=cmd_reflect)
    pec = sub.add_parser("economy", help="money supply, businesses, wages")
    pec.add_argument("--days", type=int, default=10,
                     help="also print the last N daily economy rows")
    pec.set_defaults(fn=cmd_economy)
    psk = sub.add_parser("shock", help="god mode: inject a closure | fire | festival")
    psk.add_argument("kind", choices=["closure", "fire", "festival"])
    psk.add_argument("venue", help="venue name (substring match) or place id")
    psk.add_argument("--day", type=int, default=None,
                     help="1-based day the shock lands (default: today for "
                          "closure/fire, tomorrow for festival)")
    psk.add_argument("--days", type=int, default=None,
                     help="days until the venue reopens (fire default 14; "
                          "closure default follows the market's 21-day rule)")
    psk.set_defaults(fn=cmd_shock)
    plk = sub.add_parser("shocks", help="list injected shocks")
    plk.set_defaults(fn=cmd_shocks)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
