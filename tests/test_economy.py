"""Tests for the economy: rent, prices, business books, wages, scarcity."""
from __future__ import annotations

import sqlite3

import pytest

from miniville import db as mvdb
from miniville import economy
from miniville.economy import (
    FAIL_THRESHOLD_CENTS,
    GROCERY_CENTS,
    GROCERY_TICK,
    RENT_BY_DISTRICT,
    REOPEN_AFTER_DAYS,
    venue_price,
)
from miniville.events import emit
from miniville.timekeeper import TICKS_PER_DAY
from miniville.world import create_world

DAY = TICKS_PER_DAY


def _world() -> sqlite3.Connection:
    conn = mvdb.connect(":memory:")
    create_world(conn)
    # homes are normally created by the ingest pass, so the tests make their own
    for district in ("Downtown", "The Flats"):
        for i in range(3):
            conn.execute(
                """INSERT INTO places(name,kind,district,capacity,open_tick,
                                      close_tick,tags)
                   VALUES(?,?,?,?,0,47,'[]')""",
                (f"{district} Home {i}", "home", district, 6))
    economy.ensure_businesses(conn)
    return conn


def _place(conn, name: str) -> int:
    return conn.execute("SELECT id FROM places WHERE name=?", (name,)).fetchone()["id"]


def _add_adult(conn, aid: int, home: int, money: int, household: int | None = None):
    conn.execute(
        """INSERT INTO agents(id,name,age,sex,marital_status,home_place_id,
                              household_id,is_child,occupation)
           VALUES(?,?,?,?,?,?,?,0,?)""",
        (aid, f"Resident {aid}", 30, "Female", "never_married", home,
         household, "clerk"))
    conn.execute(
        "INSERT INTO agent_state(agent_id, place_id, money_cents) VALUES(?,?,?)",
        (aid, home, money))
    return aid


def _add_household(conn, hid: int, home: int, name: str = "Test Household"):
    conn.execute("INSERT INTO households(id,name,home_place_id) VALUES(?,?,?)",
                 (hid, name, home))


# --- prices -----------------------------------------------------------------


def test_venue_price_by_activity_and_tags():
    assert venue_price({"food"}, "eat_out") == economy.DINING_BASE_CENTS
    assert venue_price({"food", "drink"}, "eat_out") > venue_price({"food"}, "eat_out")
    assert venue_price({"food", "coffee"}, "eat_out") < venue_price({"food"}, "eat_out")
    assert venue_price({"retail"}, "shopping") == economy.SHOPPING_BASE_CENTS
    assert venue_price({"retail", "trades"}, "shopping") > venue_price({"retail"}, "shopping")
    assert venue_price({"outdoors"}, "leisure") == 0        # the park is free
    assert venue_price({"coffee"}, "leisure") > 0
    assert venue_price({"food"}, "eat") == GROCERY_CENTS
    # the venue's price index scales everything
    assert venue_price({"food"}, "eat_out", 1.5) == int(round(
        economy.DINING_BASE_CENTS * 1.5))


def test_rent_varies_by_district():
    conn = _world()
    downtown = _place(conn, "Town Hall")          # Downtown
    flats = _place(conn, "Miniville Grocer")      # The Flats
    assert economy.rent_for(conn, downtown) == RENT_BY_DISTRICT["Downtown"]
    assert economy.rent_for(conn, flats) == RENT_BY_DISTRICT["The Flats"]
    assert economy.rent_for(conn, downtown) > economy.rent_for(conn, flats)


# --- household flows --------------------------------------------------------


def _schedule_work(conn, aid: int, place: int, tick: int = 20):
    """Give an agent a plan row proving they worked today."""
    conn.execute(
        "INSERT OR REPLACE INTO plans(agent_id,tick,place_id,activity) VALUES(?,?,?,?)",
        (aid, tick, place, "work"))


def test_wages_credit_worker_and_debit_business():
    conn = _world()
    home = _place(conn, "Town Hall")
    _add_adult(conn, 1, home, 100_000)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                            work_days) VALUES(1,?,?,?,12,30,62)""",
        (home, "clerk", 10_000))
    _schedule_work(conn, 1, home)

    paid = economy.pay_wages(conn, 30)          # tick_of_day == 30 == shift_end
    assert paid == 10_000
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 110_000
    b = conn.execute("SELECT * FROM businesses WHERE place_id=?", (home,)).fetchone()
    assert b["payroll_today"] == 10_000


def test_wages_scale_with_the_wage_index():
    conn = _world()
    home = _place(conn, "Town Hall")
    _add_adult(conn, 1, home, 0)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                            work_days) VALUES(1,?,?,?,12,30,62)""",
        (home, "clerk", 10_000))
    _schedule_work(conn, 1, home)
    mvdb.set_meta(conn, "wage_index", "0.5")

    assert economy.pay_wages(conn, 30) == 5_000


def test_only_days_actually_worked_are_paid():
    conn = _world()
    home = _place(conn, "Town Hall")
    _add_adult(conn, 1, home, 0)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                            work_days) VALUES(1,?,?,?,12,30,62)""",
        (home, "clerk", 10_000))

    # no work in the plan (a weekend or a holiday): nothing is paid
    assert economy.pay_wages(conn, 30) == 0
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 0

    # once the plan shows a shift, the wage lands
    _schedule_work(conn, 1, home)
    assert economy.pay_wages(conn, 30) == 10_000


def test_groceries_are_charged_once_a_day_and_credit_the_grocer():
    conn = _world()
    home = _place(conn, "Town Hall")
    grocer = _place(conn, "Miniville Grocer")
    _add_adult(conn, 1, home, 100_000)
    conn.execute("UPDATE agent_state SET activity='eat' WHERE agent_id=1")

    # a meal outside the grocery window costs nothing
    economy.charge_spending(conn, 26)
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 100_000

    # the morning shop does
    economy.charge_spending(conn, GROCERY_TICK)
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 100_000 - GROCERY_CENTS
    assert conn.execute("SELECT revenue_today FROM businesses WHERE place_id=?",
                        (grocer,)).fetchone()["revenue_today"] == GROCERY_CENTS


def test_eating_out_credits_the_venue_and_respects_its_price_index():
    conn = _world()
    home = _place(conn, "Town Hall")
    diner = _place(conn, "Riverside Diner")
    _add_adult(conn, 1, home, 100_000)
    conn.execute("UPDATE agent_state SET activity='eat_out', place_id=? WHERE agent_id=1",
                 (diner,))
    conn.execute("UPDATE businesses SET price_index=1.5 WHERE place_id=?", (diner,))

    tags = {"food"}
    expected = venue_price(tags, "eat_out", 1.5)
    economy.charge_spending(conn, 26)
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 100_000 - expected
    assert conn.execute("SELECT revenue_today FROM businesses WHERE place_id=?",
                        (diner,)).fetchone()["revenue_today"] == expected


def test_broke_residents_cannot_spend_below_zero():
    conn = _world()
    home = _place(conn, "Town Hall")
    diner = _place(conn, "Riverside Diner")
    _add_adult(conn, 1, home, 100)                 # $1 to their name
    conn.execute("UPDATE agent_state SET activity='eat_out', place_id=? WHERE agent_id=1",
                 (diner,))

    economy.charge_spending(conn, 26)
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 0


def test_closed_venues_serve_nobody():
    conn = _world()
    home = _place(conn, "Town Hall")
    diner = _place(conn, "Riverside Diner")
    _add_adult(conn, 1, home, 100_000)
    conn.execute("UPDATE agent_state SET activity='eat_out', place_id=? WHERE agent_id=1",
                 (diner,))
    conn.execute("UPDATE businesses SET status='closed' WHERE place_id=?", (diner,))

    economy.charge_spending(conn, 26)
    assert conn.execute("SELECT money_cents FROM agent_state WHERE agent_id=1"
                        ).fetchone()["money_cents"] == 100_000


# --- rent -------------------------------------------------------------------


def test_rent_is_collected_weekly_and_split_across_adults():
    conn = _world()
    home = _place(conn, "Miniville Grocer")       # The Flats
    _add_household(conn, 1, home)
    _add_adult(conn, 1, home, 1_000_000, household=1)
    _add_adult(conn, 2, home, 1_000_000, household=1)
    rent = RENT_BY_DISTRICT["The Flats"]

    # not a rent day
    assert economy.collect_rent(conn, DAY, "s")["collected"] == 0

    out = economy.collect_rent(conn, 7 * DAY, "s")
    assert out["collected"] == rent
    total = conn.execute(
        "SELECT SUM(money_cents) s FROM agent_state").fetchone()["s"]
    assert total == 2_000_000 - rent


def test_a_household_that_cannot_pay_rent_falls_into_arrears_then_downsizes():
    conn = _world()
    home = _place(conn, "Town Hall")              # Downtown, the priciest
    _add_household(conn, 1, home)
    _add_adult(conn, 1, home, 0, household=1)     # no money at all

    first = economy.collect_rent(conn, 7 * DAY, "s")
    assert first["missed"] == 1 and first["downsized"] == 0
    assert conn.execute("SELECT missed_payments FROM rent_arrears WHERE household_id=1"
                        ).fetchone()["missed_payments"] == 1

    second = economy.collect_rent(conn, 14 * DAY, "s")
    assert second["missed"] == 1 and second["downsized"] == 1
    district = conn.execute(
        """SELECT p.district FROM households h JOIN places p ON p.id=h.home_place_id
           WHERE h.id=1""").fetchone()["district"]
    assert district == "The Flats"
    assert conn.execute("SELECT COUNT(*) n FROM rent_arrears").fetchone()["n"] == 0


# --- business books ---------------------------------------------------------


def test_public_service_venues_break_even_and_never_fail():
    conn = _world()
    hall = _place(conn, "Town Hall")              # civic
    _add_adult(conn, 1, hall, 0)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                            work_days) VALUES(1,?,?,?,12,30,62)""",
        (hall, "clerk", 10_000))
    # bleed the business on purpose
    conn.execute("UPDATE businesses SET balance_cents=? WHERE place_id=?",
                 (FAIL_THRESHOLD_CENTS * 10, hall))

    out = economy.settle_businesses(conn, DAY, "s")
    assert out["closed"] == 0
    b = conn.execute("SELECT * FROM businesses WHERE place_id=?", (hall,)).fetchone()
    assert b["status"] == "open"


def test_a_bleeding_commercial_venue_closes_and_lays_off_its_staff():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    _add_adult(conn, 1, diner, 0)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                            work_days) VALUES(1,?,?,?,12,30,62)""",
        (diner, "cook", 10_000))
    conn.execute("UPDATE agents SET work_place_id=? WHERE id=1", (diner,))
    conn.execute("UPDATE businesses SET balance_cents=? WHERE place_id=?",
                 (FAIL_THRESHOLD_CENTS - 1, diner))

    out = economy.settle_businesses(conn, DAY, "s")
    assert out["closed"] == 1
    b = conn.execute("SELECT * FROM businesses WHERE place_id=?", (diner,)).fetchone()
    assert b["status"] == "closed"
    assert conn.execute("SELECT COUNT(*) n FROM jobs WHERE place_id=?",
                        (diner,)).fetchone()["n"] == 0
    assert conn.execute("SELECT work_place_id FROM agents WHERE id=1"
                        ).fetchone()["work_place_id"] is None
    ev = conn.execute("SELECT * FROM events WHERE kind='town_event' ORDER BY id DESC"
                      ).fetchone()
    assert "has closed" in ev["data"]


def test_a_closed_business_reopens_after_three_weeks():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    conn.execute("UPDATE businesses SET status='closed', closed_tick=0 WHERE place_id=?",
                 (diner,))

    assert economy.settle_businesses(conn, DAY, "s")["reopened"] == 0
    tick = REOPEN_AFTER_DAYS * DAY
    assert economy.settle_businesses(conn, tick, "s")["reopened"] == 1
    assert conn.execute("SELECT status FROM businesses WHERE place_id=?",
                        (diner,)).fetchone()["status"] == "open"


def test_closed_workplaces_are_not_hiring_targets():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    open_names = {r["name"] for r in economy.open_workplaces(conn)}
    assert "Riverside Diner" in open_names

    conn.execute("UPDATE businesses SET status='closed' WHERE place_id=?", (diner,))
    assert "Riverside Diner" not in {r["name"] for r in economy.open_workplaces(conn)}


def test_healthy_businesses_raise_prices_when_bleeding_and_ease_back():
    conn = _world()
    diner = _place(conn, "Riverside Diner")
    _add_adult(conn, 1, diner, 0)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                            work_days) VALUES(1,?,?,?,12,30,62)""",
        (diner, "cook", 10_000))
    conn.execute("UPDATE businesses SET balance_cents=-1000 WHERE place_id=?", (diner,))
    economy.settle_businesses(conn, DAY, "s")
    px = conn.execute("SELECT price_index FROM businesses WHERE place_id=?",
                      (diner,)).fetchone()["price_index"]
    assert px > 1.0

    # a profitable business drifts back toward 1.0
    conn.execute("UPDATE businesses SET balance_cents=100000 WHERE place_id=?", (diner,))
    economy.settle_businesses(conn, 2 * DAY, "s")
    px2 = conn.execute("SELECT price_index FROM businesses WHERE place_id=?",
                       (diner,)).fetchone()["price_index"]
    assert px2 < px


# --- labour market ----------------------------------------------------------


def test_wages_fall_when_unemployment_is_high_and_rise_when_labour_is_scarce():
    conn = _world()
    home = _place(conn, "Town Hall")
    for aid in range(1, 11):
        _add_adult(conn, aid, home, 0)
    # 1 job / 10 adults = 90% unemployment
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                            work_days) VALUES(1,?,?,?,12,30,62)""",
        (home, "clerk", 10_000))
    assert economy.unemployment(conn) == pytest.approx(0.9)

    before = economy.wage_index(conn)
    economy.wage_dynamics(conn, 7 * DAY, "s")
    assert economy.wage_index(conn) < before

    # now everyone has a job: labour is scarce, wages drift up
    for aid in range(2, 11):
        conn.execute(
            """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
                                work_days) VALUES(?,?,?,?,12,30,62)""",
            (aid, home, "clerk", 10_000))
    assert economy.unemployment(conn) == pytest.approx(0.0)
    mid = economy.wage_index(conn)
    economy.wage_dynamics(conn, 14 * DAY, "s")
    assert economy.wage_index(conn) > mid


def test_wage_index_is_clamped():
    conn = _world()
    mvdb.set_meta(conn, "wage_index", str(economy.WAGE_INDEX_MIN))
    home = _place(conn, "Town Hall")
    for aid in range(1, 11):
        _add_adult(conn, aid, home, 0)
    economy.wage_dynamics(conn, 7 * DAY, "s")
    assert economy.wage_index(conn) >= economy.WAGE_INDEX_MIN


# --- reporting --------------------------------------------------------------


def test_weekly_levy_recycles_business_reserves_to_residents():
    conn = _world()
    home = _place(conn, "Town Hall")
    diner = _place(conn, "Riverside Diner")
    _add_adult(conn, 1, home, 0)
    _add_adult(conn, 2, home, 0)
    conn.execute("UPDATE businesses SET balance_cents=100_000 WHERE place_id=?", (diner,))

    assert economy.weekly_levy(conn, DAY, "s")["levied"] == 0      # not a levy day
    out = economy.weekly_levy(conn, 7 * DAY, "s")
    taxed = int(100_000 * economy.BUSINESS_TAX_RATE)
    assert out["levied"] == taxed
    assert out["residents"] == 2
    # the town has no public payroll to fund in this fixture, so the purse
    # needs no buffer and the whole levy comes back out as the dividend
    assert economy.public_payroll_week(conn) == 0
    assert out["dividend"] == taxed // 2
    assert conn.execute("SELECT SUM(money_cents) s FROM agent_state"
                        ).fetchone()["s"] == taxed
    assert economy.town_balance(conn) == 0
    assert conn.execute("SELECT balance_cents FROM businesses WHERE place_id=?",
                        (diner,)).fetchone()["balance_cents"] == 100_000 - taxed


def test_levy_funds_public_payroll_and_banks_a_buffer(conn=None):
    """With public staff on the books the purse keeps a buffer, and the levy
    still funds the payroll rather than vanishing."""
    conn = _world()
    home = _place(conn, "Town Hall")
    hospital = _place(conn, "Miniville General Hospital")
    diner = _place(conn, "Riverside Diner")
    _add_adult(conn, 1, home, 0)
    _add_adult(conn, 2, home, 0)
    conn.execute(
        """INSERT INTO jobs(agent_id,place_id,role,wage_cents,shift_start,shift_end,
           work_days) VALUES(1,?,'nurse',20000,16,34,62)""", (hospital,))
    conn.execute("UPDATE businesses SET balance_cents=10_000_000 WHERE place_id=?",
                 (diner,))

    week = economy.public_payroll_week(conn)
    assert week == 100_000                       # one post, five days
    out = economy.weekly_levy(conn, 7 * DAY, "s")
    taxed = int(10_000_000 * economy.BUSINESS_TAX_RATE)
    assert out["levied"] == taxed
    # the purse banks up to its buffer instead of handing everything out
    assert economy.town_balance(conn) <= economy.PURSE_BUFFER_WEEKS * week
    assert out["dividend"] * 2 <= taxed
    # and the payroll comes out of the purse, not out of thin air
    before = economy.town_balance(conn)
    economy.settle_businesses(conn, 7 * DAY + 48, "s")
    assert economy.town_balance(conn) <= before


def test_economy_stats_reports_the_town():
    conn = _world()
    home = _place(conn, "Town Hall")
    _add_adult(conn, 1, home, 500_000)
    _add_adult(conn, 2, home, 100_000)

    s = economy.economy_stats(conn)
    assert s["money_supply_cents"] == 600_000
    assert s["mean_balance_cents"] == 300_000
    assert s["median_balance_cents"] in (100_000, 500_000)
    assert s["businesses_open"] >= 1
    assert "money supply" in economy.economy_line(conn)


def test_record_day_writes_a_time_series_row():
    conn = _world()
    home = _place(conn, "Town Hall")
    _add_adult(conn, 1, home, 500_000)

    stats = economy.record_day(conn, DAY)
    assert stats["day"] == 0
    row = conn.execute("SELECT * FROM economy_days WHERE day=0").fetchone()
    assert row["money_supply_cents"] == 500_000

    # the in-progress day must not be reported as the last completed day
    economy._bump_day(conn, 1, spending=123)
    assert economy.economy_stats(conn)["last_day"]["day"] == 0
