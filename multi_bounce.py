"""Pure multi-bounce / multi-leg market-structure helpers.

This module intentionally has no API, Streamlit, or model dependencies so the
live analyzer, historical replay, and ML training code can all use the exact
same sequence logic.  When called from training it must only receive bars that
existed at the historical snapshot; that keeps the features leakage-safe.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import median

from market_structure import bounce_sequence_context


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _clamp(value, low, high):
    return max(low, min(high, value))


def _parse_dt(value):
    if value is None:
        return None
    try:
        text=str(value).strip()
        if not text:
            return None
        if text.isdigit():
            numeric=float(text)
            if numeric > 10_000_000_000:
                numeric /= 1000.0
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        dt=datetime.fromisoformat(text.replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _bar_spacing_minutes(rows):
    diffs=[]
    previous=None
    for row in rows:
        current=_parse_dt(row.get("t"))
        if current is None:
            continue
        if previous is not None:
            diff=(current-previous).total_seconds()/60.0
            if 0 < diff <= 60:
                diffs.append(diff)
        previous=current
    if not diffs:
        return 1.0
    return max(0.25, float(median(diffs)))


def _rows(bars):
    out = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        o = _num(bar.get("o"))
        h = _num(bar.get("h"))
        l = _num(bar.get("l"))
        c = _num(bar.get("c"))
        v = _num(bar.get("v")) or 0.0
        if h is None or l is None or c is None or h <= 0 or l <= 0:
            continue
        if o is None:
            o = c
        out.append(
            {
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
                "t": bar.get("t"),
            }
        )
    return out


def _find_impulse(rows, atr_pct=None, min_impulse_pct=None):
    if len(rows) < 8:
        return None

    atrp = _num(atr_pct) or 8.0
    minimum = (
        _num(min_impulse_pct)
        if _num(min_impulse_pct) is not None
        else max(6.0, min(16.0, atrp * 0.70))
    )
    candidates = []
    n = len(rows)

    for peak_idx in range(5, n - 1):
        start = max(0, peak_idx - 120)
        low_idx = min(range(start, peak_idx), key=lambda i: rows[i]["l"])
        low = rows[low_idx]["l"]
        peak = rows[peak_idx]["h"]
        if peak <= low:
            continue
        move_pct = (peak / low - 1.0) * 100.0
        if move_pct < minimum or peak_idx - low_idx < 3:
            continue

        age = n - 1 - peak_idx
        recency = max(0.30, 1.0 - age / max(30.0, n * 0.90))
        post_bars = n - 1 - peak_idx
        # For a multi-bounce sequence the dominant initial impulse must stay
        # anchored even after one or two later rebounds. Recency is only a
        # mild tie-breaker; otherwise a smaller second bounce can incorrectly
        # become the "impulse" and erase the earlier bounce history.
        history_bonus = min(1.12, 0.92 + post_bars * 0.012)
        score = move_pct * (0.82 + 0.18 * recency) * history_bonus
        candidates.append((score, low_idx, peak_idx, low, peak, move_pct))

    if not candidates:
        return None

    best=max(candidates,key=lambda item:item[0])
    _,best_low_idx,best_peak_idx,best_low,best_peak,best_move_pct=best

    # A later rebound can make a *marginal* new high after a real pullback.
    # Do not let that erase the earlier impulse peak and all bounce history.
    # If an earlier candidate captured at least 90% of the eventual move,
    # the later high is within 2% of it, and price pulled back meaningfully
    # between the two peaks, anchor the initial impulse to that earlier peak.
    anchor_pullback_pct=_clamp(atrp*0.18,1.8,3.5)
    for candidate in sorted(candidates,key=lambda item:item[2]):
        _,low_idx,peak_idx,low,peak,move_pct=candidate
        if peak_idx >= best_peak_idx:
            break
        if move_pct < best_move_pct*0.90:
            continue
        if best_peak > peak*1.02:
            continue
        between=rows[peak_idx+1:best_peak_idx+1]
        if not between:
            continue
        interim_low=min(row["l"] for row in between)
        drawdown_pct=(peak/interim_low-1.0)*100.0 if interim_low>0 else 0.0
        if drawdown_pct >= anchor_pullback_pct:
            return candidate[1:]

    return best[1:]


def detect_bounce_sequence(
    bars,
    current_price=None,
    atr_pct=None,
    min_impulse_pct=None,
):
    """Canonical multi-bounce view built from shared confirmed market swings."""
    return bounce_sequence_context(
        bars,
        current_price=current_price,
        atr_pct=atr_pct,
        min_impulse_pct=min_impulse_pct,
    )

def bounce_feature_values(sequence):
    """Compact numeric features shared by live and historical ML."""
    seq = sequence or {}
    leg = str(seq.get("current_leg") or "").upper()
    leg_code = 1.0 if leg == "BOUNCING" else -1.0 if "PULL" in leg else 0.0
    return {
        "bounce_count": float(seq.get("completed_bounces") or 0),
        "last_bounce_pct": _num(seq.get("latest_bounce_pct")),
        "bounce_decay_ratio": _num(seq.get("bounce_decay_ratio")),
        "bounce_volume_decay_ratio": _num(seq.get("bounce_volume_decay_ratio")),
        "lower_high_streak": float(seq.get("lower_high_streak") or 0),
        "higher_low_streak": float(seq.get("higher_low_streak") or 0),
        "sequence_health_score": _num(seq.get("sequence_health_score")),
        "current_pullback_pct": _num(seq.get("current_pullback_pct")),
        "ongoing_bounce_pct": _num(seq.get("ongoing_bounce_pct")),
        "bounce_leg_code": leg_code,
        "reference_peak_pct_above_dip": (
            ((_num(seq.get("reference_peak")) / _num(seq.get("current_dip_low")) - 1.0) * 100.0)
            if _num(seq.get("reference_peak")) and _num(seq.get("current_dip_low"))
            else None
        ),
    }
