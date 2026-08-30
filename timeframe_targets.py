"""Shared, causal outcome targets for Scanner / Analyzer timeframe learning.

These helpers define what the model is trying to predict. Keeping them shared
between historical replay and live outcome resolution prevents target drift.
"""

from __future__ import annotations

import math

SWING_TARGET_PCT = 5.0
SWING_STOP_PCT = 4.0
SWING_HORIZON_SESSIONS = 5


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def resolve_swing_path_from_bars(
    entry_price,
    future_daily_bars,
    *,
    target_pct=SWING_TARGET_PCT,
    stop_pct=SWING_STOP_PCT,
    horizon_sessions=SWING_HORIZON_SESSIONS,
):
    """Resolve +target-before-stop over future daily OHLC bars.

    The entry is assumed to be the observation day's close. Only bars after the
    signal day should be supplied.

    If target and stop are both touched on the same daily bar before any prior
    event, ordering is unknowable from daily OHLC. The label is therefore None
    so validation excludes rather than guesses that row.
    """
    entry = _num(entry_price)
    horizon = int(horizon_sessions)
    if entry is None or entry <= 0 or horizon <= 0:
        return {}

    bars = list(future_daily_bars or [])
    if len(bars) < horizon:
        return {}

    target_price = entry * (1.0 + float(target_pct) / 100.0)
    stop_price = entry * (1.0 - float(stop_pct) / 100.0)

    max_high = None
    min_low = None
    first_event = None
    first_hit_session = None
    ambiguous_same_day = False

    for session_number, bar in enumerate(bars[:horizon], start=1):
        high = _num((bar or {}).get("h"))
        low = _num((bar or {}).get("l"))
        if high is not None:
            max_high = high if max_high is None else max(max_high, high)
        if low is not None:
            min_low = low if min_low is None else min(min_low, low)

        if first_event is not None:
            continue

        hit_target = high is not None and high >= target_price
        hit_stop = low is not None and low <= stop_price
        if hit_target and hit_stop:
            first_event = "AMBIGUOUS_SAME_DAY"
            first_hit_session = session_number
            ambiguous_same_day = True
        elif hit_target:
            first_event = "TARGET"
            first_hit_session = session_number
        elif hit_stop:
            first_event = "STOP"
            first_hit_session = session_number

    if first_event is None:
        first_event = "NEITHER"

    label = None
    if first_event == "TARGET":
        label = 1
    elif first_event in {"STOP", "NEITHER"}:
        label = 0

    mfe = (max_high / entry - 1.0) * 100.0 if max_high is not None else None
    mae = (min_low / entry - 1.0) * 100.0 if min_low is not None else None

    return {
        "swing_target_pct": float(target_pct),
        "swing_stop_pct": float(stop_pct),
        "swing_horizon_sessions": horizon,
        "swing_target_before_stop_5d": label,
        "swing_first_event_5d": first_event,
        "swing_first_hit_session": first_hit_session,
        "swing_ambiguous_same_day_5d": ambiguous_same_day,
        "swing_mfe_5d_pct": round(mfe, 3) if mfe is not None else None,
        "swing_mae_5d_pct": round(mae, 3) if mae is not None else None,
    }
