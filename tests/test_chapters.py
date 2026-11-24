"""Tests for Multi-Step Resident Life Chapters & Goal Chains (v0.64)."""
import sqlite3

from miniville import chapters, db, world


def _build_town(n_agents: int = 20) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    places = world.create_world(c)
    home_id = next(iter(places.values()))
    c.execute("INSERT INTO households(name, home_place_id) VALUES('House 1', ?)", (home_id,))
    hh_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])

    for i in range(1, n_agents + 1):
        c.execute(
            """INSERT INTO agents(
                   id, uuid, name, sex, age, is_child, marital_status, occupation,
                   skills_json, household_id, home_place_id, work_place_id, alive, standing
               ) VALUES (?, ?, ?, 'Female', ?, 0, 'married', 'artisan', '["carpentry","painting"]', ?, ?, ?, 1, ?)""",
            (i, f"u-{i}", f"Resident {i}", 26 + i, hh_id, home_id, places["Old Mill Shops"], 15 + i),
        )
        c.execute(
            "INSERT INTO agent_state(agent_id, place_id, money_cents, fun, stress) VALUES (?, ?, 160000, 60, 25)",
            (i, home_id),
        )
    c.execute("UPDATE town_account SET balance_cents = 500000 WHERE id = 1")
    c.commit()
    return c


def _total_money(conn: sqlite3.Connection) -> int:
    wallets = conn.execute("SELECT COALESCE(SUM(money_cents), 0) AS s FROM agent_state").fetchone()["s"]
    purse = conn.execute("SELECT balance_cents FROM town_account WHERE id = 1").fetchone()["balance_cents"]
    return int(wallets) + int(purse)


def test_seeding_progression_completion_and_zero_money_change():
    conn = _build_town(20)
    try:
        chapters.ensure_chapters_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(life_chapters)")}
        assert {
            "id", "agent_id", "arc_type", "title", "stage", "max_stage",
            "status", "started_day", "updated_day", "completed_day", "progress_note",
        }.issubset(cols)

        initial_money = _total_money(conn)
        total_advanced = 0
        total_completed = 0

        for day in range(60):
            res = chapters.daily_chapters(conn, day, "chap_seed")
            total_advanced += res["advanced"]
            total_completed += res["completed"]
            assert _total_money(conn) == initial_money
            active_now = conn.execute(
                "SELECT COUNT(*) AS n FROM life_chapters WHERE status = 'active'"
            ).fetchone()["n"]
            assert active_now <= chapters.MAX_ACTIVE_CHAPTERS

        assert total_advanced > 0
        assert total_completed > 0

        summary = chapters.chapters_summary(conn)
        assert summary["completed_count"] == total_completed
        assert summary["active_count"] <= chapters.MAX_ACTIVE_CHAPTERS

        # Events logged
        milestones = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind = 'chapter_milestone'"
        ).fetchone()["n"]
        completions = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind = 'chapter_completed' AND importance = 4"
        ).fetchone()["n"]
        assert milestones == total_advanced
        assert completions == total_completed

        # chapters_of helper
        some_aid = summary["chapters"][0]["agent_id"]
        assert len(chapters.chapters_of(conn, some_aid)) >= 1
    finally:
        conn.close()


def test_deceased_cleanup():
    conn = _build_town(10)
    try:
        chapters.daily_chapters(conn, 0, "dead_seed")
        active = chapters.list_chapters(conn, status="active", limit=10)
        assert len(active) > 0
        victim_id = active[0]["agent_id"]

        conn.execute("UPDATE agents SET alive = 0 WHERE id = ?", (victim_id,))
        res = chapters.daily_chapters(conn, 1, "dead_seed")
        assert res["closed"] == 1

        closed = [c for c in chapters.chapters_of(conn, victim_id) if c["status"] == "closed"]
        assert len(closed) == 1
    finally:
        conn.close()


def test_determinism_and_idempotency():
    c1 = _build_town(18)
    c2 = _build_town(18)
    try:
        for day in range(40):
            r1 = chapters.daily_chapters(c1, day, "det_chap")
            r2 = chapters.daily_chapters(c2, day, "det_chap")
            assert r1 == r2

        assert chapters.list_chapters(c1) == chapters.list_chapters(c2)
        assert chapters.chapters_summary(c1) == chapters.chapters_summary(c2)

        before = chapters.chapters_summary(c1)
        again = chapters.daily_chapters(c1, 15, "det_chap")
        assert again["already_ran"] is True
        assert chapters.chapters_summary(c1) == before
    finally:
        c1.close()
        c2.close()
