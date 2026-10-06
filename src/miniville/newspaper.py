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

import json
import sqlite3

from .db import get_meta
from .events import NOTABLE, describe, emit
from .rng import rng_for

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
    "press_claim": "The Gazette's Own Account",
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

# A front page is an agenda, not a second event ledger. The owner/editor line
# changes which true events rise to the top and what gets space; it does not
# change the world's events. False claims are stored separately and can be
# checked against the observer record.
LINE_BONUS = {
    "working": {"fired": 5, "quit": 3, "rent_distress": 4, "downsize": 5,
                "motion_passed": 2, "boycott": 2, "promoted": -1},
    "business": {"business_reopened": 5, "business_closed": -2,
                 "motion_passed": 2, "dividend": -2, "rent_distress": -1,
                 "promoted": 2},
    "establishment": {"election": 5, "motion_passed": 4, "motion_rejected": 2,
                       "slander": -2, "boycott": -1},
    "community": {"group_founded": 4, "schism": 3, "favor": 3,
                   "favor_repaid": 2, "birth": 3, "death": 2},
}

POLICY_PREFERENCE = {
    "business": {"levy_rate": -1, "dividend_share": -1,
                 "rent_multiplier": -1, "min_wage": -1},
    "working": {"levy_rate": 1, "dividend_share": 1,
                "rent_multiplier": -1, "min_wage": 1},
    "establishment": {},
    "community": {},
}
FALSE_CLAIM_BASE = 0.18       # when a council result directly hurts the owner


def week_of(day: int) -> int:
    return day // DAYS_PER_WEEK


def _tag_of(row: sqlite3.Row) -> str:
    try:
        import json
        return (json.loads(row["data"] or "{}") or {}).get("tag", "")
    except Exception:
        return ""


def press_profile(conn: sqlite3.Connection, tick: int) -> dict:
    """The paper's resident publisher, editor, and their material interest.

    If either leaves the Gazette or dies, the paper promotes from its staff.
    The publisher's line is derived from their seat, wealth and work — not
    assigned a political identity by the simulator.
    """
    profile = conn.execute(
        """SELECT n.*, p.name publisher, e.name editor
           FROM newspaper_profile n
           LEFT JOIN agents p ON p.id=n.publisher_id
           LEFT JOIN agents e ON e.id=n.editor_id WHERE n.id=1""").fetchone()
    staff = conn.execute(
        """SELECT a.id, a.name, a.occupation, a.standing, a.education_level,
                  a.alive, j.rank, j.wage_cents
           FROM jobs j JOIN agents a ON a.id=j.agent_id
           JOIN places p ON p.id=j.place_id WHERE p.name='Miniville Gazette'
           AND a.alive=1 ORDER BY a.id""").fetchall()
    if not staff:
        staff = conn.execute(
            """SELECT id, name, occupation, standing, education_level, alive,
                      0 rank, 0 wage_cents FROM agents
               WHERE alive=1 AND is_child=0
               ORDER BY standing DESC, id LIMIT 12""").fetchall()

    live_ids = {x["id"] for x in staff}
    owner_id = profile["publisher_id"] if profile and profile["publisher_id"] in live_ids else None
    editor_id = profile["editor_id"] if profile and profile["editor_id"] in live_ids else None

    if owner_id is None or editor_id is None:
        from .conflict import influence_of
        ranked = sorted(staff, key=lambda x: (-influence_of(conn, x["id"]), x["id"]))
        if owner_id is None:
            owner_id = ranked[0]["id"]
        if editor_id is None:
            writers = [x for x in staff if any(
                term in (x["occupation"] or "").lower()
                for term in ("writer", "journal", "report"))]
            newsroom = writers or [x for x in staff if any(
                term in (x["occupation"] or "").lower()
                for term in ("editor", "media"))]
            editor_id = max(newsroom or staff,
                            key=lambda x: (x["standing"] or 0, x["rank"] or 0,
                                           -x["id"]))["id"]

        owner = conn.execute(
            "SELECT * FROM agents WHERE id=?", (owner_id,)).fetchone()
        median = conn.execute(
            """SELECT money_cents FROM agent_state s JOIN agents a ON a.id=s.agent_id
               WHERE a.alive=1 AND a.is_child=0 ORDER BY money_cents""").fetchall()
        med = median[len(median) // 2]["money_cents"] if median else 1
        seat = conn.execute("SELECT 1 FROM council WHERE agent_id=?",
                            (owner_id,)).fetchone()
        job = conn.execute("SELECT place_id FROM jobs WHERE agent_id=?",
                           (owner_id,)).fetchone()
        groups = conn.execute(
            "SELECT COUNT(*) n FROM memberships WHERE agent_id=? AND role='officer'",
            (owner_id,)).fetchone()["n"]
        wallet = conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=?",
                              (owner_id,)).fetchone()
        balance = wallet["money_cents"] if wallet else 0
        if seat:
            line, basis = "establishment", "the publisher holds a council seat"
        elif groups:
            line, basis = "community", "the publisher leads a town group"
        elif balance > med * 1.35:
            line, basis = "business", "the publisher's household is above the town median"
        elif job is None or balance < med * 0.85:
            line, basis = "working", "the publisher's household is below the town median"
        else:
            line, basis = "community", "no single interest dominates the publisher's ties"
        conn.execute(
            """INSERT INTO newspaper_profile(id,publisher_id,editor_id,founded_tick,
                   editorial_line,editorial_basis,credibility)
               VALUES(1,?,?,?,?,?,1.0)
               ON CONFLICT(id) DO UPDATE SET publisher_id=excluded.publisher_id,
                   editor_id=excluded.editor_id, editorial_line=excluded.editorial_line,
                   editorial_basis=excluded.editorial_basis""",
            (owner_id, editor_id, tick, line, basis))
        profile = conn.execute(
            """SELECT n.*, p.name publisher, e.name editor
               FROM newspaper_profile n
               LEFT JOIN agents p ON p.id=n.publisher_id
               LEFT JOIN agents e ON e.id=n.editor_id WHERE n.id=1""").fetchone()
        conn.commit()
    return dict(profile)


def _tag_data(row: sqlite3.Row) -> tuple[str, dict]:
    try:
        data = json.loads(row["data"] or "{}")
    except (TypeError, ValueError):
        data = {}
    return data.get("tag", ""), data


def editorial_weight(row: sqlite3.Row, line: str) -> float:
    """Agenda weight, not a change to what happened."""
    tag, data = _tag_data(row)
    weight = float(row["importance"])
    weight += LINE_BONUS.get(line, {}).get(tag, 0)
    preference = POLICY_PREFERENCE.get(line, {}).get(data.get("policy"))
    if preference and data.get("direction"):
        actual = data["direction"] if data.get("passed") else 0
        if actual == preference:
            weight += 3
        elif actual and actual != preference:
            weight -= 2
    return weight


def observer_record(conn: sqlite3.Connection, week: int) -> list[dict]:
    """The neutral ledger for the same week, independent of press coverage."""
    start, end = week * DAYS_PER_WEEK, week * DAYS_PER_WEEK + DAYS_PER_WEEK - 1
    rows = conn.execute("SELECT * FROM events WHERE day BETWEEN ? AND ? "
                        "ORDER BY day, tick, id", (start, end)).fetchall()
    return [{"day": r["day"] + 1, "tick": r["tick"], "kind": r["kind"],
             "importance": r["importance"], "text": describe(conn, r)} for r in rows]


def _false_claim(conn: sqlite3.Connection, row: sqlite3.Row, profile: dict,
                 week: int, seed: str) -> dict | None:
    """A reproducible editorial lie about a recorded council vote.

    The newspaper can misreport a vote when it went against its owner's line.
    The claim is stored alongside the actual source event and marked false for
    the observer; the Gazette itself prints it as true. This is where the
    observer can see both the false report and the receipt.
    """
    tag, data = _tag_data(row)
    line = profile["editorial_line"]
    if tag not in ("motion_passed", "motion_rejected") or line not in POLICY_PREFERENCE:
        return None
    preferred = POLICY_PREFERENCE[line].get(data.get("policy"))
    if not preferred:
        return None
    direction = int(data.get("direction", 0))
    passed = bool(data.get("passed"))
    # The owner wants `preferred` enacted. Only lie if the actual result
    # opposes that preference: claim the opposite outcome, not whatever
    # happens to sound favorable.
    if (passed and direction == preferred) or (not passed and direction != preferred):
        return None
    credibility = float(profile.get("credibility", 1.0))
    r = rng_for(seed, "gazette_claim", week, row["id"], profile["publisher_id"])
    chance = FALSE_CLAIM_BASE * max(0.0, min(1.0, credibility))
    if r.random() >= chance:
        return None
    policy_name = data.get("policy", "policy").replace("_", " ")
    direction = data.get("direction", 1)
    action = "raise" if direction > 0 else "lower"
    # The claim asserts the motion carried/failed opposite to the truth.
    outcome = "passed" if not passed else "was rejected"
    claim = (f"The council's motion to {action} {policy_name} {outcome}.")
    factual = describe(conn, row)
    return {"event_id": row["id"], "claim": claim, "factual_record": factual,
            "truth": False, "policy": data.get("policy"),
            "actual_passed": bool(data.get("passed"))}


def _headline(conn: sqlite3.Connection, rows: list[sqlite3.Row],
              profile: dict | None = None) -> str:
    if not rows:
        return "A quiet week in Miniville"
    profile = profile or {"editorial_line": "community"}
    top = max(rows, key=lambda r: (editorial_weight(r, profile["editorial_line"]), -r["id"]))
    return describe(conn, top).rstrip(".")


def publish_week(conn: sqlite3.Connection, week: int,
                 seed: str = "miniville") -> str:
    """A resident writes the Gazette; the ledger remains the observer's truth.

    The owner's line changes the agenda and can produce a provably false
    claim about a council vote. We preserve that claim verbatim in
    `newspaper_claims`; the event ledger is never edited. Observer clients can
    show both side by side.
    """
    start, end = week * DAYS_PER_WEEK, week * DAYS_PER_WEEK + DAYS_PER_WEEK - 1
    rows = conn.execute(
        "SELECT * FROM events WHERE day BETWEEN ? AND ? AND importance >= 2 "
        "ORDER BY importance DESC, id", (start, end)).fetchall()
    profile = press_profile(conn, int(get_meta(conn, "tick", "0") or 0))
    line = profile["editorial_line"]
    prior = conn.execute("SELECT credibility FROM newspapers WHERE week=?",
                         (week,)).fetchone()
    # Re-publishing the same edition is idempotent: use the credibility it
    # started with, and do not punish the publisher a second time.
    issue_credibility = float(prior["credibility"] if prior else profile.get("credibility", 1.0))
    issue_profile = {**profile, "credibility": issue_credibility}
    headline = _headline(conn, rows, issue_profile)

    lines = [f"THE MINIVILLE GAZETTE — Week {week + 1}",
             f"Owned by {profile['publisher'] or 'the Gazette staff'}; "
             f"by {profile['editor'] or 'the Gazette staff'}.",
             f"Editorial line: {line} — {profile['editorial_basis']}. "
             f"Credibility: {profile['credibility']:.0%}.", ""]
    lines.append(f"**{headline}**")
    lines.append("")

    buckets: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        tag = _tag_of(r)
        section = SECTIONS.get(tag)
        if section:
            buckets.setdefault(section, []).append(r)

    for section in SECTION_ORDER:
        items = buckets.get(section)
        if not items:
            continue
        items.sort(key=lambda e: (-editorial_weight(e, line), e["id"]))
        lines.append(f"{section}:")
        for event in items[:4]:
            lines.append(f"  - {describe(conn, event)}")
        lines.append("")

    # A newsroom can lie about a recorded vote. The paper prints the claim as
    # fact; the neutral record below remains untouched and the API returns the
    # claim with a verifiable pointer to the source event. Repeated falsehoods
    # damage the publication's credibility, which reduces future claim-making.
    claims = []
    for event in rows:
        claim = _false_claim(conn, event, issue_profile, week, seed)
        if claim:
            claims.append(claim)
    if claims:
        lines.append("The publisher's account:")
        for claim in claims:
            lines.append(f"  - {claim['claim']}")
        lines.append("")
        if prior is None:
            conn.execute("UPDATE newspaper_profile SET credibility=MAX(0, credibility-?) "
                         "WHERE id=1", (0.03 * len(claims),))

    economy_block = _economy_block(conn, start, end)
    if economy_block:
        lines.extend(economy_block)

    pop = conn.execute(
        "SELECT COUNT(*) c FROM agents WHERE alive=1").fetchone()["c"]
    lines.append(f"— Population this week: {pop}. "
                 f"{len(rows)} notable events on record.")

    text = "\n".join(lines)
    conn.execute(
        """INSERT INTO newspapers(week,text,created_tick,publisher_id,editor_id,
               editorial_line,editorial_basis,credibility,claims_json)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(week) DO UPDATE SET text=excluded.text,
               created_tick=excluded.created_tick,
               publisher_id=excluded.publisher_id, editor_id=excluded.editor_id,
               editorial_line=excluded.editorial_line,
               editorial_basis=excluded.editorial_basis,
               credibility=excluded.credibility, claims_json=excluded.claims_json""",
        (week, text, int(get_meta(conn, "tick", "0") or 0),
         profile["publisher_id"], profile["editor_id"], line,
         profile["editorial_basis"], issue_credibility, json.dumps(claims)))
    # The edition row now exists for the claim foreign key. Re-publishing a
    # week replaces its claims idempotently alongside the text.
    conn.execute("DELETE FROM newspaper_claims WHERE week=?", (week,))
    for claim in claims:
        conn.execute(
            """INSERT INTO newspaper_claims(week,event_id,publisher_id,claim,truth)
               VALUES(?,?,?,?,0)""",
            (week, claim["event_id"], profile["publisher_id"], claim["claim"]))
    if prior is None and claims:
        # the neutral event ledger records the act of publishing a claim which
        # conflicts with the world; it does not rewrite the source event
        emit(conn, int(get_meta(conn, "tick", "0") or 0), "town_event",
             a=profile["publisher_id"], importance=NOTABLE,
             text=f"the Gazette published a claim about a council vote which "
                  f"does not match the event ledger",
             tag="press_claim", week=week + 1, claim_count=len(claims))
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
