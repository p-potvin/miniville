"""Memory stream: append, retrieve, reflect, and graceful embedding fallback."""
import sqlite3

import pytest

from miniville import db, memory, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    for i, name in enumerate(["Ada Ashbrook", "Ben Briar"]):
        c.execute(
            """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
               hobbies_json,home_place_id) VALUES(?,?,?,?,?,?,?,?)""",
            (f"t{i}", name, "Female", 30, "never_married", "teacher", '["Reading"]', home))
        c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(?,?)",
                  (i + 1, home))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_remember_and_retrieve(conn):
    memory.remember(conn, 1, 0, "saw a heron by the lake", importance=2)
    memory.remember(conn, 1, 10, "won the pie contest", importance=5)
    conn.commit()
    top = memory.retrieve(conn, 1, k=2, now_tick=10)
    assert len(top) == 2
    # the recent, important memory outranks the older, trivial one
    assert "pie contest" in top[0]["text"]


def test_retrieve_is_per_agent(conn):
    memory.remember(conn, 1, 0, "Ada's private thought", importance=3)
    memory.remember(conn, 2, 0, "Ben's private thought", importance=3)
    conn.commit()
    assert all("Ada" in m["text"] for m in memory.retrieve(conn, 1, k=5))


def test_record_event_memories_is_idempotent(conn):
    from miniville.events import emit
    emit(conn, 5, "chat", a=1, b=2, importance=2, text="chatted at the cafe")
    conn.commit()
    first = memory.record_event_memories(conn, 5)
    assert first == 2                      # one memory for each participant
    assert memory.record_event_memories(conn, 5) == 0   # nothing new to fold in


def test_reflect_creates_reflection_memory(conn):
    for i in range(4):
        memory.remember(conn, 1, i, f"day {i} happening", importance=3)
    conn.commit()
    text = memory.reflect(conn, 1, day=3)
    assert text and "Ada Ashbrook" in text
    kinds = {r["kind"] for r in conn.execute(
        "SELECT kind FROM memories WHERE agent_id=1")}
    assert "reflection" in kinds


def test_reflect_needs_enough_memories(conn):
    memory.remember(conn, 1, 0, "only one thing happened", importance=3)
    conn.commit()
    assert memory.reflect(conn, 1, day=0) is None


def test_embed_text_degrades_without_gateway(monkeypatch):
    monkeypatch.setattr(memory, "VW_EMBED_URL",
                        "http://127.0.0.1:9/v1/embeddings")
    assert memory.embed_text("hello", timeout=1) is None


def test_memory_digest_renders(conn):
    memory.remember(conn, 1, 0, "a small thing", importance=1)
    conn.commit()
    assert "a small thing" in memory.memory_digest(conn, 1)


def test_event_memory_uses_event_tick(conn):
    from miniville.events import emit

    emit(conn, 7, "chat", a=1, b=2, importance=2, text="a dated conversation")
    conn.commit()
    memory.record_event_memories(conn, 100)

    rows = conn.execute(
        "SELECT tick,day FROM memories ORDER BY agent_id").fetchall()
    assert [tuple(row) for row in rows] == [(7, 0), (7, 0)]
