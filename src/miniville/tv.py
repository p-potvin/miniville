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
    prev_plans = {
        int(r["agent_id"]): (r["place_id"], r["activity"])
        for r in conn.execute(
            "SELECT agent_id, place_id, activity FROM plans WHERE tick = ?",
            (max(0, tick - 1),),
        ).fetchall()
    }
    next_plans = {
        int(r["agent_id"]): (r["place_id"], r["activity"])
        for r in conn.execute(
            "SELECT agent_id, place_id, activity FROM plans WHERE tick = ?",
            (tick + 1,),
        ).fetchall()
    }
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
        # Rotate across residents who recently had notable events or active ambitions
        ev_rows = conn.execute(
            """SELECT DISTINCT a_id FROM events
               WHERE a_id IS NOT NULL AND importance >= 2
                 AND kind IN ('life_event', 'milestone', 'romance', 'conflict', 'encounter')
               ORDER BY id DESC LIMIT 16"""
        ).fetchall()
        pool = [int(r["a_id"]) for r in ev_rows if r["a_id"]]
        if not pool:
            pool = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM agents WHERE alive = 1 ORDER BY standing DESC, id ASC LIMIT 16"
                ).fetchall()
            ]
        if pool:
            chosen_id = pool[(tick // 1) % len(pool)]

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
        # Rotate among the top 5 busiest venues
        active_pool = [r for r in crowd_rows if int(r["inside"] or 0) > 0][:5] or crowd_rows[:5]
        chosen = active_pool[(tick // 1) % len(active_pool)]

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
        items.append({
            "badge": kind_badge,
            "text": describe(conn, e),
            "agent_id": e["a_id"],
            "place_id": e["place_id"],
            "time": timekeeper.fmt_tick(int(e["tick"])),
        })

    return items[:12]


def tv_director_snapshot(conn: sqlite3.Connection,
                         focus_agent_id: int | None = None,
                         focus_place_id: int | None = None,
                         max_movers: int = 10) -> dict[str, Any]:
    """Return the complete payload for the 10-Foot Living TV & Director Mode."""
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

    # Camera target sequence for the auto-director cycle
    camera_targets: list[dict[str, Any]] = []
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
    if spot_ven:
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
    }


def tv_step(conn: sqlite3.Connection, ticks: int = 1) -> dict[str, Any]:
    """Advance the simulation by `ticks` (1..6) and return the new TV director snapshot."""
    n = max(1, min(6, int(ticks or 1)))
    seed = get_meta(conn, "seed", "miniville") or "miniville"
    for _ in range(n):
        engine.step(conn, seed)
    return tv_director_snapshot(conn)
