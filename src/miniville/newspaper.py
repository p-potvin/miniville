"""The Miniville Gazette — a weekly front page distilled from the chronicle.

The simulation already writes a per-day chronicle. This module rolls seven of
those days into a single newspaper edition: a headline drawn from the week's
most important event, then short sections (arrivals, weddings & births, work,
town life, and the odd scandal). Editions are stored so the observer UI can
browse the archive.

Prose is template-based so the paper never depends on an LLM; `polish()` can
optionally hand the draft to the vault-inference gateway for a rewrite.
"""
from __future__ import annotations

import sqlite3

from .db import get_meta
from .events import describe

DAYS_PER_WEEK = 7

# event tag -> (section title, verb phrase for the roundup)
SECTIONS = {
    "immigration": "Arrivals",
    "birth": "Weddings & Births",
    "marriage": "Weddings & Births",
    "cohabitation": "Weddings & Births",
    "hire": "Working Life",
    "fired": "Working Life",
    "sick": "Town Life",
    "move": "Town Life",
    "affair": "The Scandal Sheet",
    "betrayal": "The Scandal Sheet",
    "separation": "The Scandal Sheet",
    "favor": "Neighbors",
    "favor_repaid": "Neighbors",
    "work_buddy": "Working Life",
    "holiday": "Town Life",
    "festival": "Town Life",
    "festival_announced": "Town Life",
    "season": "Town Life",
    "coming_of_age": "Town Life",
    "injured": "Town Life",
    "shock_fire": "Town Life",
    "shock_closure": "Working Life",
    "business_closed": "Working Life",
    "business_reopened": "Working Life",
    # the paper used to be blind to everything the town gained later: no
    # council, no clubs, no feuds, no careers, no obituaries
    "hired": "Working Life",
    "quit": "Working Life",
    "retired": "Working Life",
    "promoted": "Working Life",
    "death": "Obituaries",
    "obituary": "Obituaries",
    "group_founded": "Clubs & Congregations",
    "schism": "Clubs & Congregations",
    "election": "The Town Council",
    "seat_vacated": "The Town Council",
    "motion_passed": "The Town Council",
    "motion_rejected": "The Town Council",
    "town_deficit": "The Town Council",
    "slander": "The Feud",
    "boycott": "The Feud",
    "dividend": "The Town Council",
    "wage_change": "Working Life",
    "rent_distress": "Town Life",
    "downsize": "Town Life",
}

# printed in this order when the week has anything for them
SECTION_ORDER = ("The Town Council", "Arrivals", "Weddings & Births",
                 "Working Life", "Clubs & Congregations", "The Feud",
                 "The Scandal Sheet", "Neighbors", "Obituaries", "Town Life")


def week_of(day: int) -> int:
    return day // DAYS_PER_WEEK


def _tag_of(row: sqlite3.Row) -> str:
    try:
        import json
        return (json.loads(row["data"] or "{}") or {}).get("tag", "")
    except Exception:
        return ""


def _headline(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "A quiet week in Miniville"
    top = max(rows, key=lambda r: (r["importance"], -r["id"]))
    return describe(conn, top).rstrip(".")


def publish_week(conn: sqlite3.Connection, week: int,
                 seed: str = "miniville") -> str:
    """Compose and store the front page for `week`. Returns the text."""
    start, end = week * DAYS_PER_WEEK, week * DAYS_PER_WEEK + DAYS_PER_WEEK - 1
    rows = conn.execute(
        "SELECT * FROM events WHERE day BETWEEN ? AND ? AND importance >= 2 "
        "ORDER BY importance DESC, id", (start, end)).fetchall()

    lines = [f"THE MINIVILLE GAZETTE — Week {week + 1}", ""]
    lines.append(f"**{_headline(conn, rows)}**")
    lines.append("")

    buckets: dict[str, list[str]] = {}
    for r in rows:
        tag = _tag_of(r)
        section = SECTIONS.get(tag)
        if not section:
            continue
        buckets.setdefault(section, []).append(describe(conn, r))

    for section in SECTION_ORDER:
        items = buckets.get(section)
        if not items:
            continue
        lines.append(f"{section}:")
        for it in items[:4]:
            lines.append(f"  - {it}")
        lines.append("")

    economy_block = _economy_block(conn, start, end)
    if economy_block:
        lines.extend(economy_block)

    pop = conn.execute(
        "SELECT COUNT(*) c FROM agents WHERE alive=1").fetchone()["c"]
    lines.append(f"— Population this week: {pop}. "
                 f"{len(rows)} notable events on record.")

    text = "\n".join(lines)
    conn.execute(
        "INSERT INTO newspapers(week,text,created_tick) VALUES(?,?,?) "
        "ON CONFLICT(week) DO UPDATE SET text=excluded.text, "
        "created_tick=excluded.created_tick",
        (week, text, int(get_meta(conn, "tick", "0") or 0)))
    conn.commit()
    return text


def _economy_block(conn: sqlite3.Connection, start: int, end: int) -> list[str]:
    """The week's money, jobs and businesses, for the back page."""
    row = conn.execute(
        """SELECT SUM(revenue_cents) r, SUM(payroll_cents) p, SUM(rent_cents) rent,
                  SUM(spending_cents) spend
           FROM economy_days WHERE day BETWEEN ? AND ?""", (start, end)).fetchone()
    if not row or not row["p"]:
        return []
    first = conn.execute(
        "SELECT money_supply_cents, unemployment_bp, businesses_closed FROM economy_days "
        "WHERE day >= ? AND money_supply_cents > 0 ORDER BY day LIMIT 1", (start,)).fetchone()
    last = conn.execute(
        "SELECT money_supply_cents, unemployment_bp, businesses_closed, wage_index "
        "FROM economy_days WHERE day <= ? AND money_supply_cents > 0 "
        "ORDER BY day DESC LIMIT 1", (end,)).fetchone()
    if not last:
        return []
    drift = last["money_supply_cents"] - (first["money_supply_cents"] if first else 0)
    lines = ["Economy:"]
    lines.append(f"  - Wages paid out this week: ${row['p'] / 100:,.0f}; "
                 f"shops and diners took in ${row['r'] / 100:,.0f}.")
    lines.append(f"  - Rent collected: ${(row['rent'] or 0) / 100:,.0f}; "
                 f"households spent ${(row['spend'] or 0) / 100:,.0f} on food and errands.")
    lines.append(f"  - Money in circulation ${last['money_supply_cents'] / 100:,.0f} "
                 f"({'+' if drift >= 0 else ''}{drift / 100:,.0f} this week); "
                 f"unemployment {last['unemployment_bp'] / 100:.1f}%.")
    lines.append(f"  - Wages stand at {last['wage_index'] * 100:.0f}% of the spring level; "
                 f"{last['businesses_closed']} business(es) dark.")
    lines.append("")
    return lines


def polish(text: str, model: str = "") -> str:
    """Optional LLM rewrite of a draft front page via vault-inference.

    Returns the original text unchanged when the gateway is unreachable, so the
    paper is always publishable.
    """
    from .narrator import _call_vw
    prompt = ("You are the editor of a small-town newspaper. Tighten this front "
              "page into crisp copy, keeping every fact and the section "
              "headings:\n\n" + text)
    try:
        out = _call_vw(prompt, model, max_tokens=900)
        return out or text
    except Exception:
        return text


def latest(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM newspapers ORDER BY week DESC LIMIT 1").fetchone()


# --- the year in review -------------------------------------------------------


def _count(conn: sqlite3.Connection, tag: str, start: int, end: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) n FROM events WHERE day BETWEEN ? AND ? AND data LIKE ?",
        (start, end, f'%"tag": "{tag}"%')).fetchone()["n"]


def year_in_review(conn: sqlite3.Connection, year: int) -> str:
    """A year of the town, read back from its own ledger.

    This is the point of keeping a ledger: not a list of events but the shape
    of a year — who came, who went, who governed, who fell out with whom, and
    what it all did to the numbers. Deterministic prose, no model involved, so
    it can be replayed and checked like everything else.
    """
    start, end = (year - 1) * 365, year * 365 - 1
    today = int(get_meta(conn, "tick", "0") or 0) // 48
    so_far = " (so far)" if today < end else ""
    lines = [f"MINIVILLE - the year {year}{so_far}", ""]

    born = _count(conn, "birth", start, end)
    died = _count(conn, "death", start, end)
    arrived = _count(conn, "immigration", start, end)
    married = _count(conn, "marriage", start, end)
    split = _count(conn, "separation", start, end) + _count(conn, "betrayal", start, end)
    came_of_age = _count(conn, "coming_of_age", start, end)
    pop = conn.execute("SELECT COUNT(*) n FROM agents WHERE alive=1").fetchone()["n"]
    lines.append(
        f"{pop} residents. {born} born, {died} died, {arrived} arrived from "
        f"elsewhere, {came_of_age} came of age. {married} married; {split} "
        f"marriages broke.")
    lines.append("")

    # who governed
    seats = conn.execute(
        """SELECT c.seat, c.district, a.name, c.backers FROM council c
           LEFT JOIN agents a ON a.id = c.agent_id ORDER BY c.seat""").fetchall()
    if seats:
        lines.append("THE COUNCIL")
        for s in seats:
            lines.append(f"  {s['district']}: {s['name'] or 'vacant'} "
                         f"({s['backers']} votes)")
        passed = _count(conn, "motion_passed", start, end)
        rejected = _count(conn, "motion_rejected", start, end)
        lines.append(f"  {passed} motions carried, {rejected} defeated.")
        from .politics import POLICIES, policy
        moved = [f"{name} {policy(conn, name):.2f}" for name in sorted(POLICIES)
                 if abs(policy(conn, name) - POLICIES[name][0]) > 1e-9]
        if moved:
            lines.append(f"  The numbers moved: {', '.join(moved)} "
                         f"(defaults {', '.join(f'{n} {POLICIES[n][0]:.2f}' for n in sorted(POLICIES) if abs(policy(conn, n) - POLICIES[n][0]) > 1e-9)}).")
        lines.append("")

    # who mattered
    from .conflict import most_influential
    top = most_influential(conn, 3)
    if top and top[0]["influence"] > 0:
        lines.append("THE INFLUENTIAL")
        for t in top:
            lines.append(f"  {t['name']} (influence {t['influence']:.1f})")
        lines.append("")

    # what the town did about its grudges
    slandered = _count(conn, "slander", start, end)
    boycotted = _count(conn, "boycott", start, end)
    schisms = _count(conn, "schism", start, end)
    rivals = conn.execute(
        "SELECT COUNT(*) n FROM relationships WHERE affinity <= -20").fetchone()["n"]
    if slandered or boycotted or schisms:
        lines.append("THE FEUDS")
        if slandered:
            lines.append(f"  Somebody was talked about {slandered} "
                         f"{'time' if slandered == 1 else 'times'}.")
        if boycotted:
            lines.append(f"  {'A group declared a boycott' if boycotted == 1 else f'{boycotted} boycotts were declared'}.")
        if schisms:
            lines.append(f"  A congregation split." if schisms == 1
                         else f"  {schisms} congregations split.")
        lines.append(f"  {rivals} rivalries stand unresolved.")
        lines.append("")

    # the year's business
    closed = _count(conn, "business_closed", start, end)
    opened = _count(conn, "business_reopened", start, end)
    from .economy import town_balance
    lines.append("THE TOWN'S BOOKS")
    lines.append(f"  {closed} business(es) failed, {opened} reopened. "
                 f"The town holds ${town_balance(conn) / 100:,.0f}.")
    lines.append("")

    # the year's biggest moments, in the town's own words
    rows = conn.execute(
        "SELECT * FROM events WHERE day BETWEEN ? AND ? AND importance >= 4 "
        "ORDER BY importance DESC, id", (start, end)).fetchall()
    # at most two of any one kind, so the list reads as a year rather than as
    # whichever event happened to be the most common
    seen: dict[str, int] = {}
    picked = []
    for r in rows:
        tag = _tag_of(r) or r["kind"]
        if tag in ("death", "obituary"):
            continue                      # they have their own section
        if seen.get(tag, 0) >= 2:
            continue
        seen[tag] = seen.get(tag, 0) + 1
        picked.append(r)
        if len(picked) >= 8:
            break
    if picked:
        lines.append("WHAT PEOPLE WILL REMEMBER")
        for r in picked:
            lines.append(f"  {describe(conn, r).rstrip('.')}.")
        lines.append("")

    # who did not see the year out
    gone = conn.execute(
        """SELECT e.data, a.name FROM events e
           LEFT JOIN agents a ON a.id = e.a_id
           WHERE e.day BETWEEN ? AND ? AND e.data LIKE '%"tag": "death"%'
           ORDER BY e.id LIMIT 6""", (start, end)).fetchall()
    if gone:
        lines.append("GONE")
        for g in gone:
            import json
            try:
                d = json.loads(g["data"])
            except ValueError:
                continue
            if d.get("text"):
                who = g["name"] or "someone"
                lines.append(f"  {who} {d['text']}.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
