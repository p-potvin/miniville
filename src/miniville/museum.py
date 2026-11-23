"""Town Museum, Exhibits & Artifact Curation (v0.55).

Curates notable town artifacts and moments — keepsakes, historical plaques,
high-importance chronicle events, multi-generational dynasties, and retired
elders — into `museum_exhibits` at a bounded cadence (~1 exhibit every 10-20
days). Tracks daily museum visitors (higher on rainy/stormy days and among
curious residents), transfers optional tiny conserved visitor donations from
`agent_state.money_cents` to `town_account`, and logs `museum_exhibit` events.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .economy import town_credit
from .events import NOTABLE, describe, emit
from .rng import rng_for
from .seasons import date_of
from .timekeeper import TICKS_PER_DAY
from .weather import today as weather_today

MIN_CURATION_GAP_DAYS = 10
MAX_CURATION_GAP_DAYS = 20
P_CURATE_IN_WINDOW = 0.18

DONATION_MIN_WALLET_CENTS = 50_000
DONATION_MIN_CENTS = 100
DONATION_MAX_CENTS = 500
P_VISITOR_DONATION = 0.25

CURIOUS_KEYWORDS = {
    "curious", "inquisitive", "history", "historical", "art", "painting",
    "reading", "museum", "science", "learning", "photography", "writing",
    "archaeology", "genealogy", "architecture", "poetry", "book",
}

WEATHER_VISITOR_MULT: dict[str, float] = {
    "storm": 1.65,
    "blizzard": 1.65,
    "rain": 1.45,
    "snow": 1.45,
    "fog": 1.25,
    "heatwave": 1.25,
    "overcast": 1.10,
    "breezy": 1.00,
    "clear": 1.00,
}


def ensure_museum_schema(conn: sqlite3.Connection) -> None:
    """Create museum tables and indexes if they do not yet exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS museum_exhibits (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               day_curated INTEGER NOT NULL,
               year_curated INTEGER NOT NULL,
               category TEXT NOT NULL,
               title TEXT NOT NULL,
               description TEXT NOT NULL,
               donor_agent_id INTEGER REFERENCES agents(id),
               subject_agent_id INTEGER REFERENCES agents(id),
               place_id INTEGER REFERENCES places(id),
               visitors_count INTEGER NOT NULL DEFAULT 0,
               featured INTEGER NOT NULL DEFAULT 0
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_museum_exhibits_day ON museum_exhibits(day_curated DESC)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS museum_days (
               day INTEGER PRIMARY KEY,
               visitors INTEGER NOT NULL DEFAULT 0,
               donations_cents INTEGER NOT NULL DEFAULT 0,
               weather_condition TEXT NOT NULL DEFAULT 'clear',
               featured_exhibit_id INTEGER REFERENCES museum_exhibits(id)
           )"""
    )


def _default_museum_place_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """SELECT id FROM places
           WHERE name IN ('Miniville Public Library', 'Miniville Community Center', 'Town Hall')
           ORDER BY CASE name
               WHEN 'Miniville Public Library' THEN 1
               WHEN 'Miniville Community Center' THEN 2
               ELSE 3
           END
           LIMIT 1"""
    ).fetchone()
    if row:
        return int(row["id"])
    row = conn.execute(
        "SELECT id FROM places WHERE kind IN ('public', 'civic') ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def _parse_keywords(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        items = json.loads(raw)
        if isinstance(items, list):
            return {str(x).strip().lower() for x in items}
    except (ValueError, TypeError):
        pass
    return set()


def _is_curious(row: sqlite3.Row) -> bool:
    tokens = _parse_keywords(row["traits_json"]) | _parse_keywords(row["hobbies_json"])
    return any(any(kw in tok for kw in CURIOUS_KEYWORDS) for tok in tokens)


def _collect_candidates(conn: sqlite3.Connection, day: int) -> list[dict[str, Any]]:
    """Gather uncurated exhibit candidates from keepsakes, plaques, events, dynasties, and elders."""
    existing = {
        (r["category"], r["title"])
        for r in conn.execute("SELECT category, title FROM museum_exhibits").fetchall()
    }
    candidates: list[dict[str, Any]] = []

    # 1. Keepsakes
    try:
        krows = conn.execute(
            """SELECT k.id, k.title, k.craft, k.creator_id, k.owner_id, k.venue_id,
                      k.generations, k.appreciations,
                      c.name AS creator_name, o.name AS owner_name
               FROM keepsakes k
               JOIN agents c ON c.id = k.creator_id
               JOIN agents o ON o.id = k.owner_id
               ORDER BY k.generations DESC, k.appreciations DESC, k.id ASC
               LIMIT 10"""
        ).fetchall()
        for k in krows:
            title = f"Heirloom: {k['title']}"
            if ("keepsake", title) not in existing:
                gens = int(k["generations"] or 0) + 1
                desc = (
                    f"Crafted by {k['creator_name']} ({k['craft']}), preserved across "
                    f"{gens} generation(s) in {k['owner_name']}'s care."
                )
                candidates.append({
                    "category": "keepsake",
                    "title": title,
                    "description": desc,
                    "donor_agent_id": int(k["owner_id"]),
                    "subject_agent_id": int(k["creator_id"]),
                    "place_id": k["venue_id"],
                })
    except sqlite3.OperationalError:
        pass

    # 2. Historical plaques
    try:
        prows = conn.execute(
            """SELECT hp.id, hp.title, hp.inscription, hp.honouree_id, hp.place_id
               FROM historical_plaques hp
               ORDER BY hp.day DESC, hp.id DESC
               LIMIT 10"""
        ).fetchall()
        for hp in prows:
            title = str(hp["title"])
            if ("plaque", title) not in existing:
                candidates.append({
                    "category": "plaque",
                    "title": title,
                    "description": str(hp["inscription"]),
                    "donor_agent_id": None,
                    "subject_agent_id": int(hp["honouree_id"]) if hp["honouree_id"] is not None else None,
                    "place_id": hp["place_id"],
                })
    except sqlite3.OperationalError:
        pass

    # 3. Recent high-importance events
    min_day = max(0, day - 30)
    erows = conn.execute(
        """SELECT * FROM events
           WHERE importance >= 3 AND day >= ? AND kind != 'museum_exhibit'
           ORDER BY importance DESC, day DESC, id DESC
           LIMIT 12""",
        (min_day,),
    ).fetchall()
    for e in erows:
        kind_label = str(e["kind"]).replace("_", " ").title()
        title = f"Day {int(e['day']) + 1} {kind_label} (#{e['id']})"
        if ("event", title) not in existing:
            candidates.append({
                "category": "event",
                "title": title,
                "description": describe(conn, e),
                "donor_agent_id": None,
                "subject_agent_id": int(e["a_id"]) if e["a_id"] is not None else None,
                "place_id": e["place_id"],
            })

    # 4. Dynasties (multi-member families sharing a surname)
    arows = conn.execute(
        """SELECT id, name, age, alive, standing, work_place_id, home_place_id
           FROM agents
           ORDER BY COALESCE(age, 0) DESC, id ASC"""
    ).fetchall()
    by_surname: dict[str, list[sqlite3.Row]] = {}
    for a in arows:
        parts = str(a["name"]).strip().split()
        if len(parts) >= 2:
            surname = parts[-1]
            by_surname.setdefault(surname, []).append(a)
    for surname, members in sorted(by_surname.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]:
        if len(members) >= 2:
            anchor = members[0]
            title = f"The {surname} Family Legacy"
            if ("dynasty", title) not in existing:
                candidates.append({
                    "category": "dynasty",
                    "title": title,
                    "description": (
                        f"Multi-generational Miniville lineage of {len(members)} residents, "
                        f"anchored by {anchor['name']}."
                    ),
                    "donor_agent_id": int(anchor["id"]) if anchor["alive"] else None,
                    "subject_agent_id": int(anchor["id"]),
                    "place_id": anchor["work_place_id"] or anchor["home_place_id"],
                })

    # 5. Retired elders
    elders = conn.execute(
        """SELECT a.id, a.name, a.age, a.occupation, COALESCE(a.standing, 0) AS standing,
                  a.work_place_id, a.home_place_id
           FROM agents a
           WHERE a.alive = 1 AND a.is_child = 0 AND COALESCE(a.age, 0) >= 65
             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.agent_id = a.id)
           ORDER BY COALESCE(a.standing, 0) DESC, a.age DESC, a.id ASC
           LIMIT 8"""
    ).fetchall()
    for el in elders:
        title = f"Portrait of {el['name']}"
        if ("elder", title) not in existing:
            occ = el["occupation"] or "townsperson"
            candidates.append({
                "category": "elder",
                "title": title,
                "description": (
                    f"Honouring retired {occ} {el['name']} "
                    f"(age {el['age']}, standing {el['standing']})."
                ),
                "donor_agent_id": int(el["id"]),
                "subject_agent_id": int(el["id"]),
                "place_id": el["work_place_id"] or el["home_place_id"],
            })

    return candidates


def _should_curate(conn: sqlite3.Connection, day: int, r) -> bool:
    row = conn.execute("SELECT MAX(day_curated) AS last_day FROM museum_exhibits").fetchone()
    last_day = row["last_day"] if row and row["last_day"] is not None else None
    if last_day is None:
        if day >= 14:
            return True
        return r.random() < 0.20
    gap = int(day) - int(last_day)
    if gap < MIN_CURATION_GAP_DAYS:
        return False
    if gap >= MAX_CURATION_GAP_DAYS:
        return True
    return r.random() < P_CURATE_IN_WINDOW


def daily_museum(conn: sqlite3.Connection, day: int, seed: str = "miniville") -> dict[str, Any]:
    """Run one day of museum curation, visitor tracking, and conserved donations.

    Idempotent per `day`: if `museum_days` already has a row for `day`, returns
    the recorded day summary without duplicating visitors, exhibits, or transfers.
    """
    ensure_museum_schema(conn)
    day = int(day)

    existing_day = conn.execute(
        "SELECT day, visitors, donations_cents, weather_condition, featured_exhibit_id FROM museum_days WHERE day = ?",
        (day,),
    ).fetchone()
    if existing_day:
        return {
            "day": int(existing_day["day"]),
            "curated": None,
            "visitors": int(existing_day["visitors"]),
            "donations_cents": int(existing_day["donations_cents"]),
            "weather_condition": existing_day["weather_condition"],
            "featured_exhibit_id": existing_day["featured_exhibit_id"],
            "already_ran": True,
        }

    tick = day * TICKS_PER_DAY + 24
    r_curate = rng_for(seed, "museum_curate", day)
    curated_exhibit: dict[str, Any] | None = None

    if _should_curate(conn, day, r_curate):
        candidates = _collect_candidates(conn, day)
        if candidates:
            pick = r_curate.choice(candidates)
            year_curated, _, _ = date_of(day)
            place_id = pick["place_id"] or _default_museum_place_id(conn)
            conn.execute("UPDATE museum_exhibits SET featured = 0 WHERE featured = 1")
            cur = conn.execute(
                """INSERT INTO museum_exhibits(
                       day_curated, year_curated, category, title, description,
                       donor_agent_id, subject_agent_id, place_id, visitors_count, featured
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)""",
                (
                    day,
                    year_curated,
                    pick["category"],
                    pick["title"],
                    pick["description"],
                    pick["donor_agent_id"],
                    pick["subject_agent_id"],
                    place_id,
                ),
            )
            exhibit_id = int(cur.lastrowid)
            emit(
                conn,
                tick,
                "museum_exhibit",
                place_id=place_id,
                a=pick["subject_agent_id"],
                b=pick["donor_agent_id"],
                importance=NOTABLE,
                exhibit_id=exhibit_id,
                category=pick["category"],
                title=pick["title"],
                text=f"The Miniville Museum unveiled '{pick['title']}' ({pick['category']})",
            )
            curated_exhibit = {
                "id": exhibit_id,
                "day_curated": day,
                "year_curated": year_curated,
                "category": pick["category"],
                "title": pick["title"],
                "description": pick["description"],
            }

    # Visitor tracking
    w = weather_today(conn, day)
    cond = str(w.get("condition", "clear"))
    weather_mult = WEATHER_VISITOR_MULT.get(cond, 1.0)

    exhibit_rows = conn.execute(
        "SELECT id, featured FROM museum_exhibits ORDER BY featured DESC, day_curated DESC, id DESC"
    ).fetchall()
    has_exhibits = len(exhibit_rows) > 0
    base_prob = 0.08 if has_exhibits else 0.02

    residents = conn.execute(
        """SELECT a.id, a.traits_json, a.hobbies_json, s.money_cents
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1
           ORDER BY a.id ASC"""
    ).fetchall()

    r_visit = rng_for(seed, "museum_visitors", day)
    visitors_count = 0
    donations_cents = 0

    for res in residents:
        curiosity_mult = 2.0 if _is_curious(res) else 1.0
        p_visit = min(0.85, base_prob * weather_mult * curiosity_mult)
        if r_visit.random() < p_visit:
            visitors_count += 1
            aid = int(res["id"])
            conn.execute(
                """UPDATE agent_state
                   SET fun = MIN(100, fun + 3),
                       stress = MAX(0, stress - 2)
                   WHERE agent_id = ?""",
                (aid,),
            )
            if int(res["money_cents"]) >= DONATION_MIN_WALLET_CENTS and r_visit.random() < P_VISITOR_DONATION:
                gift = r_visit.randint(DONATION_MIN_CENTS, DONATION_MAX_CENTS)
                if int(res["money_cents"]) >= gift:
                    conn.execute(
                        "UPDATE agent_state SET money_cents = money_cents - ? WHERE agent_id = ?",
                        (gift, aid),
                    )
                    town_credit(conn, gift)
                    donations_cents += gift

    featured_id: int | None = int(exhibit_rows[0]["id"]) if exhibit_rows else None
    if visitors_count > 0 and exhibit_rows:
        # Credit featured exhibit primarily, sharing a portion with up to 2 recent exhibits
        if len(exhibit_rows) == 1:
            conn.execute(
                "UPDATE museum_exhibits SET visitors_count = visitors_count + ? WHERE id = ?",
                (visitors_count, featured_id),
            )
        else:
            featured_share = max(1, (visitors_count * 2 + 2) // 3)
            other_share = visitors_count - featured_share
            conn.execute(
                "UPDATE museum_exhibits SET visitors_count = visitors_count + ? WHERE id = ?",
                (featured_share, featured_id),
            )
            if other_share > 0:
                secondary = exhibit_rows[1:3]
                per, rem = divmod(other_share, len(secondary))
                for idx, ex in enumerate(secondary):
                    add = per + (1 if idx < rem else 0)
                    if add > 0:
                        conn.execute(
                            "UPDATE museum_exhibits SET visitors_count = visitors_count + ? WHERE id = ?",
                            (add, int(ex["id"])),
                        )

    conn.execute(
        """INSERT INTO museum_days(day, visitors, donations_cents, weather_condition, featured_exhibit_id)
           VALUES (?, ?, ?, ?, ?)""",
        (day, visitors_count, donations_cents, cond, featured_id),
    )

    return {
        "day": day,
        "curated": curated_exhibit,
        "visitors": visitors_count,
        "donations_cents": donations_cents,
        "weather_condition": cond,
        "featured_exhibit_id": featured_id,
        "already_ran": False,
    }


def list_exhibits(conn: sqlite3.Connection, limit: int = 24) -> list[dict[str, Any]]:
    """Return museum exhibits ordered by featured status and recency."""
    ensure_museum_schema(conn)
    rows = conn.execute(
        """SELECT m.*,
                  d.name AS donor_name,
                  s.name AS subject_name,
                  p.name AS place_name
           FROM museum_exhibits m
           LEFT JOIN agents d ON d.id = m.donor_agent_id
           LEFT JOIN agents s ON s.id = m.subject_agent_id
           LEFT JOIN places p ON p.id = m.place_id
           ORDER BY m.featured DESC, m.day_curated DESC, m.id DESC
           LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def museum_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return aggregate museum stats, featured exhibit, and recent exhibits."""
    ensure_museum_schema(conn)
    totals = conn.execute(
        """SELECT COUNT(*) AS total_exhibits,
                  COALESCE(SUM(visitors_count), 0) AS exhibit_visitors
           FROM museum_exhibits"""
    ).fetchone()
    day_totals = conn.execute(
        """SELECT COALESCE(SUM(visitors), 0) AS total_visitors,
                  COALESCE(SUM(donations_cents), 0) AS total_donations_cents
           FROM museum_days"""
    ).fetchone()
    latest = conn.execute(
        """SELECT day, visitors, donations_cents, weather_condition
           FROM museum_days ORDER BY day DESC LIMIT 1"""
    ).fetchone()
    cats = conn.execute(
        "SELECT category, COUNT(*) AS n FROM museum_exhibits GROUP BY category ORDER BY n DESC"
    ).fetchall()
    exhibits = list_exhibits(conn, limit=12)
    featured = next((e for e in exhibits if e.get("featured")), None)

    return {
        "total_exhibits": int(totals["total_exhibits"]),
        "total_visitors": max(int(day_totals["total_visitors"]), int(totals["exhibit_visitors"])),
        "total_donations_cents": int(day_totals["total_donations_cents"]),
        "today_visitors": int(latest["visitors"]) if latest else 0,
        "today_donations_cents": int(latest["donations_cents"]) if latest else 0,
        "last_day": int(latest["day"]) if latest else None,
        "featured": featured,
        "by_category": {r["category"]: int(r["n"]) for r in cats},
        "exhibits": exhibits,
    }
