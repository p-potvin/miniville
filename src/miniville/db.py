"""SQLite persistence layer. One file = one world."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("MINIVILLE_DB", "data/miniville.db"))

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Static persona facts (from the dataset + generated attributes)
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE,
    name TEXT NOT NULL,
    sex TEXT,
    age INTEGER,
    birth_year INTEGER,
    marital_status TEXT,
    education_level TEXT,
    occupation TEXT,
    origin_city TEXT,
    origin_state TEXT,
    persona TEXT,
    professional_persona TEXT,
    hobbies_json TEXT,
    skills_json TEXT,
    traits_json TEXT,
    household_id INTEGER,
    home_place_id INTEGER,
    work_place_id INTEGER,
    alive INTEGER NOT NULL DEFAULT 1,
    is_child INTEGER NOT NULL DEFAULT 0
);

-- Mutable per-agent state, updated every tick
CREATE TABLE IF NOT EXISTS agent_state (
    agent_id INTEGER PRIMARY KEY REFERENCES agents(id),
    energy REAL NOT NULL DEFAULT 80,
    hunger REAL NOT NULL DEFAULT 80,
    social REAL NOT NULL DEFAULT 60,
    fun REAL NOT NULL DEFAULT 60,
    stress REAL NOT NULL DEFAULT 20,
    money_cents INTEGER NOT NULL DEFAULT 500000,
    mood TEXT NOT NULL DEFAULT 'content',
    place_id INTEGER,
    activity TEXT NOT NULL DEFAULT 'idle'
);

CREATE TABLE IF NOT EXISTS households (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    home_place_id INTEGER
);

CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,          -- home | workplace | public | civic
    district TEXT NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 40,
    open_tick INTEGER NOT NULL DEFAULT 0,   -- tick-of-day opens (0-47)
    close_tick INTEGER NOT NULL DEFAULT 47, -- tick-of-day closes
    tags TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS jobs (
    agent_id INTEGER PRIMARY KEY REFERENCES agents(id),
    place_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    wage_cents INTEGER NOT NULL,
    shift_start INTEGER NOT NULL,   -- tick-of-day
    shift_end INTEGER NOT NULL,
    work_days INTEGER NOT NULL DEFAULT 62   -- bitmask Mon..Sun, 62 = Mon-Fri
);

-- Current-day plan (rebuilt each day): agent_id -> tick -> place_id
CREATE TABLE IF NOT EXISTS plans (
    agent_id INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    place_id INTEGER NOT NULL,
    activity TEXT NOT NULL,
    PRIMARY KEY (agent_id, tick)
);

-- Relationship graph, canonical a_id < b_id
CREATE TABLE IF NOT EXISTS relationships (
    a_id INTEGER NOT NULL,
    b_id INTEGER NOT NULL,
    familiarity REAL NOT NULL DEFAULT 0,   -- 0..100, grows with contact
    affinity REAL NOT NULL DEFAULT 0,      -- -100..100, like/dislike
    romance REAL NOT NULL DEFAULT 0,       -- 0..100
    label TEXT NOT NULL DEFAULT 'stranger',
    interactions INTEGER NOT NULL DEFAULT 0,
    last_met_tick INTEGER NOT NULL DEFAULT -1,
    PRIMARY KEY (a_id, b_id)
);

-- Append-only event ledger; data_json holds structured payload
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    day INTEGER NOT NULL,
    kind TEXT NOT NULL,
    place_id INTEGER,
    a_id INTEGER,
    b_id INTEGER,
    importance INTEGER NOT NULL DEFAULT 1,  -- 1 trivial .. 5 historic
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(day);
CREATE INDEX IF NOT EXISTS idx_events_agents ON events(a_id, b_id);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

CREATE TABLE IF NOT EXISTS chronicle (
    day INTEGER PRIMARY KEY,
    text TEXT NOT NULL
);

-- Authored prose per day, written by an LLM narrator (in-session agent,
-- local Ollama, or HF inference). source tags who wrote it.
CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day INTEGER NOT NULL,
    source TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_narratives_day ON narratives(day);

-- Temporary agent conditions (illness etc.) that override plans
CREATE TABLE IF NOT EXISTS conditions (
    agent_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    until_tick INTEGER NOT NULL,
    PRIMARY KEY (agent_id, kind)
);
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)  # idempotent: keeps existing DBs up to date
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
