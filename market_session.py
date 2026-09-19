"""Shared US equity session clock (NYSE calendar, America/New_York).

Clock status describes a trading session, never provider coverage or quote health.
Naive datetimes are interpreted as Eastern for compatibility with existing callers.
Scheduled early-close days end the common US equity late session at 17:00 ET.
"""
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LABELS = {"premarket": "PRE-MARKET", "regular": "MARKET OPEN",
          "afterhours": "AFTER-HOURS", "closed": "MARKET CLOSED"}


def eastern(now=None):
    now = now if now is not None else datetime.now(ET)
    return now.replace(tzinfo=ET) if now.tzinfo is None else now.astimezone(ET)


@lru_cache(maxsize=1)
def _calendar():
    import pandas_market_calendars as mcal
    return mcal.get_calendar("NYSE")


@lru_cache(maxsize=512)
def trading_day_bounds(day):
    """None on weekends/holidays; calendar failure never implies an open market."""
    schedule = _calendar().schedule(start_date=day, end_date=day, tz="America/New_York")
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    opening, closing = row["market_open"], row["market_close"]
    open_min = opening.hour * 60 + opening.minute
    close_min = closing.hour * 60 + closing.minute
    return (240, open_min, close_min, 1020 if close_min < 960 else 1200)


def market_session_phase(now=None):
    now = eastern(now)
    bounds = trading_day_bounds(now.date())
    if bounds is None:
        return "closed"
    pre, opening, closing, post = bounds
    minute = now.hour * 60 + now.minute
    if pre <= minute < opening:
        return "premarket"
    if opening <= minute < closing:
        return "regular"
    if closing <= minute < post:
        return "afterhours"
    return "closed"


def session_bounds(phase, now=None):
    bounds = trading_day_bounds(eastern(now).date())
    if bounds is None:
        return None
    pre, opening, closing, post = bounds
    return {"premarket": (pre, opening), "regular": (opening, closing),
            "afterhours": (closing, post)}.get(phase)


def workspace_market_status(now=None):
    now = eastern(now)
    phase = market_session_phase(now)
    return now, phase == "regular", LABELS[phase]
