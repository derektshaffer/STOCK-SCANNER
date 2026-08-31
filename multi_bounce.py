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
    return max(candidates, key=lambda item: item[0])[1:]


def detect_bounce_sequence(
    bars,
    current_price=None,
    atr_pct=None,
    min_impulse_pct=None,
):
    """Detect repeated pullback -> bounce legs after a meaningful impulse.

    The detector is deliberately conservative. A swing is only considered
    complete after price reverses far enough in the opposite direction. This
    prevents every noisy candle from becoming a new "bounce."

    Returned percentages describe observed structure; they are not probabilities.
    """

    rows = _rows(bars)
    if len(rows) < 10:
        return {
            "status": "insufficient_data",
            "detected": False,
            "completed_bounces": 0,
            "bounces": [],
        }

    impulse = _find_impulse(rows, atr_pct=atr_pct, min_impulse_pct=min_impulse_pct)
    if not impulse:
        return {
            "status": "no_clear_impulse",
            "detected": False,
            "completed_bounces": 0,
            "bounces": [],
        }

    low_idx, impulse_peak_idx, impulse_low, impulse_high, impulse_move_pct = impulse
    run_size = impulse_high - impulse_low
    price = _num(current_price) or rows[-1]["c"]
    atrp = _num(atr_pct) or 8.0

    # Price threshold alone is not enough on volatile small caps: one-minute
    # candles can zig-zag by several percent inside a single larger pullback.
    # Require distinct swing separation and meaningful recovery as well.
    swing_pct = _clamp(atrp * 0.32, 1.8, 6.0)
    swing_abs = max(impulse_high * swing_pct / 100.0, run_size * 0.045)
    bar_minutes = _bar_spacing_minutes(rows)
    min_leg_minutes = max(2.0, bar_minutes)
    first_bounce_min_cycle_minutes = max(3.0, bar_minutes * 1.5)
    min_cycle_minutes = max(5.0, bar_minutes * 2.0)
    min_leg_bars = max(1, int(math.ceil(min_leg_minutes / bar_minutes)))
    first_bounce_min_cycle_bars = max(
        2,
        int(math.ceil(first_bounce_min_cycle_minutes / bar_minutes)),
    )
    min_cycle_bars = max(2, int(math.ceil(min_cycle_minutes / bar_minutes)))
    min_recovery_fraction = 0.35

    state = "seek_trough"
    trough_idx = None
    trough_price = None
    candidate_idx = None
    candidate_price = None
    prior_peak_idx = impulse_peak_idx
    prior_peak_price = impulse_high
    bounces = []

    def enough_up(low_price, high_price):
        return (
            high_price - low_price >= swing_abs
            or high_price >= low_price * (1.0 + swing_pct / 100.0)
        )

    def enough_down(high_price, low_price):
        return (
            high_price - low_price >= swing_abs
            or low_price <= high_price * (1.0 - swing_pct / 100.0)
        )

    for i in range(impulse_peak_idx + 1, len(rows)):
        row = rows[i]

        if state == "seek_trough":
            if candidate_price is None or row["l"] < candidate_price:
                candidate_price = row["l"]
                candidate_idx = i

            pullback_is_distinct = bool(
                candidate_price is not None
                and candidate_idx is not None
                and candidate_idx - prior_peak_idx >= min_leg_bars
                and enough_down(prior_peak_price, candidate_price)
            )
            if (
                pullback_is_distinct
                and enough_up(candidate_price, row["h"])
            ):
                trough_price = candidate_price
                trough_idx = candidate_idx
                # If this same candle created the trough, we do not know
                # whether its high happened before or after that low. Start
                # the rebound candidate on a later candle instead of assuming
                # an intrabar order we cannot observe.
                if trough_idx is not None and trough_idx < i:
                    candidate_price = row["h"]
                    candidate_idx = i
                else:
                    candidate_price = None
                    candidate_idx = None
                state = "seek_peak"

        else:  # seek_peak
            # A lower low before confirmation means the apparent rebound was
            # only noise inside the same pullback. Re-anchor the trough rather
            # than counting a micro-bounce.
            if trough_price is not None and row["l"] < trough_price:
                trough_price = row["l"]
                trough_idx = i
                # Same-candle high may have occurred before the new low; wait
                # for a later candle before measuring the rebound.
                candidate_price = None
                candidate_idx = None
                continue

            if candidate_price is None or row["h"] > candidate_price:
                candidate_price = row["h"]
                candidate_idx = i

            if candidate_price is not None and enough_down(candidate_price, row["l"]):
                bounce_peak = candidate_price
                bounce_peak_idx = candidate_idx

                pullback_range = (
                    prior_peak_price - trough_price
                    if trough_price is not None
                    else 0.0
                )
                recovery_fraction = (
                    (bounce_peak - trough_price) / pullback_range
                    if pullback_range > 0
                    else 0.0
                )
                required_cycle_bars = (
                    first_bounce_min_cycle_bars
                    if not bounces
                    else min_cycle_bars
                )
                bounce_is_distinct = bool(
                    bounce_peak_idx is not None
                    and trough_idx is not None
                    and i > bounce_peak_idx
                    and bounce_peak_idx - trough_idx >= min_leg_bars
                    and bounce_peak_idx - prior_peak_idx >= required_cycle_bars
                    and recovery_fraction >= min_recovery_fraction
                )
                if not bounce_is_distinct:
                    continue

                pullback_drop_pct = (
                    (prior_peak_price / trough_price - 1.0) * 100.0
                    if trough_price
                    else None
                )
                retracement_of_initial_run_pct = (
                    (prior_peak_price - trough_price) / run_size * 100.0
                    if run_size > 0
                    else None
                )
                bounce_pct = (
                    (bounce_peak / trough_price - 1.0) * 100.0
                    if trough_price
                    else None
                )
                recovery_to_prior_peak_pct = (
                    (bounce_peak - trough_price)
                    / max(1e-9, prior_peak_price - trough_price)
                    * 100.0
                    if trough_price < prior_peak_price
                    else None
                )

                pullback_vols = [
                    rows[j]["v"]
                    for j in range(prior_peak_idx + 1, trough_idx + 1)
                    if rows[j]["v"] > 0
                ]
                bounce_vols = [
                    rows[j]["v"]
                    for j in range(trough_idx, bounce_peak_idx + 1)
                    if rows[j]["v"] > 0
                ]
                pullback_avg_vol = (
                    sum(pullback_vols) / len(pullback_vols)
                    if pullback_vols
                    else None
                )
                bounce_avg_vol = (
                    sum(bounce_vols) / len(bounce_vols)
                    if bounce_vols
                    else None
                )

                previous_bounce = bounces[-1] if bounces else None
                previous_bounce_pct = (
                    _num(previous_bounce.get("bounce_pct"))
                    if previous_bounce
                    else None
                )
                decay_ratio = (
                    bounce_pct / previous_bounce_pct
                    if bounce_pct is not None
                    and previous_bounce_pct
                    and previous_bounce_pct > 0
                    else None
                )

                bounces.append(
                    {
                        "number": len(bounces) + 1,
                        "pullback_low": round(trough_price, 4),
                        "pullback_low_index": int(trough_idx),
                        "pullback_low_time": rows[trough_idx].get("t"),
                        "bounce_peak": round(bounce_peak, 4),
                        "bounce_peak_index": int(bounce_peak_idx),
                        "bounce_peak_time": rows[bounce_peak_idx].get("t"),
                        "confirmation_time": row.get("t"),
                        "pullback_drop_pct": round(pullback_drop_pct, 2)
                        if pullback_drop_pct is not None
                        else None,
                        "retracement_of_initial_run_pct": round(
                            retracement_of_initial_run_pct, 2
                        )
                        if retracement_of_initial_run_pct is not None
                        else None,
                        "bounce_pct": round(bounce_pct, 2)
                        if bounce_pct is not None
                        else None,
                        "recovery_to_prior_peak_pct": round(
                            recovery_to_prior_peak_pct, 2
                        )
                        if recovery_to_prior_peak_pct is not None
                        else None,
                        "recovery_fraction": round(recovery_fraction, 3),
                        "lower_high": bool(
                            bounce_peak < prior_peak_price * 0.995
                        ),
                        "higher_high": bool(
                            bounce_peak > prior_peak_price * 1.005
                        ),
                        "new_session_impulse_high": bool(
                            bounce_peak > impulse_high * 1.005
                        ),
                        "pullback_bars": int(trough_idx - prior_peak_idx),
                        "bounce_bars": int(bounce_peak_idx - trough_idx),
                        "pullback_avg_volume": round(pullback_avg_vol, 2)
                        if pullback_avg_vol is not None
                        else None,
                        "bounce_avg_volume": round(bounce_avg_vol, 2)
                        if bounce_avg_vol is not None
                        else None,
                        "decay_vs_previous": round(decay_ratio, 3)
                        if decay_ratio is not None
                        else None,
                    }
                )

                prior_peak_idx = bounce_peak_idx
                prior_peak_price = bounce_peak
                candidate_price = row["l"]
                candidate_idx = i
                state = "seek_trough"

    # Describe the still-forming leg.
    current_leg = "PULLBACK"
    current_leg_move_pct = None
    current_pullback_pct = None
    ongoing_bounce_pct = None
    current_dip_low = None
    current_bounce_high = None

    if state == "seek_peak" and trough_price is not None:
        current_leg = "BOUNCING"
        current_dip_low = trough_price
        ongoing_high = max(
            [r["h"] for r in rows[trough_idx:]] + [price]
        )
        current_bounce_high = ongoing_high
        ongoing_bounce_pct = (
            (ongoing_high / trough_price - 1.0) * 100.0
            if trough_price
            else None
        )
        current_leg_move_pct = ongoing_bounce_pct
    else:
        current_leg = "PULLING BACK"
        current_low = min(
            [r["l"] for r in rows[prior_peak_idx + 1:]] + [price]
        ) if prior_peak_idx + 1 < len(rows) else price
        current_dip_low = current_low
        current_pullback_pct = (
            (prior_peak_price / current_low - 1.0) * 100.0
            if current_low and prior_peak_price
            else None
        )
        current_leg_move_pct = current_pullback_pct

    bounce1 = _num(bounces[0].get("bounce_pct")) if len(bounces) >= 1 else None
    bounce2 = _num(bounces[1].get("bounce_pct")) if len(bounces) >= 2 else None
    bounce3 = _num(bounces[2].get("bounce_pct")) if len(bounces) >= 3 else None
    last_bounce = bounces[-1] if bounces else None
    previous_bounce = bounces[-2] if len(bounces) >= 2 else None

    last_decay = (
        _num(last_bounce.get("decay_vs_previous"))
        if last_bounce
        else None
    )
    first_vol = (
        _num(bounces[0].get("bounce_avg_volume"))
        if bounces
        else None
    )
    last_vol = (
        _num(last_bounce.get("bounce_avg_volume"))
        if last_bounce
        else None
    )
    volume_decay = (
        last_vol / first_vol
        if last_vol is not None and first_vol and first_vol > 0
        else None
    )

    lower_high_streak = 0
    for bounce in reversed(bounces):
        if bounce.get("lower_high"):
            lower_high_streak += 1
        else:
            break

    higher_low_streak = 0
    if len(bounces) >= 2:
        troughs = [_num(b.get("pullback_low")) for b in bounces]
        for i in range(len(troughs) - 1, 0, -1):
            if troughs[i] is not None and troughs[i - 1] is not None and troughs[i] > troughs[i - 1] * 1.002:
                higher_low_streak += 1
            else:
                break

    health = 55.0
    if bounces:
        health += 5.0
    if last_bounce:
        recovery = _num(last_bounce.get("recovery_to_prior_peak_pct"))
        if recovery is not None:
            if recovery >= 90:
                health += 12
            elif recovery >= 65:
                health += 6
            elif recovery < 45:
                health -= 10
    if last_decay is not None:
        if last_decay < 0.55:
            health -= 16
        elif last_decay < 0.75:
            health -= 10
        elif last_decay > 1.05:
            health += 6
    if volume_decay is not None:
        if volume_decay < 0.55:
            health -= 9
        elif volume_decay < 0.75:
            health -= 5
        elif volume_decay > 1.10:
            health += 4
    health -= lower_high_streak * 8
    health += min(10, higher_low_streak * 5)
    if current_leg == "BOUNCING" and ongoing_bounce_pct is not None:
        health += min(8.0, ongoing_bounce_pct * 0.5)
    health = _clamp(health, 0.0, 100.0)

    if len(bounces) == 0:
        sequence_state = "FIRST PULLBACK / BOUNCE FORMING"
    elif lower_high_streak >= 2 and (last_decay is None or last_decay < 0.80):
        sequence_state = "BOUNCES WEAKENING"
    elif health >= 68:
        sequence_state = "HEALTHY MULTI-LEG CONTINUATION"
    elif health < 42:
        sequence_state = "DECAY / FAILURE RISK"
    else:
        sequence_state = "MIXED MULTI-BOUNCE STRUCTURE"

    return {
        "status": "ok",
        "detected": True,
        "sequence_state": sequence_state,
        "current_leg": current_leg,
        "current_leg_move_pct": round(current_leg_move_pct, 2)
        if current_leg_move_pct is not None
        else None,
        "impulse_low": round(impulse_low, 4),
        "impulse_low_index": int(low_idx),
        "impulse_high": round(impulse_high, 4),
        "impulse_peak_index": int(impulse_peak_idx),
        "impulse_move_pct": round(impulse_move_pct, 2),
        "completed_bounces": len(bounces),
        "next_bounce_number": len(bounces) + 1,
        "reference_peak": round(prior_peak_price, 4) if prior_peak_price is not None else None,
        "reference_peak_index": int(prior_peak_idx) if prior_peak_idx is not None else None,
        "current_dip_low": round(current_dip_low, 4) if current_dip_low is not None else None,
        "current_bounce_high": round(current_bounce_high, 4) if current_bounce_high is not None else None,
        "bounces": bounces,
        "bounce1_pct": round(bounce1, 2) if bounce1 is not None else None,
        "bounce2_pct": round(bounce2, 2) if bounce2 is not None else None,
        "bounce3_pct": round(bounce3, 2) if bounce3 is not None else None,
        "latest_bounce_pct": _num(last_bounce.get("bounce_pct"))
        if last_bounce
        else None,
        "previous_bounce_pct": _num(previous_bounce.get("bounce_pct"))
        if previous_bounce
        else None,
        "bounce_decay_ratio": round(last_decay, 3)
        if last_decay is not None
        else None,
        "bounce_volume_decay_ratio": round(volume_decay, 3)
        if volume_decay is not None
        else None,
        "lower_high_streak": int(lower_high_streak),
        "higher_low_streak": int(higher_low_streak),
        "current_pullback_pct": round(current_pullback_pct, 2)
        if current_pullback_pct is not None
        else None,
        "ongoing_bounce_pct": round(ongoing_bounce_pct, 2)
        if ongoing_bounce_pct is not None
        else None,
        "sequence_health_score": round(health, 1),
        "swing_threshold_pct": round(swing_pct, 2),
        "bar_spacing_minutes": round(bar_minutes, 2),
        "min_leg_minutes": round(min_leg_minutes, 2),
        "first_bounce_min_cycle_minutes": round(first_bounce_min_cycle_minutes, 2),
        "min_cycle_minutes": round(min_cycle_minutes, 2),
        "min_leg_bars": int(min_leg_bars),
        "first_bounce_min_cycle_bars": int(first_bounce_min_cycle_bars),
        "min_cycle_bars": int(min_cycle_bars),
        "min_recovery_fraction": min_recovery_fraction,
    }


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
