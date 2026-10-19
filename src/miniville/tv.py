"""10-Foot Living TV & Ambient Director Mode backend (v0.44 - v0.47).

Curates a calm, high-signal television broadcast feed (`tv_director_snapshot`)
and optional background simulation stepping (`tv_step`) designed for a
widescreen television display:

* **Bounded Active Movers (`max_movers=10`)** — compares each resident's
  previous/planned location with their current venue so the TV map can animate
  8–10 purposeful commuters/walkers gliding along smooth paths without turning
  600 residents into visual noise.
* **Rotating Director Spotlight** — deterministically rotates every tick/scene
  through notable residents (with their temperament, active ambition, household
  pet, keepsakes, and latest happening) and bustling venues.
* **Progression & Broadcast Ticker** — aggregates WMNV 94.3 FM radio headlines,
  recent life milestones, civic landmarks, theater productions, book club picks,
  craft guilds, and economy/vitality progression for the 10-foot lower-third HUD.
* **Ambient Sky & Time-of-Day Palette** — computes smooth sky tinting and lighting
  phases (`night`, `dawn`, `morning`, `afternoon`, `dusk`) from `tick_of_day`.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import ambitions, economy, engine, individual, seasons, timekeeper
from .db import get_meta, set_meta
from .events import describe
from .ui.server import _district_layout, _venue_xy


def _sky_state(tod: int, season: str) -> dict[str, Any]:
    """Return ambient lighting phase and hex tint for the 10-foot TV canvas."""
    if tod < 10 or tod >= 44:
        return {
            "phase": "night",
            "label": "Starlight Night",
            "bg_hex": "#0b0e17",
            "tint_hex": "#1a2542",
            "ambient_alpha": 0.32,
        }
    if tod < 14:
        return {
            "phase": "dawn",
            "label": "Early Dawn",
            "bg_hex": "#161926",
            "tint_hex": "#d98a6c",
            "ambient_alpha": 0.16,
        }
    if tod < 24:
        return {
            "phase": "morning",
            "label": f"{season.capitalize()} Morning",
            "bg_hex": "#141a24",
            "tint_hex": "#8ab4f8",
            "ambient_alpha": 0.06,
        }
    if tod < 36:
        return {
            "phase": "afternoon",
            "label": f"{season.capitalize()} Afternoon",
            "bg_hex": "#151b24",
            "tint_hex": "#f6c34c",
            "ambient_alpha": 0.08,
        }
    return {
        "phase": "dusk",
        "label": "Golden Hour & Evening",
        "bg_hex": "#151622",
        "tint_hex": "#e88d4c",
        "ambient_alpha": 0.18,
    }


def _venue_coords_map(conn: sqlite3.Connection) -> dict[int, tuple[float, float]]:
    zones = _district_layout()
    per_dist: dict[str, int] = {}
    out: dict[int, tuple[float, float]] = {}
    rows = conn.execute(
        "SELECT id, district FROM places WHERE kind != 'home' ORDER BY id"
    ).fetchall()
    for r in rows:
        d = r["district"] if r["district"] in zones else "_other"
        idx = per_dist.get(d, 0)
        per_dist[d] = idx + 1
        vx, vy = _venue_xy(zones[d], idx)
        out[int(r["id"])] = (float(vx), float(vy))
    return out


def _place_coords(place_row: dict[str, Any] | sqlite3.Row | None,
                  fallback_district: str | None = None,
                  agent_id: int = 1,
                  vcoords: dict[int, tuple[float, float]] | None = None) -> tuple[float, float, str]:
    """Resolve (x, y, label) map coordinates for any venue or home district."""
    zones = _district_layout()
    if place_row is not None:
        pid = int(place_row["id"])
        pname = place_row["name"]
        if place_row["kind"] != "home" and vcoords and pid in vcoords:
            vx, vy = vcoords[pid]
            return float(vx), float(vy), pname
        dist = place_row["district"] or fallback_district or "_other"
    else:
        dist = fallback_district or "_other"

    tile = zones.get(dist) or zones["_other"]
    col = (agent_id * 37) % max(1, int((tile["w"] - 48) // 14))
    row = (agent_id * 13) % 3
    hx = float(tile["x"] + 24 + col * 14)
    hy = float(tile["y"] + tile["h"] - 64 + row * 14)
    return hx, hy, f"{tile['name']} Homes"


def _curated_movers(conn: sqlite3.Connection, tick: int,
                    places_by_id: dict[int, dict[str, Any]],
                    vcoords: dict[int, tuple[float, float]],
                    max_movers: int = 10) -> list[dict[str, Any]]:
    """Select at most `max_movers` active travellers with origin -> destination coords.

    Instead of animating 600 dots simultaneously (which creates visual chaos on
    a TV), we prioritize residents who just transitioned between places between
    `tick - 1` and `tick` (or `tick` and `tick + 1`), supplemented by active
    public-venue visitors with recent events so there are always ~6-10 calm,
    purposeful movers on screen.
    """
    # plans are keyed by tick-of-day (0-47), not by absolute tick: looking them
    # up by `tick - 1` matched nothing after day one, so every mover fell back
    # to a home -> venue arc instead of showing who had actually just moved
    tod = timekeeper.tick_of_day(tick)
    per_day = timekeeper.TICKS_PER_DAY
    prev_plans = {
        int(r["agent_id"]): (r["place_id"], r["activity"])
        for r in conn.execute(
            "SELECT agent_id, place_id, activity FROM plans WHERE tick = ?",
            ((tod - 1) % per_day,),
        ).fetchall()
    } if tod > 0 else {}
    next_plans = {
        int(r["agent_id"]): (r["place_id"], r["activity"])
        for r in conn.execute(
            "SELECT agent_id, place_id, activity FROM plans WHERE tick = ?",
            (tod + 1,),
        ).fetchall()
    } if tod + 1 < per_day else {}
    recent_event_agents = {
        int(r["a_id"])
        for r in conn.execute(
            "SELECT a_id FROM events WHERE tick >= ? AND a_id IS NOT NULL ORDER BY importance DESC LIMIT 30",
            (max(0, tick - 3),),
        ).fetchall()
    }

    rows = conn.execute(
        """SELECT a.id, a.name, a.occupation, a.home_place_id,
                  hp.district AS home_district,
                  s.place_id, s.activity, s.mood
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN places hp ON hp.id = a.home_place_id
           WHERE a.alive = 1 AND s.activity != 'sleep'
           ORDER BY a.id"""
    ).fetchall()

    candidates: list[tuple[int, dict[str, Any]]] = []
    for r in rows:
        aid = int(r["id"])
        cur_pid = r["place_id"]
        cur_place = places_by_id.get(int(cur_pid)) if cur_pid else None
        to_x, to_y, to_label = _place_coords(cur_place, r["home_district"], aid, vcoords)

        prev_pid, prev_act = prev_plans.get(aid, (None, None))
        next_pid, next_act = next_plans.get(aid, (None, None))

        origin_pid = None
        if prev_pid is not None and prev_pid != cur_pid:
            origin_pid = prev_pid
        elif next_pid is not None and next_pid != cur_pid:
            # Show them setting out toward their next planned stop
            origin_pid = cur_pid
            dest_place = places_by_id.get(int(next_pid))
            to_x, to_y, to_label = _place_coords(dest_place, r["home_district"], aid, vcoords)
        elif cur_place and cur_place["kind"] != "home" and r["home_place_id"]:
            origin_pid = r["home_place_id"]

        orig_place = places_by_id.get(int(origin_pid)) if origin_pid else None
        from_x, from_y, from_label = _place_coords(orig_place, r["home_district"], aid, vcoords)

        dist_sq = (to_x - from_x) ** 2 + (to_y - from_y) ** 2
        if dist_sq < 400:
            continue

        # Score candidates so real commuters and recent event actors come first,
        # rotated deterministically by tick so different villagers take turns moving
        score = 0
        if prev_pid is not None and prev_pid != cur_pid:
            score += 50
        if aid in recent_event_agents:
            score += 35
        if r["activity"] in ("social_call", "celebrate", "eat_out", "leisure", "shopping", "work"):
            score += 20
        score += ((aid * 17 + tick * 7) % 19)

        candidates.append((
            score,
            {
                "id": aid,
                "name": r["name"],
                "occupation": (r["occupation"] or "resident").replace("_", " "),
                "activity": r["activity"],
                "mood": r["mood"],
                "place_id": cur_pid,
                "from_x": round(from_x, 1),
                "from_y": round(from_y, 1),
                "from_label": from_label,
                "to_x": round(to_x, 1),
                "to_y": round(to_y, 1),
                "to_label": to_label,
            },
        ))

    # Guarantee gentle movement even during quiet midnight ticks (night watch,
    # hospital rounds, early bakers, and night owls) so the TV never freezes
    if len(candidates) < min(4, max_movers):
        night_rows = conn.execute(
            """SELECT a.id, a.name, a.occupation, a.home_place_id,
                      hp.district AS home_district,
                      a.work_place_id, s.mood
               FROM agents a
               JOIN agent_state s ON s.agent_id = a.id
               LEFT JOIN places hp ON hp.id = a.home_place_id
               WHERE a.alive = 1 AND a.is_child = 0
               ORDER BY a.id"""
        ).fetchall()
        public_places = [p for p in places_by_id.values() if p["kind"] != "home"]
        seen_ids = {item[1]["id"] for item in candidates}
        for idx, nr in enumerate(night_rows):
            if len(candidates) >= min(6, max_movers):
                break
            aid = int(nr["id"])
            if aid in seen_ids:
                continue
            # Rotate which residents do midnight rounds each tick
            if (aid + tick) % max(1, len(night_rows) // 6) != 0 and idx < len(night_rows) - 6:
                continue
            from_x, from_y, from_label = _place_coords(None, nr["home_district"], aid, vcoords)
            target_place = places_by_id.get(int(nr["work_place_id"])) if nr["work_place_id"] else (
                public_places[(aid + tick) % len(public_places)] if public_places else None
            )
            to_x, to_y, to_label = _place_coords(target_place, nr["home_district"], aid, vcoords)
            if (to_x - from_x) ** 2 + (to_y - from_y) ** 2 < 400:
                continue
            candidates.append((
                10 - idx,
                {
                    "id": aid,
                    "name": nr["name"],
                    "occupation": (nr["occupation"] or "resident").replace("_", " "),
                    "activity": "leisure",
                    "mood": nr["mood"],
                    "place_id": target_place["id"] if target_place else None,
                    "from_x": round(from_x, 1),
                    "from_y": round(from_y, 1),
                    "from_label": from_label,
                    "to_x": round(to_x, 1),
                    "to_y": round(to_y, 1),
                    "to_label": f"{to_label} (night watch)",
                },
            ))

    candidates.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [item[1] for item in candidates[:max_movers]]


def _spotlight_resident(conn: sqlite3.Connection, tick: int,
                        places_by_id: dict[int, dict[str, Any]],
                        vcoords: dict[int, tuple[float, float]],
                        focus_agent_id: int | None = None) -> dict[str, Any] | None:
    """Pick a compelling spotlight resident (either requested or rotated by tick)."""
    seed = get_meta(conn, "seed", "miniville") or "miniville"
    chosen_id = focus_agent_id

    if not chosen_id:
        # The cast of the town's current storylines, awake first, held for two
        # game hours each: the old rotation changed every tick and often
        # featured someone asleep.
        pool = storyline_cast(conn, tick)
        if not pool:
            pool = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM agents WHERE alive = 1 ORDER BY standing DESC, id ASC LIMIT 16"
                ).fetchall()
            ]
        if pool:
            chosen_id = pool[(tick // SPOTLIGHT_SLOT_TICKS) % len(pool)]

    if not chosen_id:
        return None

    row = conn.execute(
        """SELECT a.*, s.place_id, s.activity, s.mood, s.energy, s.stress,
                  s.social, s.fun, s.money_cents,
                  p.name AS place_name, p.district AS place_district,
                  hp.district AS home_district,
                  h.name AS household_name, h.tradition AS household_tradition
           FROM agents a
           JOIN agent_state s ON s.agent_id = a.id
           LEFT JOIN places p ON p.id = s.place_id
           LEFT JOIN places hp ON hp.id = a.home_place_id
           LEFT JOIN households h ON h.id = a.household_id
           WHERE a.id = ? AND a.alive = 1""",
        (chosen_id,),
    ).fetchone()
    if not row:
        return None

    aid = int(row["id"])
    prof = individual.profile_of(conn, aid, seed)
    amb = ambitions.ambition_of(conn, aid)

    # Check household pet if pets table exists
    pet_info = None
    try:
        from . import pets
        p_obj = pets.pet_for_resident(conn, aid)
        if p_obj:
            pet_info = f"{p_obj['name']} ({p_obj['breed']} {p_obj['species']})"
    except Exception:
        pass

    # Latest personal event or memory
    latest_ev = conn.execute(
        """SELECT * FROM events
           WHERE (a_id = ? OR b_id = ?) AND importance >= 2
           ORDER BY id DESC LIMIT 1""",
        (aid, aid),
    ).fetchone()
    latest_story = describe(conn, latest_ev) if latest_ev else None

    cur_place = places_by_id.get(int(row["place_id"])) if row["place_id"] else None
    cx, cy, clabel = _place_coords(cur_place, row["home_district"], aid, vcoords)

    return {
        "id": aid,
        "name": row["name"],
        "age": row["age"],
        "occupation": (row["occupation"] or "resident").replace("_", " "),
        "standing": int(row["standing"] or 0),
        "archetype": prof.get("archetype", "balanced regular"),
        "chronotype": prof.get("chronotype", "day_walker"),
        "knack": prof.get("knack", "helping out"),
        "activity": row["activity"],
        "mood": row["mood"],
        "money_dollars": round(int(row["money_cents"] or 0) / 100),
        "energy": row["energy"],
        "social": row["social"],
        "fun": row["fun"],
        "stress": row["stress"],
        "place_id": row["place_id"],
        "place_name": clabel,
        "x": round(cx, 1),
        "y": round(cy, 1),
        "household": row["household_name"],
        "tradition": row["household_tradition"],
        "pet": pet_info,
        "ambition": {
            "label": amb["label"],
            "progress_pct": round(float(amb["progress"] or 0.0) * 100),
            "status": amb["status"],
        } if amb else None,
        "latest_story": latest_story,
        "avatar_url": f"/avatars/{aid}.jpg" if row["avatar_path"] else None,
    }


def _spotlight_venue(conn: sqlite3.Connection, tick: int,
                     places_by_id: dict[int, dict[str, Any]],
                     vcoords: dict[int, tuple[float, float]],
                     focus_place_id: int | None = None) -> dict[str, Any] | None:
    """Pick an active public/workplace venue to feature on the TV camera."""
    crowd_rows = conn.execute(
        """SELECT p.id, p.name, p.kind, p.district, p.capacity,
                  COUNT(s.agent_id) AS inside
           FROM places p
           LEFT JOIN agent_state s ON s.place_id = p.id AND s.activity != 'sleep'
           WHERE p.kind != 'home'
           GROUP BY p.id
           ORDER BY inside DESC, p.id ASC"""
    ).fetchall()
    if not crowd_rows:
        return None

    chosen = None
    if focus_place_id:
        chosen = next((r for r in crowd_rows if int(r["id"]) == int(focus_place_id)), None)
    if chosen is None:
        # Rotate among the busiest venues, never featuring an empty one while
        # anywhere has people in it
        active_pool = [r for r in crowd_rows if int(r["inside"] or 0) > 0][:5] or crowd_rows[:5]
        chosen = active_pool[(tick // SPOTLIGHT_SLOT_TICKS) % len(active_pool)]

    pid = int(chosen["id"])
    occupants = conn.execute(
        """SELECT a.id, a.name, a.occupation, s.activity, s.mood
           FROM agent_state s
           JOIN agents a ON a.id = s.agent_id
           WHERE s.place_id = ? AND a.alive = 1
           ORDER BY a.standing DESC, a.id ASC LIMIT 8""",
        (pid,),
    ).fetchall()

    recent_ev = conn.execute(
        """SELECT * FROM events
           WHERE place_id = ? AND importance >= 2
           ORDER BY id DESC LIMIT 1""",
        (pid,),
    ).fetchone()

    vx, vy = vcoords.get(pid, (800.0, 450.0))
    return {
        "id": pid,
        "name": chosen["name"],
        "kind": chosen["kind"],
        "district": chosen["district"],
        "capacity": chosen["capacity"],
        "inside": int(chosen["inside"] or 0),
        "x": float(vx),
        "y": float(vy),
        "occupants": [dict(o) for o in occupants],
        "latest_event": describe(conn, recent_ev) if recent_ev else None,
    }


def _progression_cards(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Gather high-level village progression milestones for the TV HUD."""
    cards: list[dict[str, Any]] = []

    # 1. Ambitions & Mentorships completed
    amb_done = conn.execute(
        "SELECT COUNT(*) n FROM ambitions WHERE status = 'completed'"
    ).fetchone()["n"]
    amb_active = conn.execute(
        "SELECT COUNT(*) n FROM ambitions WHERE status = 'active'"
    ).fetchone()["n"]
    cards.append({
        "category": "Ambitions",
        "title": f"{amb_done} Life Goals Fulfilled",
        "detail": f"{amb_active} residents actively pursuing personal ambitions",
    })

    # 2. Civic landmarks / projects
    try:
        proj = conn.execute(
            "SELECT title, district, status, progress_pct FROM civic_projects ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if proj:
            cards.append({
                "category": "Civic Landmark",
                "title": proj["title"],
                "detail": f"{proj['district']} · {proj['status']} ({int(proj['progress_pct'] or 0)}%)",
            })
    except sqlite3.OperationalError:
        pass

    # 3. Cultural Commons / Theater / Book Club / Guilds
    try:
        show = conn.execute(
            "SELECT title, genre, status FROM theater_productions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if show:
            cards.append({
                "category": "Stage & Arts",
                "title": show["title"],
                "detail": f"{show['genre'].capitalize()} production ({show['status']})",
            })
    except sqlite3.OperationalError:
        pass

    # 4. Town Era / Hall of Fame
    try:
        era = conn.execute(
            "SELECT title, summary FROM town_eras ORDER BY start_day DESC LIMIT 1"
        ).fetchone()
        if era:
            cards.append({
                "category": "Current Era",
                "title": era["title"],
                "detail": era["summary"],
            })
    except sqlite3.OperationalError:
        pass

    return cards[:4]


def _broadcast_ticker(conn: sqlite3.Connection, tick: int) -> list[dict[str, Any]]:
    """Build a 10-foot lower-third broadcast ticker with clickable items."""
    items: list[dict[str, Any]] = []

    # Latest WMNV 94.3 FM radio broadcast if present
    try:
        from . import radio
        for b in radio.list_broadcasts(conn, 2):
            items.append({
                "badge": "📻 WMNV 94.3 FM",
                "text": f"{b['title']}: {b['transcript']}",
                "agent_id": b.get("host_id"),
                "place_id": None,
                "time": f"Day {int(b['day']) + 1}",
            })
    except Exception:
        pass

    # Recent notable town & personal events
    evs = conn.execute(
        """SELECT * FROM events
           WHERE importance >= 2
           ORDER BY id DESC LIMIT 12"""
    ).fetchall()
    for e in evs:
        kind_badge = {
            "life_event": "✨ Village Life",
            "romance": "💛 Romance",
            "milestone": "🎉 Milestone",
            "town_event": "🏛️ Town Wire",
            "conflict": "⚡ Tension",
            "encounter": "💬 Encounter",
        }.get(e["kind"], "📰 News")
        # a story the director recognises gets its own label (Police Blotter,
        # Wedding, Grand Opening...) instead of the generic kind
        category = _category(e)
        if category != "Village Life" and newsworthiness(e) >= STORY_MIN:
            kind_badge = f"🔴 {category}" if newsworthiness(e) >= BREAKING_MIN else f"📰 {category}"
        items.append({
            "badge": kind_badge,
            "text": describe(conn, e),
            "agent_id": e["a_id"],
            "place_id": e["place_id"],
            "time": timekeeper.fmt_tick(int(e["tick"])),
        })

    return items[:12]


# --- the story director (v0.48) ---------------------------------------------
#
# The v0.44 director rotated mechanically: a new "spotlight" every tick drawn
# from whoever last appeared in the ledger (often someone asleep), a venue
# with nobody in it, and no way for the town's real news to reach the screen.
# The importance field cannot pick the news on its own: in a 600-resident town
# about 56 events a day are importance 3, and almost all of them are a
# relationship label drifting ("acquaintance", "familiar"), while a wedding,
# a death or a shop opening is a handful a day. So the director scores each
# event for how much a viewer would want to see it, and builds the broadcast
# from that: story scenes, breaking news that cuts in, map hotspots, and a
# recap of yesterday.

# extra newsworthiness by event tag, on top of 10 x importance
NEWS_BONUS: dict[str, int] = {
    "death": 40, "obituary": -40, "birth": 30, "marriage": 35, "cohabitation": 25,
    "separation": 25, "betrayal": 30, "business_founded": 30, "business_closed": 30,
    "business_reopened": 15, "inheritance": 15, "arrest": 30, "theft": 10,
    "shock_fire": 40, "shock_closure": 30, "injured": 20, "festival": 25,
    "festival_announced": 10, "election": 30, "seat_vacated": 20, "motion_passed": 15,
    "slander": 25, "boycott": 25, "schism": 25, "promoted": 10, "fired": 10,
    "downsize": 10, "immigration": 5, "coming_of_age": 10, "retirement_sendoff": 10,
    "theater_premiere": 15, "guild_masterpiece": 12, "civic_project_completed": 20,
    "plaque_dedicated": 12, "key_to_the_town": 25, "competition_won": 10,
    "district_derby": 10, "block_party": 10, "ambition_achieved": 8,
    "apprenticeship_completed": 5, "town_deficit": 10, "press_claim": 15,
    "sick": -10, "medical_treatment": -10, "move": -5, "owner_draws": -25,
    "dividend": -15, "wage_change": -10, "jobs_rebalanced": -25, "season": -10,
}
KIND_BONUS: dict[str, int] = {
    "betrayal": 30, "affair": 15, "arrival": 5,
    "encounter": -20, "gossip": -20, "favor": -15, "favor_repaid": -15,
    "work_buddy": -10,
}
# a relationship row only makes the news when it turns into a romance or a feud
RELATIONSHIP_BONUS: dict[str, int] = {
    "sweetheart": 20, "partner": 25, "spouse": 30, "rival": 20, "estranged": 20,
    "close_friend": 0,
}
STORY_MIN = 35          # worth a scene in the playlist
BREAKING_MIN = 60       # worth cutting in for
HOTSPOT_MIN = 30        # worth a ripple on the map
HOTSPOT_TICKS = 6       # ...for three game hours
STORY_WINDOW_TICKS = 96  # scenes come from the last two game days
SPOTLIGHT_SLOT_TICKS = 4  # the featured resident changes every two game hours

CATEGORY_BY_TAG = {
    "death": "Obituary", "birth": "New Arrival", "marriage": "Wedding",
    "cohabitation": "Romance", "separation": "Heartbreak", "betrayal": "Scandal",
    "business_founded": "Grand Opening", "business_closed": "Closing Down",
    "business_reopened": "Back in Business", "inheritance": "Inheritance",
    "arrest": "Police Blotter", "theft": "Police Blotter", "shock_fire": "Fire",
    "shock_closure": "Closure", "injured": "Injured", "festival": "Festival",
    "election": "Election Night", "seat_vacated": "Council", "motion_passed": "Council",
    "slander": "Feud", "boycott": "Boycott", "schism": "Schism",
    "promoted": "Career", "fired": "Laid Off", "theater_premiere": "Opening Night",
    "ambition_achieved": "Milestone", "apprenticeship_completed": "Milestone",
    "civic_project_completed": "Civic Pride", "plaque_dedicated": "Civic Pride",
    "key_to_the_town": "Civic Pride", "competition_won": "Champion",
    "district_derby": "Champion", "guild_masterpiece": "Masterpiece",
}


def _event_data(ev: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(ev["data"] or "{}")
    except (TypeError, ValueError):
        return {}


def newsworthiness(ev: sqlite3.Row) -> int:
    """How much a viewer would want to see this event, 0 and up."""
    data = _event_data(ev)
    tag = data.get("tag")
    score = 10 * int(ev["importance"] or 0)
    score += KIND_BONUS.get(ev["kind"], 0)
    if ev["kind"] == "relationship":
        score += RELATIONSHIP_BONUS.get(data.get("label"), -25)
    if tag:
        score += NEWS_BONUS.get(tag, 0)
    return max(0, score)


def _category(ev: sqlite3.Row) -> str:
    data = _event_data(ev)
    if ev["kind"] == "relationship":
        label = data.get("label")
        return {"rival": "Feud", "estranged": "Feud"}.get(label, "Romance")
    if ev["kind"] in ("affair", "betrayal"):
        return "Scandal"
    return CATEGORY_BY_TAG.get(data.get("tag"), "Village Life")


def _event_frame(conn: sqlite3.Connection, ev: sqlite3.Row,
                 places_by_id: dict[int, dict[str, Any]],
                 vcoords: dict[int, tuple[float, float]]) -> tuple[float, float, str]:
    """Where the camera should look for an event: its venue, else where its
    resident is now, else their home district."""
    if ev["place_id"] and int(ev["place_id"]) in places_by_id:
        place = places_by_id[int(ev["place_id"])]
        return _place_coords(place, place.get("district"), int(ev["a_id"] or 1), vcoords)
    if ev["a_id"]:
        row = conn.execute(
            """SELECT s.place_id, hp.district home_district FROM agents a
               LEFT JOIN agent_state s ON s.agent_id=a.id
               LEFT JOIN places hp ON hp.id=a.home_place_id WHERE a.id=?""",
            (ev["a_id"],)).fetchone()
        if row:
            place = places_by_id.get(int(row["place_id"])) if row["place_id"] else None
            return _place_coords(place, row["home_district"], int(ev["a_id"]), vcoords)
    return 800.0, 450.0, "Miniville"


def _story_scene(conn: sqlite3.Connection, ev: sqlite3.Row, score: int,
                 places_by_id: dict[int, dict[str, Any]],
                 vcoords: dict[int, tuple[float, float]]) -> dict[str, Any]:
    x, y, label = _event_frame(conn, ev, places_by_id, vcoords)
    category = _category(ev)
    return {
        "mode": "story",
        "event_id": int(ev["id"]),
        "category": category,
        "title": f"{category}: {describe(conn, ev)}",
        "headline": describe(conn, ev),
        "subtitle": f"{label} · {timekeeper.fmt_tick(int(ev['tick']))}",
        "x": round(x, 1), "y": round(y, 1),
        "scale": 1.7,
        "agent_id": ev["a_id"], "other_id": ev["b_id"], "place_id": ev["place_id"],
        "score": score,
        "breaking": score >= BREAKING_MIN,
        "tick": int(ev["tick"]),
    }


def story_scenes(conn: sqlite3.Connection, tick: int,
                 places_by_id: dict[int, dict[str, Any]],
                 vcoords: dict[int, tuple[float, float]],
                 since_event_id: int = 0, limit: int = 6) -> list[dict[str, Any]]:
    """The most newsworthy events of the last two game days (or since the
    viewer's cursor), best first, one scene per resident-and-story."""
    rows = conn.execute(
        """SELECT * FROM events WHERE tick >= ? AND id > ? AND importance >= 2
           ORDER BY id DESC LIMIT 600""",
        (max(0, tick - STORY_WINDOW_TICKS), int(since_event_id or 0))).fetchall()
    scored = []
    seen: set[tuple] = set()
    for ev in rows:
        score = newsworthiness(ev)
        if score < STORY_MIN:
            continue
        key = (ev["a_id"], ev["b_id"], _event_data(ev).get("tag") or ev["kind"])
        if key in seen:
            continue            # the same story twice (a couple's move-in, say)
        seen.add(key)
        scored.append((score, int(ev["id"]), ev))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [_story_scene(conn, ev, score, places_by_id, vcoords)
            for score, _id, ev in scored[:limit]]


def hotspots(conn: sqlite3.Connection, tick: int,
             places_by_id: dict[int, dict[str, Any]],
             vcoords: dict[int, tuple[float, float]]) -> list[dict[str, Any]]:
    """Ripples on the map where something happened in the last few hours."""
    out = []
    for ev in conn.execute(
            """SELECT * FROM events WHERE tick >= ? AND importance >= 2
               ORDER BY id DESC LIMIT 300""",
            (max(0, tick - HOTSPOT_TICKS),)).fetchall():
        score = newsworthiness(ev)
        if score < HOTSPOT_MIN:
            continue
        x, y, _label = _event_frame(conn, ev, places_by_id, vcoords)
        out.append({"x": round(x, 1), "y": round(y, 1), "score": score,
                    "age": tick - int(ev["tick"]), "category": _category(ev),
                    "event_id": int(ev["id"])})
        if len(out) >= 12:
            break
    return out


def day_recap(conn: sqlite3.Connection, day: int, limit: int = 5) -> dict[str, Any] | None:
    """Yesterday's top stories, for the 'previously in Miniville' card."""
    if day < 1:
        return None
    rows = conn.execute(
        "SELECT * FROM events WHERE day = ? AND importance >= 2", (day - 1,)).fetchall()
    scored = sorted(((newsworthiness(ev), int(ev["id"]), ev) for ev in rows),
                    key=lambda t: (-t[0], -t[1]))
    items, seen = [], set()
    for score, _id, ev in scored:
        if score < STORY_MIN:
            break
        text = describe(conn, ev)
        if text in seen:
            continue
        seen.add(text)
        items.append({"category": _category(ev), "text": text, "score": score,
                      "agent_id": ev["a_id"], "place_id": ev["place_id"]})
        if len(items) >= limit:
            break
    return {"day": day, "date": seasons.fmt_date(day - 1), "events": len(rows),
            "stories": items}


def storyline_cast(conn: sqlite3.Connection, tick: int, limit: int = 12) -> list[int]:
    """Residents with the most going on lately, most newsworthy first; the
    awake ones before those asleep."""
    totals: dict[int, int] = {}
    for ev in conn.execute(
            """SELECT * FROM events WHERE tick >= ? AND importance >= 2
               AND a_id IS NOT NULL ORDER BY id DESC LIMIT 1500""",
            (max(0, tick - 3 * timekeeper.TICKS_PER_DAY),)).fetchall():
        score = newsworthiness(ev)
        if score < 20:
            continue
        for aid in (ev["a_id"], ev["b_id"]):
            if aid:
                totals[int(aid)] = totals.get(int(aid), 0) + score
    if not totals:
        return []
    ids = list(totals)
    marks = ",".join("?" * len(ids))
    state = {int(r["id"]): r for r in conn.execute(
        f"""SELECT a.id, a.alive, s.activity FROM agents a
            LEFT JOIN agent_state s ON s.agent_id=a.id WHERE a.id IN ({marks})""", ids)}
    cast = [aid for aid in ids if state.get(aid) is not None and state[aid]["alive"]]
    cast.sort(key=lambda aid: (state[aid]["activity"] == "sleep", -totals[aid], aid))
    return cast[:limit]


def _segments(rows, start_tod: int) -> list[dict[str, Any]]:
    """Collapse a resident's remaining plan rows into stops."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if out and out[-1]["place_id"] == r["place_id"] and out[-1]["activity"] == r["activity"]:
            continue
        out.append({"tick_of_day": int(r["tick"]), "place_id": r["place_id"],
                    "activity": r["activity"]})
    return [s for s in out if s["tick_of_day"] > start_tod]


def _today_lines(conn: sqlite3.Connection, rows) -> list[dict[str, Any]]:
    """Today's events as time + text, identical lines merged."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev in rows:
        text = describe(conn, ev)
        if text in seen:
            continue
        seen.add(text)
        out.append({"time": timekeeper.fmt_tick(int(ev["tick"])).split()[-1],
                    "text": text, "score": newsworthiness(ev)})
    return out


def follow_view(conn: sqlite3.Connection, agent_id: int, tick: int,
                places_by_id: dict[int, dict[str, Any]],
                vcoords: dict[int, tuple[float, float]]) -> dict[str, Any] | None:
    """Everything the follow-cam needs about one resident: where they are,
    where they are going next today, and what has happened to them today."""
    row = conn.execute(
        """SELECT a.id, a.name, a.age, a.occupation, a.alive, s.place_id, s.activity,
                  s.mood, hp.district home_district
           FROM agents a LEFT JOIN agent_state s ON s.agent_id=a.id
           LEFT JOIN places hp ON hp.id=a.home_place_id WHERE a.id=?""",
        (agent_id,)).fetchone()
    if not row:
        return None
    place = places_by_id.get(int(row["place_id"])) if row["place_id"] else None
    x, y, label = _place_coords(place, row["home_district"], int(row["id"]), vcoords)
    tod = timekeeper.tick_of_day(tick)
    day_start = tick - tod
    plan = conn.execute(
        "SELECT tick, place_id, activity FROM plans WHERE agent_id=? ORDER BY tick",
        (agent_id,)).fetchall()
    upcoming = []
    for seg in _segments(plan, tod)[:4]:
        p = places_by_id.get(int(seg["place_id"])) if seg["place_id"] else None
        upcoming.append({
            "time": timekeeper.fmt_tick(day_start + seg["tick_of_day"]).split()[-1],
            "activity": seg["activity"],
            "place": (p["name"] if p and p["kind"] != "home" else "home"),
        })
    today = _today_lines(conn, conn.execute(
        """SELECT * FROM events WHERE (a_id=? OR b_id=?) AND tick >= ?
           AND importance >= 2 ORDER BY id DESC LIMIT 8""",
        (agent_id, agent_id, day_start)).fetchall())[:6]
    return {
        "id": int(row["id"]), "name": row["name"], "age": row["age"],
        "alive": bool(row["alive"]),
        "occupation": (row["occupation"] or "resident").replace("_", " "),
        "activity": row["activity"], "mood": row["mood"],
        "place_id": row["place_id"], "place_name": label,
        "x": round(x, 1), "y": round(y, 1),
        "upcoming": upcoming, "today": today,
    }


def follow_venue_view(conn: sqlite3.Connection, place_id: int, tick: int,
                      places_by_id: dict[int, dict[str, Any]],
                      vcoords: dict[int, tuple[float, float]]) -> dict[str, Any] | None:
    """The follow-cam on a venue: who is in, and what happened there today."""
    place = places_by_id.get(int(place_id))
    if not place:
        return None
    x, y, _label = _place_coords(place, place.get("district"), 1, vcoords)
    inside = conn.execute(
        """SELECT a.id, a.name, s.activity, s.mood FROM agent_state s
           JOIN agents a ON a.id=s.agent_id WHERE s.place_id=? AND a.alive=1
           ORDER BY a.standing DESC, a.id LIMIT 12""", (place_id,)).fetchall()
    count = conn.execute(
        "SELECT COUNT(*) n FROM agent_state s JOIN agents a ON a.id=s.agent_id "
        "WHERE s.place_id=? AND a.alive=1", (place_id,)).fetchone()["n"]
    day_start = tick - timekeeper.tick_of_day(tick)
    today = _today_lines(conn, conn.execute(
        """SELECT * FROM events WHERE place_id=? AND tick >= ?
           AND importance >= 2 ORDER BY id DESC LIMIT 8""",
        (place_id, day_start)).fetchall())[:6]
    return {"id": int(place_id), "name": place["name"], "kind": place["kind"],
            "district": place.get("district"), "capacity": place.get("capacity"),
            "x": round(x, 1), "y": round(y, 1), "inside": count,
            "occupants": [dict(o) for o in inside], "today": today}


def tv_director_snapshot(conn: sqlite3.Connection,
                         focus_agent_id: int | None = None,
                         focus_place_id: int | None = None,
                         max_movers: int = 10,
                         since_event_id: int = 0,
                         follow_agent_id: int | None = None,
                         follow_place_id: int | None = None) -> dict[str, Any]:
    """Return the complete payload for the 10-Foot Living TV & Director Mode.

    `since_event_id` is the viewer's cursor: `breaking` only carries stories
    newer than it, so a display cuts in for each piece of news once. The
    follow ids feed the follow-cam (`followed` / `followed_venue`).
    """
    tick = int(get_meta(conn, "tick", "0") or 0)
    day = timekeeper.day_of(tick)
    tod = timekeeper.tick_of_day(tick)
    season = seasons.season_of(day)
    holiday = seasons.holiday_for(conn, day)

    places_by_id = {
        int(r["id"]): dict(r)
        for r in conn.execute("SELECT id, name, kind, district, capacity FROM places").fetchall()
    }

    pop = conn.execute("SELECT COUNT(*) n FROM agents WHERE alive = 1").fetchone()["n"]
    awake = conn.execute(
        "SELECT COUNT(*) n FROM agent_state s JOIN agents a ON a.id = s.agent_id "
        "WHERE a.alive = 1 AND s.activity != 'sleep'"
    ).fetchone()["n"]
    employed = conn.execute(
        "SELECT COUNT(*) n FROM jobs j JOIN agents a ON a.id = j.agent_id WHERE a.alive = 1"
    ).fetchone()["n"]
    treasury_cents = economy.town_balance(conn)

    vcoords = _venue_coords_map(conn)
    movers = _curated_movers(conn, tick, places_by_id, vcoords, max_movers=max_movers)
    spot_res = _spotlight_resident(conn, tick, places_by_id, vcoords, focus_agent_id=focus_agent_id)
    spot_ven = _spotlight_venue(conn, tick, places_by_id, vcoords, focus_place_id=focus_place_id)

    stories = story_scenes(conn, tick, places_by_id, vcoords)
    latest = conn.execute("SELECT COALESCE(MAX(id), 0) n FROM events").fetchone()["n"]
    # breaking news is what the viewer has not seen yet; a first look (cursor
    # 0) gets no backlog of breaking cuts, only the playlist
    breaking = []
    if since_event_id:
        breaking = [sc for sc in story_scenes(conn, tick, places_by_id, vcoords,
                                              since_event_id=since_event_id, limit=4)
                    if sc["breaking"]]

    # Camera target sequence for the auto-director cycle: the day's two best
    # stories, then the featured resident, a busy venue and the panorama
    camera_targets: list[dict[str, Any]] = [dict(sc) for sc in stories[:2]]
    if spot_res:
        camera_targets.append({
            "mode": "resident",
            "title": f"Spotlight: {spot_res['name']}",
            "subtitle": f"{spot_res['archetype']} · {spot_res['activity']} at {spot_res['place_name']}",
            "x": spot_res["x"],
            "y": spot_res["y"],
            "scale": 1.65,
            "agent_id": spot_res["id"],
            "place_id": spot_res["place_id"],
        })
    if spot_ven and spot_ven["inside"] > 0:
        # an empty venue is not a scene (the hospital at 3am)
        camera_targets.append({
            "mode": "venue",
            "title": f"Gathering: {spot_ven['name']}",
            "subtitle": f"{spot_ven['inside']} residents inside · {spot_ven['district']}",
            "x": spot_ven["x"],
            "y": spot_ven["y"],
            "scale": 1.45,
            "agent_id": None,
            "place_id": spot_ven["id"],
        })
    camera_targets.append({
        "mode": "overview",
        "title": "Miniville Panorama",
        "subtitle": f"{len(movers)} highlighted travellers moving across town",
        "x": 800.0,
        "y": 450.0,
        "scale": 1.0,
        "agent_id": None,
        "place_id": None,
    })

    return {
        "tick": tick,
        "day": day + 1,
        "time": timekeeper.fmt_tick(tick),
        "date": seasons.fmt_date(day),
        "season": season,
        "holiday": holiday.name if holiday else None,
        "sky": _sky_state(tod, season),
        "vitality": {
            "population": pop,
            "awake": awake,
            "employed": employed,
            "treasury_dollars": round(treasury_cents / 100),
            "active_movers": len(movers),
        },
        "spotlight_resident": spot_res,
        "spotlight_venue": spot_ven,
        "movers": movers,
        "camera_targets": camera_targets,
        "progression": _progression_cards(conn),
        "ticker": _broadcast_ticker(conn, tick),
        "stories": stories,
        "breaking": breaking,
        "latest_event_id": int(latest),
        "hotspots": hotspots(conn, tick, places_by_id, vcoords),
        "recap": day_recap(conn, day),
        "followed": (follow_view(conn, follow_agent_id, tick, places_by_id, vcoords)
                     if follow_agent_id else None),
        "followed_venue": (follow_venue_view(conn, follow_place_id, tick,
                                             places_by_id, vcoords)
                           if follow_place_id else None),
    }


def tv_step(conn: sqlite3.Connection, ticks: int = 1) -> dict[str, Any]:
    """Advance the simulation by `ticks` (1..6) and return the new TV director snapshot."""
    n = max(1, min(6, int(ticks or 1)))
    seed = get_meta(conn, "seed", "miniville") or "miniville"
    for _ in range(n):
        engine.step(conn, seed)
    return tv_director_snapshot(conn)
