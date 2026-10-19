"""The CLI's ledger/clock check.

A probe that writes into the live DB, or a restore that rolls the clock back,
leaves events dated after "now". The ledger is supposed to be the town's
coherent history, so `status` says so out loud.
"""
import types

from miniville import cli, db, world


def _args(db_path):
    return types.SimpleNamespace(db=str(db_path))


def _town(path):
    conn = db.connect(path)
    world.create_world(conn)
    conn.execute("INSERT INTO places(name,kind,district,capacity) "
                 "VALUES('H1','home','Downtown',6)")
    home = conn.execute("SELECT id FROM places WHERE name='H1'").fetchone()["id"]
    conn.execute(
        """INSERT INTO agents(uuid,name,sex,age,is_child,marital_status,occupation,
           hobbies_json,home_place_id) VALUES('t1','Ada Ashbrook','Female',30,0,
           'never_married','clerk','[]',?)""", (home,))
    conn.execute("INSERT INTO agent_state(agent_id,place_id,money_cents) VALUES(1,?,1000)",
                 (home,))
    db.set_meta(conn, "seed", "test")
    db.set_meta(conn, "tick", "0")
    return conn


def test_status_is_quiet_when_the_ledger_matches_the_clock(tmp_path, capsys):
    conn = _town(tmp_path / "cli.db")
    conn.execute("INSERT INTO events(tick,day,kind,importance,data) "
                 "VALUES(0,0,'town_event',2,'{\"tag\": \"weather\"}')")
    conn.commit()
    conn.close()
    cli.cmd_status(_args(tmp_path / "cli.db"))
    assert "ledger/clock mismatch" not in capsys.readouterr().out


def test_status_flags_events_dated_after_the_clock(tmp_path, capsys):
    conn = _town(tmp_path / "cli.db")
    # an event from the future: exactly what a stray probe leaves behind
    conn.execute("INSERT INTO events(tick,day,kind,importance,data) "
                 "VALUES(4800,100,'town_event',2,'{\"tag\": \"motion_rejected\"}')")
    conn.commit()
    conn.close()
    cli.cmd_status(_args(tmp_path / "cli.db"))
    out = capsys.readouterr().out
    assert "ledger/clock mismatch" in out
    assert "1 events" in out
