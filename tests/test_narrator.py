"""Narration provider chain: vw -> hf -> ollama -> raw, and never raise."""
import sqlite3

import pytest

from miniville import db, narrator, world


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    db.init_db(c)
    world.create_world(c)
    c.execute("INSERT INTO places(name,kind,district,capacity) VALUES('H1','home','Downtown',6)")
    home = c.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    c.execute(
        """INSERT INTO agents(uuid,name,sex,age,marital_status,occupation,
           hobbies_json,home_place_id) VALUES('t0','Ada Ashbrook','Female',30,
           'never_married','teacher','[]',?)""", (home,))
    c.execute("INSERT INTO agent_state(agent_id,place_id) VALUES(1,?)", (home,))
    db.set_meta(c, "seed", "test")
    db.set_meta(c, "tick", "0")
    c.commit()
    yield c
    c.close()


def test_raw_provider_never_calls_out(conn, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no provider should be called for raw")
    monkeypatch.setattr(narrator, "_call_vw", boom)
    monkeypatch.setattr(narrator, "_call_hf", boom)
    monkeypatch.setattr(narrator, "_call_ollama", boom)
    text, src = narrator._narrate_line(conn, "a line", "raw", "m", "m")
    assert src == "raw" and text == "a line"


def test_vw_provider_used_when_available(conn, monkeypatch):
    monkeypatch.setattr(narrator, "_call_vw", lambda *a, **k: "polished prose")
    text, src = narrator._narrate_line(conn, "a line", "vw", "m", "m")
    assert src == "vw" and text == "polished prose"


def test_falls_back_to_raw_when_all_providers_fail(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(narrator, "_call_vw", boom)
    monkeypatch.setattr(narrator, "_call_hf", boom)
    monkeypatch.setattr(narrator, "_call_ollama", boom)
    monkeypatch.setattr(narrator, "_load_hf_token", lambda: "token")
    text, src = narrator._narrate_line(conn, "a line", "vw", "m", "m")
    assert src == "raw" and text == "a line"


def test_vw_falls_through_to_hf(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(narrator, "_call_vw", boom)
    monkeypatch.setattr(narrator, "_load_hf_token", lambda: "token")
    monkeypatch.setattr(narrator, "_call_hf", lambda *a, **k: ("hf prose", 0.001))
    text, src = narrator._narrate_line(conn, "a line", "vw", "m", "m")
    assert src == "hf" and text == "hf prose"


def test_spend_report_notes_gateway_state(conn, monkeypatch):
    monkeypatch.setattr(narrator, "vw_budget", lambda *a, **k: None)
    assert "offline" in narrator.spend_report(conn)
    monkeypatch.setattr(narrator, "vw_budget",
                        lambda *a, **k: {"estimated_spend_usd": 0.25,
                                         "monthly_budget_usd": 2.0, "calls": 7})
    report = narrator.spend_report(conn)
    assert "0.2500" in report and "7 calls" in report
