"""Miniville geography: districts, venues, and occupation->workplace mapping.

Miniville is a fictional town. Venues are abstract social anchors rather than
map coordinates; co-presence at a venue is what creates encounters.
"""
from __future__ import annotations

import sqlite3

DISTRICTS = ["Old Mill Quarter", "Lakeshore", "Greenhill", "Downtown", "The Flats"]

# (name, kind, district, capacity, open_tick, close_tick, tags)
# ticks are half-hours of a day (0-47); 16 = 08:00, 36 = 18:00
VENUES = [
    ("Miniville General Hospital", "workplace", "Downtown", 60, 0, 47, ["health"]),
    ("Miniville School", "workplace", "Greenhill", 80, 13, 34, ["education"]),
    ("Town Hall", "workplace", "Downtown", 30, 16, 34, ["civic", "office"]),
    ("The Quaint Corner Tavern", "public", "Old Mill Quarter", 45, 32, 47, ["food", "drink", "nightlife"]),
    ("The Scenic Bean", "public", "Downtown", 25, 12, 40, ["food", "coffee"]),
    ("Lush Meadow Park", "public", "Lakeshore", 120, 0, 47, ["outdoors", "sport"]),
    ("Miniville Public Library", "public", "Greenhill", 35, 18, 38, ["quiet", "study"]),
    ("Greenhill Gym", "public", "Greenhill", 30, 10, 44, ["sport", "fitness"]),
    ("Miniville Grocer", "workplace", "The Flats", 50, 14, 44, ["retail", "food"]),
    ("Old Mill Shops", "workplace", "Old Mill Quarter", 60, 18, 40, ["retail"]),
    ("Riverside Diner", "workplace", "Lakeshore", 40, 10, 42, ["food"]),
    ("First Congregational Church", "civic", "Greenhill", 100, 16, 44, ["worship"]),
    ("Miniville Gazette", "workplace", "Downtown", 15, 16, 36, ["media", "office"]),
    ("Flats Auto & Hardware", "workplace", "The Flats", 20, 14, 40, ["trades", "retail"]),
    ("Lakeshore Marina", "public", "Lakeshore", 40, 8, 44, ["outdoors", "water"]),
    ("The Bijou Theater", "public", "Downtown", 70, 36, 47, ["arts", "nightlife"]),
    ("Miniville Community Center", "civic", "The Flats", 60, 14, 44, ["community"]),
]

# keyword -> preferred workplace tags; first matching venue wins
OCCUPATION_MAP = [
    (["nurse", "doctor", "physician", "medical", "health", "therapist", "dentist",
      "pharmacist", "paramedic", "clinical", "surgeon", "psych"], ["health"]),
    (["teacher", "professor", "educator", "instructor", "tutor", "librarian",
      "school", "coach"], ["education"]),
    (["police", "fire", "officer", "clerk", "administrator", "government",
      "public", "postal", "inspector", "social_worker", "civil"], ["civic"]),
    (["cook", "chef", "food", "bartender", "server", "waiter", "waitress",
      "restaurant", "barista", "baker", "counter"], ["food"]),
    (["retail", "sales", "cashier", "store", "customer", "merchandise"], ["retail"]),
    (["mechanic", "electrician", "plumber", "carpenter", "construction",
      "technician", "repair", "maintenance", "welder", "hvac", "driver",
      "truck", "laborer"], ["trades"]),
    (["journalist", "reporter", "writer", "editor", "media", "artist",
      "designer", "photographer"], ["media", "arts"]),
]

# fallback workplaces for unmatched occupations
GENERIC_WORKPLACES = ["office", "retail", "civic"]


def create_world(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert venues; returns {venue_name: place_id}."""
    ids = {}
    for name, kind, district, cap, ot, ct, tags in VENUES:
        cur = conn.execute(
            "INSERT INTO places(name, kind, district, capacity, open_tick, close_tick, tags)"
            " VALUES(?,?,?,?,?,?,?)",
            (name, kind, district, cap, ot, ct, __import__("json").dumps(tags)),
        )
        ids[name] = cur.lastrowid
    conn.commit()
    return ids


def workplace_tags_for(occupation: str) -> list[str]:
    occ = (occupation or "").lower()
    for keywords, tags in OCCUPATION_MAP:
        if any(k in occ for k in keywords):
            return tags
    return GENERIC_WORKPLACES
