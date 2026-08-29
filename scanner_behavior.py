"""Causal behavior features for the standalone Momentum Scanner / Analyzer.

Every feature in this module must be computable using bars available at the
observation timestamp. No future bars or outcomes are used.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

from multi_bounce import bounce_feature_values, detect_bounce_sequence
from stair_step import detect_stair_step, stair_step_feature_values

BEHAVIOR_FEATURE_VERSION = "scanner-behavior-v2-completed-bars"


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def resample_to_5min(bars, *, as_of=None, completed_only=False):
    """Aggregate bars into causal five-minute buckets.

    When completed_only is true, emit a bucket only after all five minutes
    have elapsed so live confirmations match historical replay.
    """
    buckets = defaultdict(list)
    order = []
    for bar in bars or []:
        dt = _dt(bar.get("t"))
        if dt is None:
            continue
        minute = (dt.minute // 5) * 5
        key = dt.replace(minute=minute, second=0, microsecond=0)
        if key not in buckets:
            order.append(key)
        buckets[key].append(bar)

    as_of_dt = _dt(as_of) if as_of is not None else None
    out = []
    for key in sorted(order):
        if completed_only and as_of_dt is not None:
            compare_as_of = as_of_dt
            if key.tzinfo is None and compare_as_of.tzinfo is not None:
                compare_as_of = compare_as_of.replace(tzinfo=None)
            elif key.tzinfo is not None and compare_as_of.tzinfo is None:
                compare_as_of = compare_as_of.replace(tzinfo=key.tzinfo)
            if key + timedelta(minutes=5) > compare_as_of:
                continue

        group = buckets[key]
        if not group:
            continue
        opens = [_num(b.get("o")) for b in group]
        highs = [_num(b.get("h")) for b in group]
        lows = [_num(b.get("l")) for b in group]
        closes = [_num(b.get("c")) for b in group]
        vols = [_num(b.get("v")) or 0.0 for b in group]
        opens = [v for v in opens if v is not None]
        highs = [v for v in highs if v is not None]
        lows = [v for v in lows if v is not None]
        closes = [v for v in closes if v is not None]
        if not opens or not highs or not lows or not closes:
            continue

        volume = sum(vols)
        dollar = 0.0
        for bar, volume_value in zip(group, vols):
            px = _num(bar.get("vw"))
            if px is None:
                h = _num(bar.get("h"))
                l = _num(bar.get("l"))
                c = _num(bar.get("c"))
                if h is not None and l is not None and c is not None:
                    px = (h + l + c) / 3.0
                else:
                    px = c
            if px is not None and volume_value > 0:
                dollar += px * volume_value

        out.append(
            {
                "t": key.isoformat(),
                "o": opens[0],
                "h": max(highs),
                "l": min(lows),
                "c": closes[-1],
                "v": volume,
                "vw": (
                    dollar / volume
                    if volume > 0 and dollar > 0
                    else closes[-1]
                ),
            }
        )
    return out

def impulse_pullback_features(bars):
    """Find the strongest recent impulse and its current retracement."""
    data = []
    for bar in bars or []:
        h = _num(bar.get("h"))
        l = _num(bar.get("l"))
        c = _num(bar.get("c"))
        v = _num(bar.get("v")) or 0.0
        if h is None or l is None or c is None or h <= 0 or l <= 0:
            continue
        data.append({"h": h, "l": l, "c": c, "v": v})
    if len(data) < 6:
        return {}

    current = data[-1]["c"]
    candidates = []
    n = len(data)
    for peak_idx in range(3, n - 1):
        start = max(0, peak_idx - 24)
        if peak_idx <= start:
            continue
        low_idx = min(range(start, peak_idx), key=lambda i: data[i]["l"])
        low = data[low_idx]["l"]
        peak = data[peak_idx]["h"]
        if peak <= low:
            continue
        move = (peak / low - 1.0) * 100.0
        if move < 6.0:
            continue
        after = data[peak_idx + 1 :]
        if not after:
            continue
        trough_rel = min(range(len(after)), key=lambda i: after[i]["l"])
        trough_idx = peak_idx + 1 + trough_rel
        trough = data[trough_idx]["l"]
        run = peak - low
        if run <= 0:
            continue
        max_retrace = (peak - trough) / run * 100.0
        current_retrace = (peak - current) / run * 100.0
        recovery = max_retrace - current_retrace
        age = n - 1 - peak_idx
        recency = max(0.35, 1.0 - age / max(12.0, n * 0.8))
        score = move * recency * (1.0 if 15 <= max_retrace <= 75 else 0.75)
        candidates.append(
            (
                score,
                low_idx,
                peak_idx,
                trough_idx,
                move,
                current_retrace,
                max_retrace,
                recovery,
            )
        )

    if not candidates:
        return {}

    (
        _score,
        low_idx,
        peak_idx,
        trough_idx,
        move,
        current_retrace,
        max_retrace,
        recovery,
    ) = max(candidates, key=lambda item: item[0])

    impulse_vol = [
        data[i]["v"]
        for i in range(low_idx, peak_idx + 1)
        if data[i]["v"] > 0
    ]
    pullback_vol = [
        data[i]["v"]
        for i in range(peak_idx + 1, trough_idx + 1)
        if data[i]["v"] > 0
    ]
    impulse_avg = sum(impulse_vol) / len(impulse_vol) if impulse_vol else None
    pullback_avg = sum(pullback_vol) / len(pullback_vol) if pullback_vol else None
    volume_ratio = (
        pullback_avg / impulse_avg
        if impulse_avg and pullback_avg is not None
        else None
    )

    quality = 50.0
    if 20.0 <= current_retrace <= 55.0:
        quality += 18.0
    elif current_retrace > 75.0:
        quality -= 20.0
    elif current_retrace < 5.0:
        quality -= 8.0
    if volume_ratio is not None:
        if volume_ratio <= 0.65:
            quality += 16.0
        elif volume_ratio >= 1.15:
            quality -= 14.0
    if recovery >= 10.0:
        quality += 12.0
    elif recovery < 0:
        quality -= 10.0
    quality = max(0.0, min(100.0, quality))

    return {
        "impulse_move_pct": round(move, 3),
        "impulse_retracement_pct": round(current_retrace, 3),
        "impulse_max_retracement_pct": round(max_retrace, 3),
        "impulse_bounce_recovery_pct": round(recovery, 3),
        "pullback_volume_ratio": (
            round(volume_ratio, 4) if volume_ratio is not None else None
        ),
        "pullback_quality_score": round(quality, 1),
    }


def _cumulative_vwap_series(bars):
    out = []
    total_volume = 0.0
    total_dollar = 0.0
    for bar in bars or []:
        close = _num(bar.get("c"))
        volume = _num(bar.get("v")) or 0.0
        price = _num(bar.get("vw"))
        if price is None:
            h = _num(bar.get("h"))
            l = _num(bar.get("l"))
            if h is not None and l is not None and close is not None:
                price = (h + l + close) / 3.0
            else:
                price = close
        if close is None or price is None:
            out.append((close, None))
            continue
        if volume > 0:
            total_volume += volume
            total_dollar += price * volume
        vwap = total_dollar / total_volume if total_volume > 0 else None
        out.append((close, vwap))
    return out


def vwap_interaction_features(bars):
    series = _cumulative_vwap_series(bars)
    valid = [(c, v) for c, v in series if c is not None and v is not None]
    if len(valid) < 3:
        return {}

    recent = valid[-10:]
    states = [1 if close >= vwap else -1 for close, vwap in recent]
    hold_ratio = sum(state > 0 for state in states) / len(states)
    last2 = states[-2:] if len(states) >= 2 else states
    prior = states[:-2] if len(states) > 2 else []

    reclaim = bool(last2 and all(state > 0 for state in last2) and any(state < 0 for state in prior[-6:]))
    rejection = bool(last2 and all(state < 0 for state in last2) and any(state > 0 for state in prior[-6:]))
    if reclaim:
        state_code = 2.0
    elif rejection:
        state_code = -2.0
    elif states[-1] > 0:
        state_code = 1.0
    else:
        state_code = -1.0

    crosses = sum(
        1 for a, b in zip(states, states[1:])
        if a != b
    )
    return {
        "vwap_hold_ratio_10": round(hold_ratio, 4),
        "vwap_reclaim": 1.0 if reclaim else 0.0,
        "vwap_rejection": 1.0 if rejection else 0.0,
        "vwap_state_code": state_code,
        "vwap_crosses_10": float(crosses),
    }


def volume_acceleration_features(bars):
    vols = [_num(bar.get("v")) or 0.0 for bar in (bars or [])]
    if len(vols) < 8:
        return {}
    recent = vols[-3:]
    prior = vols[-9:-3] if len(vols) >= 9 else vols[:-3]
    if not prior:
        return {}
    recent_avg = sum(recent) / len(recent)
    prior_avg = sum(prior) / len(prior)
    ratio = recent_avg / prior_avg if prior_avg > 0 else None
    return {
        "volume_acceleration_ratio": round(ratio, 4) if ratio is not None else None,
        "volume_accelerating": 1.0 if ratio is not None and ratio >= 1.35 else 0.0,
        "volume_contracting": 1.0 if ratio is not None and ratio <= 0.75 else 0.0,
    }


def breakout_behavior_features(bars):
    data = []
    for bar in bars or []:
        h = _num(bar.get("h"))
        c = _num(bar.get("c"))
        if h is None or c is None:
            continue
        data.append({"h": h, "c": c})
    if len(data) < 8:
        return {}

    start = max(4, len(data) - 4)
    event_level = None
    event_index = None
    for i in range(start, len(data)):
        prior_start = max(0, i - 20)
        prior = data[prior_start:i]
        if len(prior) < 4:
            continue
        level = max(row["h"] for row in prior)
        if data[i]["h"] > level:
            event_level = level
            event_index = i
            break

    if event_level is None:
        prior = data[max(0, len(data) - 21):-1]
        level = max((row["h"] for row in prior), default=None)
        return {
            "breakout_recent": 0.0,
            "breakout_holding": 0.0,
            "failed_breakout": 0.0,
            "breakout_extension_pct": (
                round((data[-1]["c"] / level - 1.0) * 100.0, 3)
                if level
                else None
            ),
        }

    current = data[-1]["c"]
    extension = (current / event_level - 1.0) * 100.0
    holding = current >= event_level
    failed = current < event_level
    return {
        "breakout_recent": 1.0,
        "breakout_holding": 1.0 if holding else 0.0,
        "failed_breakout": 1.0 if failed else 0.0,
        "breakout_extension_pct": round(extension, 3),
        "breakout_bars_since": float(len(data) - 1 - event_index),
    }


def intraday_behavior_features(
    bars,
    current_price=None,
    atr_pct=None,
    *,
    as_of=None,
    completed_only=False,
):
    """Return compact behavior features confirmed by the observation time."""
    bars5 = resample_to_5min(
        bars,
        as_of=as_of,
        completed_only=completed_only,
    )
    if not bars5:
        return {}

    price = (
        _num(bars5[-1].get("c"))
        if completed_only
        else (_num(current_price) or _num(bars5[-1].get("c")))
    )
    features = {}
    features.update(impulse_pullback_features(bars5))
    sequence = detect_bounce_sequence(
        bars5,
        current_price=price,
        atr_pct=atr_pct,
    )
    features.update(bounce_feature_values(sequence))
    features.update(vwap_interaction_features(bars5))
    features.update(volume_acceleration_features(bars5))
    features.update(breakout_behavior_features(bars5))
    return features


def multi_session_behavior_features(daily_bars, current_day, atr_pct=None):
    stair = detect_stair_step(
        daily_bars or [],
        current_day=current_day,
        atr_pct=atr_pct,
    )
    return stair_step_feature_values(stair)
