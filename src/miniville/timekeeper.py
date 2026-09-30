"""Simulation clock. 1 tick = 30 sim-minutes; 48 ticks = 1 day.

Day 0 is a fictional 'Day 1' — calendar labels render as 'Day N, HH:MM'.
"""
from __future__ import annotations

TICKS_PER_DAY = 48
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def day_of(tick: int) -> int:
    return tick // TICKS_PER_DAY


def tick_of_day(tick: int) -> int:
    return tick % TICKS_PER_DAY


def weekday(tick: int) -> int:
    """0=Mon .. 6=Sun"""
    return day_of(tick) % 7


def is_weekend(tick: int) -> bool:
    return weekday(tick) >= 5


def fmt_tick(tick: int) -> str:
    tod = tick_of_day(tick)
    return f"Day {day_of(tick)+1} {DAY_NAMES[weekday(tick)]} {tod//2:02d}:{(tod%2)*30:02d}"
