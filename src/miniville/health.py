"""Personal health, athletic vitality, and hospital medical rounds.

Miniville has Miniville General Hospital (open 24/7), employed physicians and
nurses (`jobs`), Greenhill Gym, Lush Meadow Park, `conditions(kind='sick')`,
and Gompertz aging hazard (`mortality.py`). This module connects them:

* **Athletic & Outdoor Vitality (`update_vitality`)** — residents who exercise
  at Greenhill Gym, Lush Meadow Park, or Lakeshore Marina (or hold athletic /
  outdoor habits and low stress) build `agent_state.vitality` (`0..100`), while
  illness or chronic high stress erodes it.
* **Hospital Medical Rounds (`hospital_rounds`)** — on-duty clinicians at
  Miniville General Hospital treat sick residents (`medical_treatment` life
  event, cutting remaining illness duration by 18 ticks and restoring `+6`
  vitality) and perform preventive `wellness_checkup` visits for seniors and
  low-vitality neighbours.
* **Longevity Payoff (`vitality_hazard_multiplier`)** — high vitality (`>= 75`)
  buffers `mortality.daily_hazard` by up to `-20%`, while depleted vitality
  (`< 30`) increases hazard slightly, rewarding active small-town lifestyles.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .events import MINOR, emit
from .memory import remember
from .rng import rng_for
from .timekeeper import day_of

TREATMENT_TICK_REDUCTION = 18


def vitality_hazard_multiplier(vitality: float | None) -> float:
    """Multiplier in `[0.80, 1.20]` applied to Gompertz daily mortality hazard."""
    v = float(60.0 if vitality is None else vitality)
    return max(0.80, min(1.20, 1.0 - 0.004 * (v - 55.0)))


def update_vitality(conn: sqlite3.Connection, tick: int) -> int:
    """Daily vitality drift with mean-reversion so athletes sit ~75-88 and
    sedentary/stressed residents sit ~45-62."""
    conn.execute(
        """UPDATE agent_state
           SET vitality = MAX(15.0, MIN(100.0, ROUND(
               COALESCE(vitality, 60.0)
               - 0.04 * (COALESCE(vitality, 60.0) - 60.0)
               + (CASE
                    WHEN EXISTS (
                        SELECT 1 FROM conditions c
                        WHERE c.agent_id = agent_state.agent_id AND c.kind = 'sick'
                    ) THEN -2.0
                    WHEN stress > 65 OR energy < 32 THEN -1.0
                    WHEN energy >= 70 AND stress <= 30 THEN 0.35
                    ELSE 0.0
                  END), 2)))"""
    )
    cur = conn.execute(
        """UPDATE agent_state
           SET vitality = MIN(100.0, ROUND(vitality + 0.85, 2))
           WHERE agent_id IN (
               SELECT id FROM agents
               WHERE alive = 1
                 AND (hobbies_json LIKE '%run%' OR hobbies_json LIKE '%gym%'
                      OR hobbies_json LIKE '%sport%' OR hobbies_json LIKE '%hik%'
                      OR hobbies_json LIKE '%swim%' OR hobbies_json LIKE '%cycl%')
           )"""
    )
    return cur.rowcount


def hospital_rounds(conn: sqlite3.Connection, tick: int,
                    seed: str = "miniville") -> dict[str, int]:
    """Clinicians at Miniville General Hospital treat sick residents and run checkups."""
    day = day_of(tick)
    r = rng_for(seed, "hospital", day)

    hosp = conn.execute(
        "SELECT id, name FROM places WHERE name = 'Miniville General Hospital'"
    ).fetchone()
    if not hosp:
        return {"treated": 0, "checkups": 0}
    hid = int(hosp["id"])

    clinicians = conn.execute(
        """SELECT j.agent_id AS id, a.name, j.role
           FROM jobs j JOIN agents a ON a.id = j.agent_id
           WHERE j.place_id = ? AND a.alive = 1
           ORDER BY j.rank DESC, j.agent_id ASC""",
        (hid,),
    ).fetchall()
    if not clinicians:
        # Fallback to any living resident with medical occupation
        clinicians = conn.execute(
            """SELECT id, name, occupation AS role FROM agents
               WHERE alive = 1 AND is_child = 0
                 AND (occupation LIKE '%nurse%' OR occupation LIKE '%doctor%'
                      OR occupation LIKE '%physician%' OR occupation LIKE '%medical%'
                      OR occupation LIKE '%paramedic%')
               ORDER BY id LIMIT 4"""
        ).fetchall()
    if not clinicians:
        return {"treated": 0, "checkups": 0}

    sick_patients = conn.execute(
        """SELECT c.agent_id, c.until_tick, a.name
           FROM conditions c JOIN agents a ON a.id = c.agent_id
           WHERE c.kind = 'sick' AND a.alive = 1
           ORDER BY c.until_tick DESC, c.agent_id ASC LIMIT 3"""
    ).fetchall()

    treated = checkups = 0
    for idx, pat in enumerate(sick_patients):
        if idx >= len(clinicians):
            break
        doc = clinicians[idx % len(clinicians)]
        did = int(doc["id"])
        pid = int(pat["agent_id"])
        if did == pid:
            continue
        new_until = max(tick + 4, int(pat["until_tick"]) - TREATMENT_TICK_REDUCTION)
        conn.execute(
            "UPDATE conditions SET until_tick = ? WHERE agent_id = ? AND kind = 'sick'",
            (new_until, pid),
        )
        conn.execute(
            """UPDATE agent_state
               SET vitality = MIN(100.0, COALESCE(vitality, 60.0) + 6.0),
                   stress = MAX(0, stress - 10),
                   energy = MIN(100, energy + 8)
               WHERE agent_id = ?""",
            (pid,),
        )
        lo, hi = min(did, pid), max(did, pid)
        conn.execute(
            """INSERT INTO relationships(a_id,b_id,familiarity,affinity,romance,
                   label,interactions,last_met_tick)
               VALUES(?,?,6.0,8.0,0.0,'acquaintance',1,?)
               ON CONFLICT(a_id,b_id) DO UPDATE SET
                 familiarity = MIN(100.0, relationships.familiarity + 3.0),
                 affinity = MIN(100.0, relationships.affinity + 5.0),
                 last_met_tick = excluded.last_met_tick""",
            (lo, hi, tick),
        )
        emit(conn, tick, "life_event", place_id=hid, a=did, b=pid,
             importance=MINOR,
             text=f"treated {pat['name']} at Miniville General Hospital, speeding their recovery",
             tag="medical_treatment")
        remember(conn, pid, tick,
                 f"{doc['name']} treated me at Miniville General Hospital and helped me recover.",
                 kind="personal", importance=3)
        treated += 1

    # Preventive wellness checkup for a senior or low-vitality resident
    if r.random() < 0.45:
        candidates = conn.execute(
            """SELECT a.id, a.name, a.age, s.vitality
               FROM agents a JOIN agent_state s ON s.agent_id = a.id
               WHERE a.alive = 1 AND a.is_child = 0
                 AND (a.age >= 60 OR COALESCE(s.vitality, 60.0) < 50.0)
               ORDER BY COALESCE(s.vitality, 60.0) ASC, a.age DESC LIMIT 6"""
        ).fetchall()
        if candidates:
            pat = r.choice(candidates)
            doc = r.choice(clinicians)
            did, pid = int(doc["id"]), int(pat["id"])
            if did != pid:
                conn.execute(
                    """UPDATE agent_state
                       SET vitality = MIN(100.0, COALESCE(vitality, 60.0) + 8.0),
                           stress = MAX(0, stress - 6)
                       WHERE agent_id = ?""",
                    (pid,),
                )
                emit(conn, tick, "life_event", place_id=hid, a=did, b=pid,
                     importance=MINOR,
                     text=f"completed a preventive wellness checkup for {pat['name']} at Miniville General Hospital",
                     tag="wellness_checkup")
                checkups += 1

    return {"treated": treated, "checkups": checkups}


def due(conn: sqlite3.Connection, tick: int,
        seed: str = "miniville") -> dict[str, int]:
    update_vitality(conn, tick)
    rounds = hospital_rounds(conn, tick, seed)
    return rounds


def health_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT AVG(COALESCE(s.vitality, 60.0)) AS avg_vitality,
                  SUM(CASE WHEN COALESCE(s.vitality, 60.0) >= 75.0 THEN 1 ELSE 0 END) AS high_vitality,
                  SUM(CASE WHEN COALESCE(s.vitality, 60.0) < 45.0 THEN 1 ELSE 0 END) AS low_vitality
           FROM agent_state s JOIN agents a ON a.id = s.agent_id
           WHERE a.alive = 1"""
    ).fetchone()
    sick_cnt = conn.execute(
        """SELECT COUNT(*) n FROM conditions c JOIN agents a ON a.id = c.agent_id
           WHERE c.kind = 'sick' AND a.alive = 1"""
    ).fetchone()["n"]
    hosp_staff = conn.execute(
        """SELECT COUNT(*) n FROM jobs j JOIN places p ON p.id = j.place_id
           JOIN agents a ON a.id = j.agent_id
           WHERE p.name = 'Miniville General Hospital' AND a.alive = 1"""
    ).fetchone()["n"]
    treatments = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE data LIKE ?", ('%"medical_treatment"%',)
    ).fetchone()["n"]
    checkups = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE data LIKE ?", ('%"wellness_checkup"%',)
    ).fetchone()["n"]
    top_seniors = conn.execute(
        """SELECT a.id, a.name, a.age, ROUND(COALESCE(s.vitality, 60.0), 1) AS vitality
           FROM agents a JOIN agent_state s ON s.agent_id = a.id
           WHERE a.alive = 1 AND a.age >= 55
           ORDER BY s.vitality DESC, a.age DESC LIMIT 6"""
    ).fetchall()
    return {
        "avg_vitality": round(float(row["avg_vitality"] or 60.0), 1),
        "high_vitality": int(row["high_vitality"] or 0),
        "low_vitality": int(row["low_vitality"] or 0),
        "currently_sick": sick_cnt,
        "hospital_clinicians": hosp_staff,
        "treatments_total": treatments,
        "checkups_total": checkups,
        "vital_seniors": [dict(r) for r in top_seniors],
    }
