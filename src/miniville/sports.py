"""Inter-District Sports Leagues & Saturday Town Derby (v0.35).

Every Saturday (`day % 7 == 5`), two Miniville districts meet at `Lush Meadow Park`
(or `Miniville Gym` in winter) for a friendly inter-district derby match
(`sports_matches` table):

* **District Teams**:
  - *Downtown Dynamos* (`Downtown`)
  - *Greenhill Wanderers* (`Greenhill`)
  - *Riverside Rowers* (`Riverside`)
  - *The Flats Ironworks* (`The Flats`)
  - *Oakwood Owls* (`Oakwood`)
* **Athletic Vitality & MVP Purse (`run_saturday_derby`)** — participating
  players gain `+4` athletic vitality (`agent_state.vitality`), `+10` fun, and
  `-6` stress. When `town_account >= 200_000`, the match MVP receives a strictly
  conserved `$25` (`2,500` cents) **Derby MVP Purse** from `town_account`
  (`district_derby`), plus `+2` civic standing.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import NOTABLE, emit
from .memory import remember
from .rng import rng_for
from .seasons import season_of
from .timekeeper import day_of

MVP_PURSE_CENTS = 2_500          # $25 conserved purse from town_account
TOWN_DERBY_FLOOR = 200_000

_TEAM_NAMES: dict[str, str] = {
    "Downtown": "Downtown Dynamos",
    "Greenhill": "Greenhill Wanderers",
    "Riverside": "Riverside Rowers",
    "The Flats": "The Flats Ironworks",
    "Oakwood": "Oakwood Owls",
    "Lakeshore": "Lakeshore Mariners",
    "Old Mill Quarter": "Old Mill Hammers",
}


def run_saturday_derby(conn: sqlite3.Connection, tick: int,
                       seed: str = "miniville") -> dict[str, Any] | None:
    """Play a Saturday inter-district sports derby match."""
    day = day_of(tick)
    if day % 7 != 5:
        return None

    already = conn.execute(
        "SELECT 1 FROM sports_matches WHERE day = ? LIMIT 1", (day,)
    ).fetchone()
    if already:
        return None

    players_by_dist: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        """SELECT a.id, a.name, a.age, p.district,
                  COALESCE(s.vitality, 65) AS vitality
           FROM agents a
           JOIN places p ON p.id = a.home_place_id
           JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.age BETWEEN 14 AND 62
             AND p.district IS NOT NULL AND p.district != ''
           ORDER BY COALESCE(s.vitality, 65) DESC, a.id ASC"""
    ).fetchall():
        players_by_dist.setdefault(row["district"], []).append(row)

    districts = [d for d, plist in sorted(players_by_dist.items()) if len(plist) >= 1]
    if len(districts) < 2:
        return None

    r = rng_for(seed, "saturday_derby", day)
    home_d, away_d = r.sample(districts, 2)
    home_roster = players_by_dist[home_d][:5]
    away_roster = players_by_dist[away_d][:5]

    season = season_of(day)
    sport = "basketball" if season == "winter" else ("baseball" if season in ("spring", "summer") else "soccer")
    venue = conn.execute(
        """SELECT id, name FROM places
           WHERE name = ? OR name = 'Lush Meadow Park'
           ORDER BY id ASC LIMIT 1""",
        ("Miniville Gym" if season == "winter" else "Lush Meadow Park",),
    ).fetchone()
    venue_id = int(venue["id"]) if venue else None
    venue_name = venue["name"] if venue else "Lush Meadow Park"

    home_vit = sum(int(p["vitality"]) for p in home_roster) / max(1, len(home_roster))
    away_vit = sum(int(p["vitality"]) for p in away_roster) / max(1, len(away_roster))
    home_score = max(1, int(round(home_vit / 15.0 + r.randint(0, 5))))
    away_score = max(1, int(round(away_vit / 15.0 + r.randint(0, 5))))
    if home_score == away_score:
        home_score += 1

    winning_roster = home_roster if home_score > away_score else away_roster
    losing_roster = away_roster if home_score > away_score else home_roster
    win_d = home_d if home_score > away_score else away_d
    lose_d = away_d if home_score > away_score else home_d
    mvp = winning_roster[0]
    mvp_id = int(mvp["id"])
    capt_other = int(losing_roster[0]["id"])

    all_ids = [int(p["id"]) for p in (home_roster + away_roster)]
    if all_ids:
        ph = ",".join("?" * len(all_ids))
        conn.execute(
            f"""UPDATE agent_state
                SET vitality = MIN(100, COALESCE(vitality, 65) + 4),
                    fun = MIN(100, fun + 10),
                    stress = MAX(0, stress - 6)
                WHERE agent_id IN ({ph})""",
            all_ids,
        )

    purse_paid = 0
    treasury = conn.execute(
        "SELECT balance_cents FROM town_account WHERE id = 1"
    ).fetchone()
    if treasury and int(treasury["balance_cents"]) >= TOWN_DERBY_FLOOR + MVP_PURSE_CENTS:
        conn.execute(
            "UPDATE town_account SET balance_cents = balance_cents - ? WHERE id = 1",
            (MVP_PURSE_CENTS,),
        )
        conn.execute(
            "UPDATE agent_state SET money_cents = money_cents + ? WHERE agent_id = ?",
            (MVP_PURSE_CENTS, mvp_id),
        )
        purse_paid = MVP_PURSE_CENTS

    conn.execute(
        "UPDATE agents SET standing = COALESCE(standing, 0) + 2 WHERE id = ?",
        (mvp_id,),
    )

    win_team = _TEAM_NAMES.get(win_d, f"{win_d} XI")
    lose_team = _TEAM_NAMES.get(lose_d, f"{lose_d} XI")
    hi_score, lo_score = max(home_score, away_score), min(home_score, away_score)
    summary = (
        f"{win_team} defeated {lose_team} {hi_score}–{lo_score} in Saturday {sport} "
        f"at {venue_name} (MVP: {mvp['name']})"
    )

    conn.execute(
        """INSERT INTO sports_matches(
               day, tick, season, sport, home_district, away_district,
               home_score, away_score, mvp_id, purse_cents, summary)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (day, tick, season, sport, home_d, away_d,
         home_score, away_score, mvp_id, purse_paid, summary),
    )

    lo, hi = min(mvp_id, capt_other), max(mvp_id, capt_other)
    if lo != hi:
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,6.0,7.0,0.0,'friend',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.5),
                 affinity = MIN(100.0, relationships.affinity + 4.0),
                 interactions = relationships.interactions + 1,
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )

    emit(conn, tick, "life_event", place_id=venue_id, a=mvp_id, b=capt_other,
         importance=NOTABLE,
         text=f"led {win_team} past {lose_team} ({hi_score}–{lo_score}) in the Saturday {sport} derby at {venue_name}",
         tag="district_derby", sport=sport, mvp=mvp["name"], purse_cents=purse_paid)
    remember(conn, mvp_id, tick,
             f"Named MVP as {win_team} beat {lose_team} {hi_score}–{lo_score} at {venue_name}!",
             kind="personal", importance=4)
    return {
        "day": day,
        "sport": sport,
        "home_district": home_d,
        "away_district": away_d,
        "home_score": home_score,
        "away_score": away_score,
        "mvp_id": mvp_id,
        "mvp_name": mvp["name"],
        "purse_cents": purse_paid,
        "summary": summary,
    }


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    res = run_saturday_derby(conn, tick, seed)
    return {"matches": 1 if res else 0}


def standings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for d, tname in _TEAM_NAMES.items():
        table[d] = {"district": d, "team": tname, "wins": 0, "losses": 0, "points_for": 0}
    for m in conn.execute("SELECT * FROM sports_matches ORDER BY id ASC").fetchall():
        hd, ad = m["home_district"], m["away_district"]
        hs, as_ = int(m["home_score"]), int(m["away_score"])
        table.setdefault(hd, {"district": hd, "team": _TEAM_NAMES.get(hd, hd), "wins": 0, "losses": 0, "points_for": 0})
        table.setdefault(ad, {"district": ad, "team": _TEAM_NAMES.get(ad, ad), "wins": 0, "losses": 0, "points_for": 0})
        table[hd]["points_for"] += hs
        table[ad]["points_for"] += as_
        if hs > as_:
            table[hd]["wins"] += 1
            table[ad]["losses"] += 1
        else:
            table[ad]["wins"] += 1
            table[hd]["losses"] += 1
    return sorted(table.values(), key=lambda x: (-x["wins"], -x["points_for"], x["district"]))


def sports_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    recent = conn.execute(
        """SELECT m.*, a.name AS mvp_name
           FROM sports_matches m
           LEFT JOIN agents a ON a.id = m.mvp_id
           ORDER BY m.id DESC LIMIT 15"""
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) n FROM sports_matches").fetchone()["n"]
    return {
        "total_matches": int(total),
        "standings": standings(conn),
        "recent_matches": [dict(r) for r in recent],
    }
