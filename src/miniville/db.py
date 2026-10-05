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
    birth_day INTEGER,
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

-- Per-resident memory stream (Generative Agents pattern). embedding is an
-- optional packed float32 vector used for relevance scoring at retrieval time.
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    day INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'event',
    text TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 1,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent_id, tick);
CREATE INDEX IF NOT EXISTS idx_memories_day ON memories(day);

-- household membership is queried per household (rent, downsizing,
-- adoption): at thousands of households each unindexed lookup is a full
-- agents scan, which made weekly rent the slowest tick in the engine
CREATE INDEX IF NOT EXISTS idx_agents_household ON agents(household_id);

-- Weekly newspaper front pages, distilled from the chronicle
CREATE TABLE IF NOT EXISTS newspapers (
    week INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_tick INTEGER NOT NULL DEFAULT 0
);

-- Open favors owed between residents (small-town IOUs)
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debtor_id INTEGER NOT NULL,
    creditor_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    repaid_tick INTEGER
);
CREATE INDEX IF NOT EXISTS idx_debts_open ON debts(debtor_id, creditor_id)
    WHERE repaid_tick IS NULL;

-- One row per town business (every non-home venue). Commercial businesses live
-- or die on customer traffic; public-service venues are funded by the town.
CREATE TABLE IF NOT EXISTS businesses (
    place_id INTEGER PRIMARY KEY REFERENCES places(id),
    status TEXT NOT NULL DEFAULT 'open',       -- open | closed
    balance_cents INTEGER NOT NULL DEFAULT 0,  -- running profit/loss
    revenue_today INTEGER NOT NULL DEFAULT 0,
    payroll_today INTEGER NOT NULL DEFAULT 0,
    traffic_today INTEGER NOT NULL DEFAULT 0,
    ema_traffic REAL NOT NULL DEFAULT 0,       -- 14-day moving average
    revenue_total INTEGER NOT NULL DEFAULT 0,
    payroll_total INTEGER NOT NULL DEFAULT 0,
    price_index REAL NOT NULL DEFAULT 1.0,     -- drifts up when the business bleeds
    opened_tick INTEGER NOT NULL DEFAULT 0,
    closed_tick INTEGER,
    reopen_day INTEGER,                        -- set by shocks; overrides closed_tick math
    last_settled_day INTEGER NOT NULL DEFAULT -1
);

-- The town's own purse. Weekly rent and the non-rebated part of the business
-- levy flow in; public-service payroll (hospital, school, town hall, library,
-- church, park) flows out. Before this existed, rent was destroyed outright
-- and public wages were minted, so the money supply drained about 8% every
-- two months and the wage index deflated with it.
CREATE TABLE IF NOT EXISTS town_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance_cents INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO town_account(id, balance_cents) VALUES (1, 0);

-- Daily economic time series, written once per simulated day
CREATE TABLE IF NOT EXISTS economy_days (
    day INTEGER PRIMARY KEY,
    revenue_cents INTEGER NOT NULL DEFAULT 0,
    payroll_cents INTEGER NOT NULL DEFAULT 0,
    rent_cents INTEGER NOT NULL DEFAULT 0,
    spending_cents INTEGER NOT NULL DEFAULT 0,
    money_supply_cents INTEGER NOT NULL DEFAULT 0,
    unemployment_bp INTEGER NOT NULL DEFAULT 0,   -- basis points
    businesses_open INTEGER NOT NULL DEFAULT 0,
    businesses_closed INTEGER NOT NULL DEFAULT 0,
    wage_index REAL NOT NULL DEFAULT 1.0
);

-- Household rent arrears, so a family can miss one payment before downsizing
CREATE TABLE IF NOT EXISTS rent_arrears (
    household_id INTEGER PRIMARY KEY REFERENCES households(id),
    missed_payments INTEGER NOT NULL DEFAULT 0,
    last_missed_day INTEGER NOT NULL DEFAULT -1
);

-- Operator-injected shocks (god mode). A row is written when the shock is
-- injected; applied flips once it has landed. detail holds per-kind payload
-- (festival window, reopen day for disasters).
CREATE TABLE IF NOT EXISTS shocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- closure | fire | festival
    place_id INTEGER,
    day INTEGER NOT NULL,            -- day index the shock lands on
    tick INTEGER NOT NULL,           -- tick it was injected at
    applied INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '{}'
);
"""


def snapshot_to(src_path: str | Path, dest_path: str | Path) -> Path:
    """Consistent copy of a live database, WAL and all.

    The town runs in WAL mode, so a plain file copy takes the main file
    without the -wal that holds the newest commits: the copy can be missing
    recent writes or, if a checkpoint is mid-flight, corrupt ("never used"
    pages). SQLite's backup API reads through the WAL and writes a clean
    database instead. A soak copy taken right after a migration came out
    malformed this way.
    """
    src = sqlite3.connect(str(src_path))
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()
    return Path(dest_path)


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
    _migrate(conn)
    conn.commit()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _migrate(conn: sqlite3.Connection) -> None:
    """Guarded column migrations — safe on every connect."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)")}
    if "avatar_path" not in cols:
        # portrait path filled later by the ColONEL-KFC/ComfyUI pipeline
        conn.execute("ALTER TABLE agents ADD COLUMN avatar_path TEXT")
    if "birth_day" not in cols:
        conn.execute("ALTER TABLE agents ADD COLUMN birth_day INTEGER")

    # shocks can schedule a venue's reopening day (fire repairs take as long as
    # they take, not the market's 21 days)
    if _has_table(conn, "businesses"):
        bcols = {r["name"] for r in conn.execute("PRAGMA table_info(businesses)")}
        if "reopen_day" not in bcols:
            conn.execute("ALTER TABLE businesses ADD COLUMN reopen_day INTEGER")

    # the economy rescalings need the meta table; hand-built or legacy DBs
    # (which the tests use) may not have it yet
    if not _has_table(conn, "meta"):
        return

    # Economy v1 rescaled wages from a toy range ($22-$90/shift) to one that
    # rent, meals and shopping can be priced against. Existing worlds keep their
    # relative position: wages and balances scale by the same factor, chosen so
    # a working household roughly covers its cost of living rather than
    # inflating. Flagged so it can only ever run once per world.
    if not get_meta(conn, "economy_v1"):
        if _has_table(conn, "jobs"):
            conn.execute("UPDATE jobs SET wage_cents = CAST(wage_cents * 2.0 AS INTEGER)")
            conn.execute(
                "UPDATE agent_state SET money_cents = MAX(0, CAST(money_cents * 2.0 AS INTEGER))")
        set_meta(conn, "economy_v1", "1")

    # Economy v2: wages are now paid for days actually worked (Mon-Fri) rather
    # than every day of the week, so a day's wage was raised by 7/5 to keep a
    # worker's weekly income — and therefore the whole balance of the town —
    # exactly where it was. Wages end up $62-$252 a day.
    if not get_meta(conn, "economy_v2"):
        if _has_table(conn, "jobs"):
            conn.execute("UPDATE jobs SET wage_cents = CAST(wage_cents * 1.4 AS INTEGER)")
        set_meta(conn, "economy_v2", "1")


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
