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
    conn = dbmod.connect(args.db)
    dbmod.init_db(conn)
    stats = populate(conn, args.dataset, args.agents, seed=args.seed)
    dbmod.set_meta(conn, "seed", args.seed)
    dbmod.set_meta(conn, "tick", "0")
    from . import individual
    from . import ambitions
    from . import civic_projects
    from . import mentorship
    from . import neighborhood
    economy.ensure_businesses(conn)
    individual.ensure_profiles(conn, args.seed)
    mentorship.ensure_traditions(conn, args.seed)
    neighborhood.ensure_districts(conn)
    civic_projects.ensure_projects(conn)
    ambitions.assign_ambitions(conn, 0, args.seed)
    conn.commit()
    # a town comes with its congregations and clubs already in it
    from .groups import assign_faith, form_groups, refresh_standing
    faith = assign_faith(conn)
    founded = form_groups(conn, 0, args.seed)
    refresh_standing(conn)
    mentorship.pair_mentors(conn, 0, args.seed)
    conn.commit()
    print(f"Miniville populated: {stats}")
    print(f"  faith parsed from personas: {faith['named']} named, "
          f"{faith['inherited']} children inherited")
    print(f"  affiliations founded: {len(founded['founded'])}")
    return 0


def cmd_economy(args) -> int:
    from . import economy
    conn = dbmod.connect(args.db)
    from . import individual
    from . import ambitions
    economy.ensure_businesses(conn)
    individual.ensure_profiles(conn, args.seed)
    ambitions.assign_ambitions(conn, 0, args.seed)
    conn.commit()
    s = economy.economy_stats(conn)
    print(f"money supply:    ${s['money_supply_cents'] / 100:,.0f}")
    print(f"median wallet:   ${s['median_balance_cents'] / 100:,.0f}   "
          f"mean ${s['mean_balance_cents'] / 100:,.0f}")
    print(f"residents in debt: {s['in_debt']}")
    print(f"town purse:      ${s['town_purse_cents'] / 100:,.0f}   "
          f"(public payroll ${s['public_payroll_week_cents'] / 100:,.0f}/week)")
    print(f"unemployment:    {s['unemployment'] * 100:.1f}%")
    print(f"wage index:      {s['wage_index'] * 100:.0f}% of baseline")
    print(f"businesses:      {s['businesses_open']} open, "
          f"{s['businesses_closed']} closed")
    print()
    print(f"{'business':<30} {'status':<7} {'balance':>12} {'rev/day':>10} "
          f"{'pay/day':>10} {'px':>5}  owner")
    for r in conn.execute(
            """SELECT p.name, p.kind, b.status, b.balance_cents, b.revenue_total,
                      b.payroll_total, b.price_index, b.last_settled_day,
                      b.revenue_today, b.payroll_today, o.name owner,
                      b.founded_tick
               FROM businesses b JOIN places p ON p.id=b.place_id
               LEFT JOIN agents o ON o.id=b.owner_id
               ORDER BY b.balance_cents"""):
        owner = (r["owner"] or "-") + (" (founded)" if r["founded_tick"] is not None else "")
        print(f"{r['name']:<30} {r['status']:<7} "
              f"${r['balance_cents'] / 100:>11,.0f} "
              f"${r['revenue_today'] / 100:>9,.0f} ${r['payroll_today'] / 100:>9,.0f} "
              f"{r['price_index']:>5.2f}  {owner}")
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
    conn = dbmod.connect(args.db)
    seed = dbmod.get_meta(conn, "seed", "miniville")
    assert seed is not None
    run(conn, args.ticks, seed, verbose=True)
    print(f"done. tick={dbmod.get_meta(conn,'tick')}")
    return 0


def cmd_status(args) -> int:
    conn = dbmod.connect(args.db)
    from .seasons import fmt_date, holiday_for, season_of
    from .timekeeper import day_of
    from .timekeeper import fmt_tick
    tick = int(dbmod.get_meta(conn, "tick", "0") or 0)
    print(f"time: {fmt_tick(tick)} (tick {tick})")
    day = day_of(tick)
    print(f"date: {fmt_date(day)} ({season_of(day)})")
    holiday = holiday_for(conn, day)
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
    # The ledger should never hold events dated after the clock. A probe that
    # wrote into the live DB (see docs/STATE.md, Fri 09 Oct 2026) or a restore
    # that rolled the clock back leaves rows from the future; say so loudly
    # rather than letting them show up as future-dated rows in the observer UI.
    future = conn.execute("SELECT COUNT(*) c FROM events WHERE tick > ?",
                          (tick,)).fetchone()["c"]
    if future:
        newest = conn.execute("SELECT MAX(tick) FROM events").fetchone()[0]
        print(f"  !! ledger/clock mismatch: {future} events dated after the clock "
              f"(now tick {tick}, newest tick {newest})")
    return 0


def cmd_inspect(args) -> int:
    conn = dbmod.connect(args.db)
    a = conn.execute(
        "SELECT * FROM agents WHERE name LIKE ? OR id=?", (f"%{args.who}%", args.who if str(args.who).isdigit() else -1)
    ).fetchone()
    if not a:
        print("no such agent")
        return 1
    st = conn.execute("SELECT * FROM agent_state WHERE agent_id=?", (a["id"],)).fetchone()
    print(f"{a['name']} — {a['age']} {a['sex']}, {a['occupation']}")
    from .individual import resident_summary
    ind = resident_summary(conn, a["id"])
    print(f"  from {a['origin_city']}, {a['origin_state']}; status={a['marital_status']}")
    print(f"  temperament: {ind['archetype']} (chronotype={ind['chronotype']}, knack={ind['knack']}"
          + (f", favorite={ind['favorite_place']}" if ind.get("favorite_place") else "") + ")")
    if ind.get("ambition"):
        am = ind["ambition"]
        print(f"  ambition: {am['label']} ({am['status']}, {am['progress']*100:.0f}%)")
    if ind.get("household_tradition"):
        print(f"  household tradition: {ind['household_tradition']}")
    if ind.get("mentor"):
        m = ind["mentor"]
        print(f"  mentor: {m['name']} in {m['craft']} ({m['status']}, {m['lessons']}/{m['target_lessons']} lessons)")
    if ind.get("apprentices"):
        apps = ", ".join(f"{ap['name']} ({ap['craft']}, {ap['status']})" for ap in ind["apprentices"])
        print(f"  apprentices: {apps}")
    if ind.get("keepsakes"):
        ks = ", ".join(f"{k['title']} (by {k['creator_name']}, gen {k['generations']})" for k in ind["keepsakes"])
        print(f"  keepsakes: {ks}")
    if ind.get("ribbons"):
        rbs = ", ".join(f"{rb['title']} (Day {rb['day']})" for rb in ind["ribbons"])
        print(f"  blue ribbons: {rbs}")
    if ind.get("diary"):
        print(f"  latest diary: {ind['diary'][0]['entry']}")
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




def cmd_fate(args) -> int:
    from . import individual
    conn = dbmod.connect(args.db)
    seed = dbmod.get_meta(conn, "seed", "miniville") or "miniville"
    out = individual.inject_fate(conn, args.who, args.kind, seed=seed)
    print(out["message"])
    return 0 if out["ok"] else 1


def cmd_traits(args) -> int:
    from . import individual
    conn = dbmod.connect(args.db)
    stats = individual.town_individualism_stats(conn)
    print("chronotypes:", stats["chronotypes"])
    print("top archetypes:", stats["top_archetypes"])
    if stats["favorite_places"]:
        print("favorite third places:")
        for fp in stats["favorite_places"]:
            print(f"  {fp['venue']:<28} {fp['regulars']} regulars")
    if stats["personal_event_counts"]:
        print("personal happenings on record:", stats["personal_event_counts"])
    return 0


def cmd_monologue(args) -> int:
    from .narrator import narrate_resident
    conn = dbmod.connect(args.db)
    a = conn.execute(
        "SELECT id FROM agents WHERE name LIKE ? OR id=?",
        (f"%{args.who}%", args.who if str(args.who).isdigit() else -1),
    ).fetchone()
    if not a:
        print("no such agent")
        return 1
    print(narrate_resident(conn, a["id"], provider=args.provider))
    return 0




def cmd_projects(args) -> int:
    from .civic_projects import list_projects
    conn = dbmod.connect(args.db)
    rows = list_projects(conn)
    print(f"{'project':<40} {'district':<18} {'status':<10} {'funded':>13} {'hours':>7}")
    for r in rows:
        fstr = f"${r['funded_cents']/100:,.0f}/${r['target_cents']/100:,.0f}"
        hstr = f"{r['volunteer_hours']}/{r['target_hours']}"
        print(f"  {r['title'][:38]:<38} {r['district']:<18} {r['status']:<10} {fstr:>13} {hstr:>7}")
    return 0


def cmd_health(args) -> int:
    from .health import health_stats
    conn = dbmod.connect(args.db)
    s = health_stats(conn)
    print(f"average vitality:    {s['avg_vitality']:.1f} / 100")
    print(f"high vitality (>=75): {s['high_vitality']} residents   low (<45): {s['low_vitality']} residents")
    print(f"currently sick:      {s['currently_sick']} residents")
    print(f"hospital clinicians: {s['hospital_clinicians']} on staff")
    print(f"medical rounds:      {s['treatments_total']} treatments, {s['checkups_total']} preventive checkups")
    if s["vital_seniors"]:
        print("most vital seniors:")
        for sr in s["vital_seniors"]:
            print(f"  {sr['name']:<24} age {sr['age']:<3} vitality {sr['vitality']:.1f}")
    return 0


def cmd_diary(args) -> int:
    from .correspondence import diary_of, list_diaries
    conn = dbmod.connect(args.db)
    if args.who:
        a = conn.execute(
            "SELECT id, name FROM agents WHERE name LIKE ? OR id=?",
            (f"%{args.who}%", args.who if str(args.who).isdigit() else -1),
        ).fetchone()
        if not a:
            print("no such agent")
            return 1
        rows = diary_of(conn, int(a["id"]), limit=args.limit)
    else:
        rows = list_diaries(conn, limit=args.limit)
    if not rows:
        print("no diary entries recorded yet")
        return 0
    for r in rows:
        print(f"  [day {r['day']:>3}] {r['name']} ({r['kind']}): {r['entry']}")
    return 0


def cmd_competitions(args) -> int:
    from .competitions import list_competitions
    conn = dbmod.connect(args.db)
    rows = list_competitions(conn, limit=args.limit)
    if not rows:
        print("no town competitions held yet")
        return 0
    print(f"{'day':>4}  {'competition':<36} {'winner':<20} {'runner-up':<20} {'entrants':>8}")
    for r in rows:
        print(f"  {r['day']:>3}  {r['title'][:34]:<36} {r['winner_name'][:18]:<20} "
              f"{(r['runner_up_name'] or '-'):<20} {r['entrants']:>8}")
    return 0


def cmd_milestones(args) -> int:
    from .milestones import list_milestones
    conn = dbmod.connect(args.db)
    rows = list_milestones(conn, limit=args.limit)
    if not rows:
        print("no personal milestones recorded yet")
        return 0
    for r in rows:
        print(f"  [day {r['day']:>3}] {r['text']}")
    return 0


def cmd_culture(args) -> int:
    from .culture import list_cultural_commons
    conn = dbmod.connect(args.db)
    rows = list_cultural_commons(conn, limit=args.limit)
    if not rows:
        print("no cultural works exhibited yet")
        return 0
    print(f"{'title':<36} {'kind':<11} {'creator':<18} {'venue':<26} {'admirers':>8}")
    for r in rows:
        print(f"  {r['title'][:34]:<34} {r['kind'][:10]:<11} "
              f"{r['creator_name'][:16]:<18} {(r['venue_name'] or '-'):<26} {r['appreciations']:>8}")
    return 0


def cmd_keepsakes(args) -> int:
    from .keepsakes import list_keepsakes
    conn = dbmod.connect(args.db)
    rows = list_keepsakes(conn, heirlooms_only=args.heirlooms, limit=args.limit)
    if not rows:
        print("no keepsakes on record yet")
        return 0
    print(f"{'title':<38} {'kind':<11} {'creator':<20} {'owner':<20} {'gen':>3}")
    for r in rows:
        print(f"  {r['title'][:36]:<36} {r['kind'][:10]:<11} "
              f"{r['creator_name'][:18]:<20} {r['owner_name'][:18]:<20} {r['generations']:>3}")
    return 0


def cmd_neighborhoods(args) -> int:
    from .neighborhood import neighborhood_stats
    conn = dbmod.connect(args.db)
    rows = neighborhood_stats(conn)
    print(f"{'district':<20} {'residents':>9} {'cohesion':>9} {'vigilance':>10}  last activity")
    for r in rows:
        last = f"{r['last_event_kind']} (day {r['last_event_day'] + 1})" if r['last_event_kind'] else "-"
        print(f"  {r['district']:<18} {r['residents']:>9} "
              f"{r['cohesion']*100:>8.0f}% {r['vigilance']*100:>9.0f}%  {last}")
    return 0


def cmd_mentorships(args) -> int:
    from .mentorship import list_mentorships, tradition_stats
    conn = dbmod.connect(args.db)
    rows = list_mentorships(conn, status=args.status, limit=args.limit)
    if not rows:
        print("no mentorships matching filter")
    else:
        print(f"{'mentor':<22} {'apprentice':<22} {'craft':<20} {'status':<10} {'lessons':>7}")
        for r in rows:
            prog = f"{r['lessons']}/{r['target_lessons']}"
            print(f"  {r['mentor_name'][:20]:<20} {r['apprentice_name'][:20]:<20} "
                  f"{r['craft'][:18]:<18} {r['status']:<10} {prog:>7}")
    trads = tradition_stats(conn)[:6]
    if trads:
        print("\nhousehold traditions:")
        for t in trads:
            print(f"  {t['tradition']:<38} {t['households']} households ({t['residents']} residents)")
    return 0


def cmd_ambitions(args) -> int:
    from .ambitions import list_ambitions
    conn = dbmod.connect(args.db)
    rows = list_ambitions(conn, status=args.status, limit=args.limit)
    if not rows:
        print("no ambitions matching filter")
        return 0
    print(f"{'resident':<24} {'ambition':<38} {'status':<10} {'progress':>8}")
    for r in rows:
        print(f"  {r['name'][:22]:<22} {r['label'][:36]:<36} {r['status']:<10} {r['progress']*100:7.0f}%")
    return 0




def cmd_pets(args) -> int:
    from .pets import pet_summary
    conn = dbmod.connect(args.db)
    summary = pet_summary(conn)
    print(f"=== Household Pets ({summary['total_pets']} total: {summary['dogs']} dogs, {summary['cats']} cats) ===")
    print(f"  park dog-walk encounters: {summary['pet_encounters']} · lost-pet reunions: {summary['reunions']}")
    for p in summary["pets"][: args.limit]:
        print(f"  {p['name']:12s} ({p['breed']} {p['species']}) · {p['household_name']} ({p['owner_name']}) — {p['quirk']}")
    return 0


def cmd_market(args) -> int:
    from .flea_market import market_summary
    conn = dbmod.connect(args.db)
    summary = market_summary(conn)
    print(f"=== Weekend Flea Market ({summary['total_trades']} sales · ${summary['total_volume_cents']/100:,.0f} volume) ===")
    for t in summary["recent_trades"][: args.limit]:
        print(f"  Day {t['day']+1:3d}: {t['buyer_name']} bought {t['item_title']} from {t['seller_name']} (${t['price_cents']/100:,.0f})")
    return 0


def cmd_youth(args) -> int:
    from .youth import youth_summary
    conn = dbmod.connect(args.db)
    summary = youth_summary(conn)
    print(f"=== Youth Discovery & Scholarships ({summary['discoveries']} discoveries · {summary['scholarships']} scholarships) ===")
    for item in summary["recent"][: args.limit]:
        print(f"  Day {item['day']:3d} [{item['tag']}]: {item['name']} — {item['text']}")
    return 0


def cmd_history(args) -> int:
    from .history import history_summary
    conn = dbmod.connect(args.db)
    summary = history_summary(conn)
    print("=== Miniville Historical Eras ===")
    for e in summary["eras"]:
        print(f"  {e['name']} [{e['theme']}]: {e['summary']}")
    print("\n=== Miniville Hall of Fame ===")
    for h in summary["hall_of_fame"]:
        print(f"  {h['category']:24s}: {h['name']} ({h['metric']} · {h['detail']})")
    return 0


def cmd_radio(args) -> int:
    from .radio import radio_summary
    conn = dbmod.connect(args.db)
    summary = radio_summary(conn)
    print(f"=== WMNV 94.3 FM Community Radio ({summary['total_broadcasts']} broadcasts: {summary['oral_histories']} oral histories, {summary['live_music_sessions']} live sessions) ===")
    for b in summary["recent"][: args.limit]:
        print(f"  Day {b['day']+1:3d} [{b['show_kind']}]: {b['title']} — {b['transcript']}")
    return 0


def cmd_guilds(args) -> int:
    from .guilds import guilds_summary
    conn = dbmod.connect(args.db)
    summary = guilds_summary(conn)
    print(f"=== Miniville Craft Guilds ({summary['total_guilds']} guilds · {summary['total_members']} members · {summary['total_masterpieces']} masterpieces · ${summary['total_patronage_cents']/100:,.0f} grants) ===")
    for g in summary["guilds"]:
        print(f"  {g['name']:36s} ({g['craft']}) · {g['members_count']:2d} members · Master: {g['master_name'] or 'none'} · {g['masterpieces_count']} masterpieces")
    return 0


def cmd_harvest(args) -> int:
    from .harvest import harvest_summary
    conn = dbmod.connect(args.db)
    summary = harvest_summary(conn)
    print(f"=== Seasonal Harvest Co-op ({summary['boxes_sold']} boxes sold [${summary['total_volume_cents']/100:,.0f}] · {summary['baskets_shared']} surplus baskets shared) ===")
    for h in summary["recent"][: args.limit]:
        print(f"  Day {h['day']+1:3d} [{h['season']} {h['kind']}]: {h['grower_name']} -> {h['recipient_name']}: {h['produce_item']} (${h['price_cents']/100:,.0f})")
    return 0


def cmd_sports(args) -> int:
    from .sports import sports_summary
    conn = dbmod.connect(args.db)
    summary = sports_summary(conn)
    print(f"=== Inter-District Sports League ({summary['total_matches']} Saturday derbies) ===")
    for s in summary["standings"]:
        print(f"  {s['team']:24s} ({s['district']:10s}): {s['wins']}W - {s['losses']}L (PF {s['points_for']})")
    if summary["recent_matches"]:
        print("\n=== Recent Saturday Derbies ===")
        for m in summary["recent_matches"][: args.limit]:
            print(f"  Day {m['day']+1:3d}: {m['summary']}")
    return 0




def cmd_brigade(args) -> int:
    from .fire_brigade import brigade_summary
    conn = dbmod.connect(args.db)
    summary = brigade_summary(conn)
    print(f"=== Volunteer Fire Brigade ({summary['total_volunteers']} volunteers · {summary['captains']} captains · {summary['total_drills']} drills · {summary['total_rebuilds']} rebuilds) ===")
    for v in summary["roster"][: args.limit]:
        print(f"  {v['name']:22s} [{v['role']:9s} · {v['district']:12s}] vitality {v['vitality']} · {v['drills_completed']} drills · {v['rescues_count']} rebuilds")
    return 0


def cmd_bookclub(args) -> int:
    from .book_club import book_club_summary
    conn = dbmod.connect(args.db)
    summary = book_club_summary(conn)
    print(f"=== Miniville Literary Circle ({summary['total_selections']} selections · ${summary['total_royalties_cents']/100:,.0f} local author royalties) ===")
    for b in summary["selections"][: args.limit]:
        auth = b["author_name"] or "Traditional"
        print(f"  Day {b['day']+1:3d} [{b['season']}]: '{b['title']}' by {auth} ({b['readers_count']} readers · ${b['royalty_cents']/100:,.0f} royalties)")
    return 0


def cmd_renovations(args) -> int:
    from .renovations import renovations_summary
    conn = dbmod.connect(args.db)
    summary = renovations_summary(conn)
    print(f"=== Home Renovations & Porch Additions ({summary['total_renovations']} upgrades · ${summary['total_spent_cents']/100:,.0f} paid to local builders) ===")
    for r in summary["recent"][: args.limit]:
        print(f"  Day {r['day']+1:3d}: {r['household_name']} ({r['district']}) hired {r['builder_name']} for {r['title']} (${r['cost_cents']/100:,.0f})")
    return 0


def cmd_jubilee(args) -> int:
    from .jubilee import jubilee_summary
    conn = dbmod.connect(args.db)
    summary = jubilee_summary(conn)
    print(f"=== Founders' Jubilee & Time Capsules ({summary['total_capsules']} capsules sealed) ===")
    for c in summary["capsules"]:
        print(f"  Day {c['day']+1:3d}: {c['theme']} · Key to Miniville: {c['honouree_name']} (${c['honorarium_cents']/100:,.0f}) · Items: {', '.join(c['items'])}")
    return 0




def cmd_theater(args) -> int:
    from .theater import theater_summary
    conn = dbmod.connect(args.db)
    summary = theater_summary(conn)
    print(f"=== Miniville Players Theater ({summary['total_productions']} productions · ${summary['total_box_office_cents']/100:,.0f} box office) ===")
    for p in summary["productions"][: args.limit]:
        print(f"  Day {p['day']+1:3d} [{p['season']}]: '{p['title']}' by {p['playwright_name']} (starring {p['lead_actor_name']}, {p['audience_count']} attendees, ${p['box_office_cents']/100:,.0f})")
    return 0


def cmd_toollibrary(args) -> int:
    from .commons_library import commons_library_summary
    conn = dbmod.connect(args.db)
    summary = commons_library_summary(conn)
    print(f"=== Tool Library, Seed Bank & Orchard ({summary['total_loans']} loans/gleanings · ${summary['total_endowment_cents']/100:,.0f} patron endowments) ===")
    for t in summary["recent"][: args.limit]:
        print(f"  Day {t['day']+1:3d} [{t['item_kind']}]: {t['borrower_name']} borrowed {t['item_name']} from steward {t['steward_name']} (${t['endowment_cents']/100:,.0f} endowment)")
    return 0


def cmd_plaques(args) -> int:
    from .plaques import plaques_summary
    conn = dbmod.connect(args.db)
    summary = plaques_summary(conn)
    print(f"=== Municipal Historical Plaques & Walking Tours ({summary['total_plaques']} plaques · ${summary['total_tips_cents']/100:,.0f} docent tips) ===")
    for hp in summary["plaques"][: args.limit]:
        print(f"  Day {hp['day']+1:3d}: {hp['title']} — {hp['inscription']}")
    return 0


def cmd_sabbaticals(args) -> int:
    from .sabbaticals import sabbaticals_summary
    conn = dbmod.connect(args.db)
    summary = sabbaticals_summary(conn)
    print(f"=== Resident Sabbaticals, Memoirs & Masterclasses ({summary['total_sabbaticals']} sessions · ${summary['total_stipends_cents']/100:,.0f} Lyceum stipends) ===")
    for sb in summary["recent"][: args.limit]:
        print(f"  Day {sb['day']+1:3d} [{sb['kind']}]: {sb['title']} at {sb['venue_name'] or 'Library'} ({sb['attendees_count']} attendees · ${sb['stipend_cents']/100:,.0f})")
    return 0


def cmd_sync(args) -> int:
    """Print structured inter-agent claims and operator priority queue."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    sync_file = repo_root / "docs" / "AGENT_SYNC.md"
    if not sync_file.is_file():
        print("no docs/AGENT_SYNC.md found")
        return 1
    text = sync_file.read_text(encoding="utf-8")
    for section in ("## Operator Priority Queue", "## Claims"):
        if section in text:
            block = text.split(section, 1)[1].split("\n## ", 1)[0].strip()
            print(section); print(block); print()
    return 0


def cmd_chronicle(args) -> int:
    conn = dbmod.connect(args.db)
    row = conn.execute(
        "SELECT * FROM chronicle WHERE day=?", (args.day - 1,)).fetchone()
    if row:
        print(row["text"])
    else:
        print(f"no chronicle for day {args.day}")
    return 0


def cmd_digest(args) -> int:
    from .chronicle import day_digest
    conn = dbmod.connect(args.db)
    print(day_digest(conn, args.day - 1, max_events=args.max))
    return 0


def cmd_narrate_write(args) -> int:
    from .chronicle import write_narrative
    conn = dbmod.connect(args.db)
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
    conn = dbmod.connect(args.db)
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
    conn = dbmod.connect(args.db)
    seed = get_meta(conn, "seed", "miniville")
    tick = int(get_meta(conn, "tick", "0") or 0)
    n = immigrate(conn, args.n, tick, seed, dataset_dir=args.dataset)
    total = conn.execute("SELECT COUNT(*) c FROM agents").fetchone()["c"]
    print(f"{n} newcomer(s) arrived; population now {total}")
    return 0


def cmd_year_in_review(args) -> int:
    from .db import get_meta
    from .newspaper import year_in_review
    conn = dbmod.connect(args.db)
    day = int(get_meta(conn, "tick", "0") or 0) // 48
    year = args.year or max(1, day // 365 + (1 if day % 365 else 0))
    print(year_in_review(conn, year))
    return 0


def cmd_influence(args) -> int:
    from .conflict import influence_of, most_influential
    conn = dbmod.connect(args.db)
    rows = most_influential(conn, args.top)
    if not rows:
        print("nobody has any influence yet")
        return 0
    print(f"{'resident':24s} {'influence':>9s}  {'standing':>8s}  {'rank':>4s}  "
          f"{'seat':>4s}  {'leads':>5s}")
    for r in rows:
        aid = r["id"]
        job = conn.execute(
            "SELECT rank FROM jobs WHERE agent_id=?", (aid,)).fetchone()
        seat = conn.execute("SELECT COUNT(*) n FROM council WHERE agent_id=?",
                            (aid,)).fetchone()["n"]
        leads = conn.execute(
            """SELECT COUNT(*) n FROM memberships WHERE agent_id=? AND role='officer'""",
            (aid,)).fetchone()["n"]
        standing = conn.execute("SELECT standing FROM agents WHERE id=?",
                                (aid,)).fetchone()["standing"]
        print(f"  {r['name'][:22]:22s} {r['influence']:9.2f} {standing or 0:8d}  "
              f"{job['rank'] if job else 0:4d}  {seat:4d}  {leads:5d}")
    return 0


def cmd_council(args) -> int:
    from .db import get_meta
    from .politics import POLICIES, council, next_election_day, policy
    conn = dbmod.connect(args.db)
    day = int(get_meta(conn, "tick", "0") or 0) // 48
    seats = council(conn)
    if not seats:
        print("no council seated yet — run `election`")
    else:
        print(f"{'seat':4s} {'councillor':22s} {'district':18s} {'votes':>6s} "
              f"{'wallet':>10s} {'out of work':>11s}")
        for s in seats:
            print(f"  {s['seat']:<2d} {s['name'] or '?':22s} {s['district'] or '?':18s} "
                  f"{s['backers']:6d} {s['backers_wallet'] / 100:10,.0f} "
                  f"{s['backers_unemployed'] * 100:10.0f}%")
    med = conn.execute(
        """SELECT s.money_cents m FROM agent_state s JOIN agents a ON a.id=s.agent_id
           WHERE a.alive=1 AND a.is_child=0 ORDER BY s.money_cents""").fetchall()
    if med:
        print(f"\nadult median wallet ${med[len(med) // 2]['m'] / 100:,.0f} "
              f"— a district below it wants the wage floor raised, a district "
              f"more out of work than average wants rent and the levy raised")
    print(f"next election: day {next_election_day(conn)} (now day {day})")
    print(f"{'policy':18s} {'now':>8s} {'default':>8s}   range")
    for name, (default, low, high, _step) in sorted(POLICIES.items()):
        print(f"  {name:16s} {policy(conn, name):8.3f} {default:8.3f}   "
              f"{low} - {high}")
    return 0


def cmd_election(args) -> int:
    from .db import get_meta
    from .politics import hold_election
    conn = dbmod.connect(args.db)
    seed = get_meta(conn, "seed", "miniville")
    tick = int(get_meta(conn, "tick", "0") or 0)
    out = hold_election(conn, tick, seed)
    conn.commit()
    if not out["seated"]:
        print("nobody stood for office")
        return 0
    print(f"turnout {out['turnout']}")
    for s in out["seated"]:
        print(f"  seat {s['seat']}: {s['name']} — {s['votes']} votes "
              f"({s['district'] or 'no fixed address'})")
    return 0


def cmd_motion(args) -> int:
    from .db import get_meta
    from .politics import consider_motion
    conn = dbmod.connect(args.db)
    seed = get_meta(conn, "seed", "miniville")
    tick = int(get_meta(conn, "tick", "0") or 0)
    out = consider_motion(conn, tick, seed)
    conn.commit()
    if not out:
        print("no council seated, or the policy is already at its rail")
        return 0
    verdict = "passed" if out["passed"] else "rejected"
    print(f"{out['policy']} {'+' if out['direction'] > 0 else '-'}"
          f"{out['value']:.3f} — {verdict} ({out['for']} for, {out['against']} against)")
    return 0


def cmd_form_groups(args) -> int:
    from .db import get_meta
    from .groups import assign_faith, form_groups, refresh_standing, roster
    conn = dbmod.connect(args.db)
    seed = get_meta(conn, "seed", "miniville")
    tick = int(get_meta(conn, "tick", "0") or 0)
    faith = assign_faith(conn)
    print(f"faith parsed from personas: {faith['named']} residents "
          f"({faith['inherited']} children inherited)")
    out = form_groups(conn, tick, seed)
    refresh_standing(conn)
    conn.commit()
    for g in out["founded"]:
        print(f"  founded {g['name']} ({g['kind']}, {g['members']} members)")
    if not out["founded"]:
        print("  nothing new to found")
    return cmd_groups(args)


def cmd_groups(args) -> int:
    from .groups import roster
    conn = dbmod.connect(args.db)
    rows = roster(conn)
    if not rows:
        print("no groups yet — run `form-groups`")
        return 0
    print(f"\n{'group':32s} {'kind':13s} {'members':>7s} {'standing':>8s}  meets")
    for r in rows:
        when = ""
        if r["meets_day"] is not None and r["meets_tick"] is not None:
            day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][r["meets_day"]]
            when = f"{day} {r['meets_tick'] // 2:02d}:{(r['meets_tick'] % 2) * 30:02d} " \
                   f"at {r['venue']}"
        print(f"  {r['name'][:30]:30s} {r['kind']:13s} {r['members']:7d} "
              f"{r['standing']:+8d}  {when}")
    return 0


def cmd_rebalance_jobs(args) -> int:
    from .db import get_meta
    from .jobs import fix_minor_flags, rebalance, venue_targets
    conn = dbmod.connect(args.db)
    seed = get_meta(conn, "seed", "miniville")
    tick = int(get_meta(conn, "tick", "0") or 0)
    minors = fix_minor_flags(conn)
    if minors:
        print(f"corrected {minors} under-18 resident(s) flagged as adults")
    before = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    out = rebalance(conn, tick, seed)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    print(f"jobs {before} -> {after}   released {out['released']}, "
          f"rehired {out['rehired']}, still unemployed {out['still_unemployed']}")
    print("\nvenue                     target  staff")
    targets = venue_targets(conn)
    staff = {r["place_id"]: r["n"] for r in conn.execute(
        "SELECT place_id, COUNT(*) n FROM jobs GROUP BY place_id")}
    for row in conn.execute(
            "SELECT id, name FROM places WHERE kind != 'home' ORDER BY id"):
        print(f"  {row['name'][:24]:24s} {targets.get(row['id'], 0):6d} "
              f"{staff.get(row['id'], 0):6d}")
    return 0


def cmd_newspaper(args) -> int:
    from .db import get_meta
    from .newspaper import latest, publish_week, week_of
    conn = dbmod.connect(args.db)
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
    conn = dbmod.connect(args.db)
    tick = int(get_meta(conn, "tick", "0") or 0)
    n = reflect_all(conn, tick // 48, limit=args.limit)
    print(f"{n} resident(s) reflected")
    return 0


def cmd_backup(args) -> int:
    from .backup import backup_db, export_days
    conn = dbmod.connect(args.db)
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
    conn = dbmod.connect(args.db)
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
    conn = dbmod.connect(args.db)
    seed = dbmod.get_meta(conn, "seed", "miniville")
    out = shocks.inject(conn, args.kind, args.venue, seed or "miniville",
                        day=None if args.day is None else args.day - 1,
                        days=args.days)
    print(out["message"])
    return 0 if out["ok"] else 1


def cmd_shocks(args) -> int:
    from . import shocks
    from .seasons import fmt_date
    conn = dbmod.connect(args.db)
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
    serve(args.db, args.host, args.port, getattr(args, "live_step", 0.0))
    return 0


def cmd_tv(args) -> int:
    from . import tv
    conn = dbmod.connect(args.db)
    snap = tv.tv_director_snapshot(conn, max_movers=args.movers)
    sky = snap["sky"]
    v = snap["vitality"]
    print(f"=== Miniville 10-Foot TV Director Feed ({snap['date']} · {snap['time']} · {sky['label']}) ===")
    print(f"Pop {v['population']} ({v['awake']} awake, {v['employed']} employed) · Treasury ${v['treasury_dollars']:,} · Active Movers: {v['active_movers']}")
    sr = snap.get("spotlight_resident")
    if sr:
        amb_str = f" · Ambition: {sr['ambition']['label']} ({sr['ambition']['progress_pct']}%)" if sr.get("ambition") else ""
        pet_str = f" · Pet: {sr['pet']}" if sr.get("pet") else ""
        print(f"Spotlight Resident: {sr['name']} ({sr['archetype']}, {sr['occupation']}) @ {sr['place_name']} [{sr['activity']}]{amb_str}{pet_str}")
    sv = snap.get("spotlight_venue")
    if sv:
        print(f"Spotlight Venue:    {sv['name']} ({sv['district']}) — {sv['inside']} inside")
    print("Featured Movers:")
    for m in snap["movers"][:8]:
        print(f"  → {m['name']} ({m['activity']}): {m['from_label']} ➔ {m['to_label']}")
    return 0


def cmd_benchmark(args) -> int:
    """Clone the live DB to a temp file and time N days of ticks."""
    import shutil
    import tempfile
    import time
    from . import engine
    src = Path(args.db) if args.db else dbmod.DEFAULT_DB
    tmp = Path(tempfile.mkdtemp()) / "bench.db"
    dbmod.snapshot_to(src, tmp)           # WAL-safe: copy2 can tear a live db
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
    pft = sub.add_parser("fate", help="trigger a personal event on an individual resident")
    pft.add_argument("who", help="resident name (substring) or id")
    pft.add_argument("kind", choices=["breakthrough", "mishap", "triumph", "repair", "care", "epiphany"])
    pft.set_defaults(fn=cmd_fate)
    ptr = sub.add_parser("traits", help="summary of town chronotypes, archetypes, and personal events")
    ptr.set_defaults(fn=cmd_traits)
    pam = sub.add_parser("ambitions", help="list residents' active or completed personal ambitions")
    pam.add_argument("--status", choices=["active", "completed", "all"], default="active")
    pam.add_argument("--limit", type=int, default=30)
    pam.set_defaults(fn=cmd_ambitions)
    pmt = sub.add_parser("mentorships", help="list intergenerational mentorships and household traditions")
    pmt.add_argument("--status", choices=["active", "graduated", "all"], default="all")
    pmt.add_argument("--limit", type=int, default=30)
    pmt.set_defaults(fn=cmd_mentorships)
    pks = sub.add_parser("keepsakes", help="list craftsman creations, keepsakes, and family heirlooms")
    pks.add_argument("--heirlooms", action="store_true", help="only show multi-generational heirlooms")
    pks.add_argument("--limit", type=int, default=30)
    pks.set_defaults(fn=cmd_keepsakes)
    pnb = sub.add_parser("neighborhoods", help="list district cohesion, watch vigilance, and neighborhood events")
    pnb.set_defaults(fn=cmd_neighborhoods)
    pml = sub.add_parser("milestones", help="list personal life milestones, anniversaries, and retirement send-offs")
    pml.add_argument("--limit", type=int, default=30)
    pml.set_defaults(fn=cmd_milestones)
    pcu = sub.add_parser("culture", help="list works exhibited in the Town Cultural Commons (library, gallery, stage)")
    pcu.add_argument("--limit", type=int, default=30)
    pcu.set_defaults(fn=cmd_culture)
    pdr = sub.add_parser("diary", help="read residents' personal diaries and hometown correspondence")
    pdr.add_argument("who", nargs="?", default=None, help="optional resident name or id")
    pdr.add_argument("--limit", type=int, default=25)
    pdr.set_defaults(fn=cmd_diary)
    pcp = sub.add_parser("competitions", help="list seasonal town competitions and Blue-Ribbon champions")
    pcp.add_argument("--limit", type=int, default=25)
    pcp.set_defaults(fn=cmd_competitions)
    ppj = sub.add_parser("projects", help="list civic improvement projects, funding, and volunteer hours")
    ppj.set_defaults(fn=cmd_projects)
    phl = sub.add_parser("health", help="town public health, athletic vitality, and hospital rounds summary")
    phl.set_defaults(fn=cmd_health)
    ppets = sub.add_parser("pets", help="show household pet companions and park encounters")
    ppets.add_argument("--limit", type=int, default=20)
    ppets.set_defaults(fn=cmd_pets)

    pmkt = sub.add_parser("market", help="show weekend flea-market and yard-sale trades")
    pmkt.add_argument("--limit", type=int, default=20)
    pmkt.set_defaults(fn=cmd_market)

    pyth = sub.add_parser("youth", help="show school youth discoveries and civic scholarships")
    pyth.add_argument("--limit", type=int, default=20)
    pyth.set_defaults(fn=cmd_youth)

    phist = sub.add_parser("history", help="show town historical eras and Hall of Fame")
    phist.set_defaults(fn=cmd_history)

    prad = sub.add_parser("radio", help="show WMNV 94.3 FM radio broadcasts and oral histories")
    prad.add_argument("--limit", type=int, default=20)
    prad.set_defaults(fn=cmd_radio)

    pgld = sub.add_parser("guilds", help="show chartered craft guilds and masterpiece grants")
    pgld.set_defaults(fn=cmd_guilds)

    phar = sub.add_parser("harvest", help="show seasonal harvest co-op sales and shared baskets")
    phar.add_argument("--limit", type=int, default=20)
    phar.set_defaults(fn=cmd_harvest)

    pspt = sub.add_parser("sports", help="show inter-district sports standings and Saturday derbies")
    pspt.add_argument("--limit", type=int, default=15)
    pspt.set_defaults(fn=cmd_sports)

    pbrg = sub.add_parser("brigade", help="show Volunteer Fire Brigade roster, drills, and rebuilds")
    pbrg.add_argument("--limit", type=int, default=20)
    pbrg.set_defaults(fn=cmd_brigade)

    pbkc = sub.add_parser("bookclub", help="show Town Book Club selections and author signings")
    pbkc.add_argument("--limit", type=int, default=20)
    pbkc.set_defaults(fn=cmd_bookclub)

    pren = sub.add_parser("renovations", help="show household porch additions, studios, and garden upgrades")
    pren.add_argument("--limit", type=int, default=20)
    pren.set_defaults(fn=cmd_renovations)

    pjub = sub.add_parser("jubilee", help="show Founders' Jubilee honourees and sealed Time Capsules")
    pjub.set_defaults(fn=cmd_jubilee)

    ptht = sub.add_parser("theater", help="show Town Theater Troupe playbills and box-office premieres")
    ptht.add_argument("--limit", type=int, default=20)
    ptht.set_defaults(fn=cmd_theater)

    ptlb = sub.add_parser("toollibrary", help="show Tool Library, Seed Bank swaps, and Community Orchard gleaning")
    ptlb.add_argument("--limit", type=int, default=20)
    ptlb.set_defaults(fn=cmd_toollibrary)

    pplq = sub.add_parser("plaques", help="show dedicated Municipal Historical Plaques and walking tours")
    pplq.add_argument("--limit", type=int, default=20)
    pplq.set_defaults(fn=cmd_plaques)

    psab = sub.add_parser("sabbaticals", help="show Resident Memoirs and Town Lyceum Masterclasses")
    psab.add_argument("--limit", type=int, default=20)
    psab.set_defaults(fn=cmd_sabbaticals)

    psy = sub.add_parser("sync", help="print agent claims and operator priority queue from AGENT_SYNC.md")
    psy.set_defaults(fn=cmd_sync)
    pmn = sub.add_parser("monologue", help="single-resident inner monologue (bounded 1-call LLM or raw)")
    pmn.add_argument("who", help="resident name (substring) or id")
    pmn.add_argument("--provider", choices=["vw", "hf", "ollama", "raw"], default="vw")
    pmn.set_defaults(fn=cmd_monologue)
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
    pv.add_argument("--live-step", type=float, default=0.0, help="Advance 1 tick every N seconds in background for TV mode")
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
    pjb = sub.add_parser("rebalance-jobs",
                         help="move surplus jobs onto the venues that need them")
    pjb.set_defaults(fn=cmd_rebalance_jobs)
    pgf = sub.add_parser("form-groups",
                         help="found the town's congregations and clubs")
    pgf.set_defaults(fn=cmd_form_groups)
    pgl = sub.add_parser("groups", help="list the town's affiliations")
    pgl.set_defaults(fn=cmd_groups)
    pco = sub.add_parser("council", help="seats, policies and the next election")
    pco.set_defaults(fn=cmd_council)
    pel = sub.add_parser("election", help="hold a town election now")
    pel.set_defaults(fn=cmd_election)
    pmo = sub.add_parser("motion", help="put a motion to the council now")
    pmo.set_defaults(fn=cmd_motion)
    pyr = sub.add_parser("year-in-review",
                         help="a year of the town, read back from its ledger")
    pyr.add_argument("--year", type=int, default=None,
                     help="calendar year (default: the one just ended)")
    pyr.set_defaults(fn=cmd_year_in_review)
    pin = sub.add_parser("influence", help="who actually runs this town")
    pin.add_argument("--top", type=int, default=12)
    pin.set_defaults(fn=cmd_influence)
    ptv = sub.add_parser("tv", help="Inspect the 10-foot TV Director snapshot")
    ptv.add_argument("--movers", type=int, default=10)
    ptv.set_defaults(fn=cmd_tv)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
