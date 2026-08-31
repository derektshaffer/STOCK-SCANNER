"""Shared causal market-structure primitives.

This module is the canonical interpretation layer between raw OHLCV bars and
higher-level concepts such as impulse/pullback and multi-bounce sequences.

Principles:
- confirmed pivots are causal: a HIGH/LOW is only confirmed by a later bar;
- no same-candle high/low ordering assumptions;
- local intraday noise informs swing sensitivity, not daily ATR alone;
- all downstream consumers receive the same swing objects and timestamps;
- already-confirmed pivots are immutable when later bars are appended.

Higher-level detectors should consume these objects instead of independently
reinterpreting raw candles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import median


STRUCTURE_VERSION = "market-structure-v2-breaks-and-trend"


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _clamp(value, low, high):
    return max(low, min(high, value))


def _parse_dt(value):
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            numeric = float(text)
            if numeric > 10_000_000_000:
                numeric /= 1000.0
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def normalize_bars(bars):
    rows = []
    for source_index, bar in enumerate(bars or []):
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
        if h < l:
            h, l = l, h
        rows.append(
            {
                "index": len(rows),
                "source_index": source_index,
                "t": bar.get("t"),
                "dt": _parse_dt(bar.get("t")),
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
            }
        )
    return rows


def bar_spacing_minutes(rows):
    diffs = []
    previous = None
    for row in rows or []:
        current = row.get("dt") or _parse_dt(row.get("t"))
        if current is None:
            continue
        if previous is not None:
            diff = (current - previous).total_seconds() / 60.0
            if 0 < diff <= 60:
                diffs.append(diff)
        previous = current
    return max(0.25, float(median(diffs))) if diffs else 1.0


def local_noise_pct(rows, window=30):
    """Robust recent bar noise estimate in percent.

    Uses the median of true-range-like one-bar movement so one huge wick cannot
    make every later swing require a huge percentage move.
    """
    clean = rows or []
    if len(clean) < 2:
        return 1.0
    values = []
    start = max(1, len(clean) - max(8, int(window)))
    for i in range(start, len(clean)):
        row = clean[i]
        prev = clean[i - 1]
        prev_close = _num(prev.get("c"))
        if not prev_close or prev_close <= 0:
            continue
        h = _num(row.get("h"))
        l = _num(row.get("l"))
        c = _num(row.get("c"))
        if h is None or l is None or c is None:
            continue
        true_range = max(
            h - l,
            abs(h - prev_close),
            abs(l - prev_close),
            abs(c - prev_close),
        )
        values.append(true_range / prev_close * 100.0)
    if not values:
        return 1.0
    return float(median(values))


def _pct_up(low, high):
    return (high / low - 1.0) * 100.0 if low and low > 0 else 0.0


def _pct_down(high, low):
    return (high / low - 1.0) * 100.0 if low and low > 0 else 0.0


def _time_distance_minutes(rows, a, b, spacing):
    if a is None or b is None:
        return None
    if 0 <= a < len(rows) and 0 <= b < len(rows):
        da = rows[a].get("dt")
        db = rows[b].get("dt")
        if da is not None and db is not None:
            return max(0.0, (db - da).total_seconds() / 60.0)
    return max(0.0, (b - a) * spacing)


def extract_market_structure(
    bars,
    *,
    swing_threshold_pct=None,
    min_swing_pct=0.75,
    max_swing_pct=3.0,
    min_leg_minutes=2.0,
):
    """Return canonical confirmed swings plus the currently developing leg.

    A pivot at bar P is only confirmed by a *later* bar C. This guarantees:
    - no same-candle high/low order assumption;
    - appending future bars cannot move an already-confirmed pivot;
    - HIGH/LOW pivots alternate by construction.
    """
    rows = normalize_bars(bars)
    if len(rows) < 3:
        return {
            "version": STRUCTURE_VERSION,
            "status": "insufficient_data",
            "rows": rows,
            "confirmed_swings": [],
            "developing": None,
        }

    spacing = bar_spacing_minutes(rows)
    noise = local_noise_pct(rows)
    threshold = (
        float(swing_threshold_pct)
        if swing_threshold_pct is not None
        else _clamp(noise * 1.45, min_swing_pct, max_swing_pct)
    )
    min_leg_bars = max(1, int(round(max(min_leg_minutes, spacing) / spacing)))

    swings = []
    direction = None  # "up" means tracking candidate HIGH, "down" candidate LOW.

    candidate_low = rows[0]["l"]
    candidate_low_idx = 0
    candidate_high = rows[0]["h"]
    candidate_high_idx = 0

    def append_pivot(kind, idx, price, confirm_idx):
        if idx is None or confirm_idx is None or confirm_idx <= idx:
            return False
        if swings and swings[-1]["kind"] == kind:
            return False
        if swings and idx <= swings[-1]["index"]:
            return False
        swings.append(
            {
                "kind": kind,
                "index": int(idx),
                "source_index": int(rows[idx]["source_index"]),
                "time": rows[idx].get("t"),
                "price": round(float(price), 6),
                "confirmed_index": int(confirm_idx),
                "confirmed_source_index": int(rows[confirm_idx]["source_index"]),
                "confirmed_time": rows[confirm_idx].get("t"),
                "bars_to_confirm": int(confirm_idx - idx),
                "minutes_to_confirm": round(
                    _time_distance_minutes(rows, idx, confirm_idx, spacing) or 0.0,
                    3,
                ),
            }
        )
        return True

    for i in range(1, len(rows)):
        row = rows[i]

        if direction is None:
            if row["l"] < candidate_low:
                candidate_low = row["l"]
                candidate_low_idx = i
            if row["h"] > candidate_high:
                candidate_high = row["h"]
                candidate_high_idx = i

            # Only confirm an initial LOW using a later bar. If this bar itself
            # just created the low, its high cannot confirm it because OHLC does
            # not reveal intrabar order.
            if (
                candidate_low_idx < i
                and i - candidate_low_idx >= min_leg_bars
                and _pct_up(candidate_low, row["h"]) >= threshold
            ):
                append_pivot("LOW", candidate_low_idx, candidate_low, i)
                direction = "up"
                candidate_high = row["h"]
                candidate_high_idx = i
                continue

            if (
                candidate_high_idx < i
                and i - candidate_high_idx >= min_leg_bars
                and _pct_down(candidate_high, row["l"]) >= threshold
            ):
                append_pivot("HIGH", candidate_high_idx, candidate_high, i)
                direction = "down"
                candidate_low = row["l"]
                candidate_low_idx = i
                continue

        elif direction == "up":
            if row["h"] > candidate_high:
                candidate_high = row["h"]
                candidate_high_idx = i

            prior_idx = int(swings[-1]["index"]) if swings else None
            if (
                candidate_high_idx < i
                and prior_idx is not None
                and candidate_high_idx - prior_idx >= min_leg_bars
                and _pct_down(candidate_high, row["l"]) >= threshold
            ):
                if append_pivot("HIGH", candidate_high_idx, candidate_high, i):
                    direction = "down"
                    # The confirmation bar occurs after the HIGH pivot, so its
                    # low is causally safe as the first candidate for the next
                    # downswing.
                    candidate_low = row["l"]
                    candidate_low_idx = i

        else:  # direction == "down"
            if row["l"] < candidate_low:
                candidate_low = row["l"]
                candidate_low_idx = i

            prior_idx = int(swings[-1]["index"]) if swings else None
            if (
                candidate_low_idx < i
                and prior_idx is not None
                and candidate_low_idx - prior_idx >= min_leg_bars
                and i - candidate_low_idx >= min_leg_bars
                and _pct_up(candidate_low, row["h"]) >= threshold
            ):
                if append_pivot("LOW", candidate_low_idx, candidate_low, i):
                    direction = "up"
                    candidate_high = row["h"]
                    candidate_high_idx = i

    developing = None
    if direction == "up":
        developing = {
            "kind": "HIGH",
            "index": int(candidate_high_idx),
            "source_index": int(rows[candidate_high_idx]["source_index"]),
            "time": rows[candidate_high_idx].get("t"),
            "price": round(float(candidate_high), 6),
            "state": "developing",
        }
    elif direction == "down":
        developing = {
            "kind": "LOW",
            "index": int(candidate_low_idx),
            "source_index": int(rows[candidate_low_idx]["source_index"]),
            "time": rows[candidate_low_idx].get("t"),
            "price": round(float(candidate_low), 6),
            "state": "developing",
        }

    return {
        "version": STRUCTURE_VERSION,
        "status": "ok",
        "rows": rows,
        "confirmed_swings": swings,
        "developing": developing,
        "direction": direction,
        "local_noise_pct": round(noise, 4),
        "swing_threshold_pct": round(threshold, 4),
        "bar_spacing_minutes": round(spacing, 4),
        "min_leg_minutes": float(min_leg_minutes),
        "min_leg_bars": int(min_leg_bars),
    }


def _streak(values, comparison):
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if comparison(values[i], values[i - 1]):
            count += 1
        else:
            break
    return count


def swing_trend_context(bars_or_structure):
    """Canonical higher-high/lower-high and higher-low/lower-low structure."""
    structure = (
        bars_or_structure
        if isinstance(bars_or_structure, dict)
        and "confirmed_swings" in bars_or_structure
        else extract_market_structure(bars_or_structure)
    )
    swings = structure.get("confirmed_swings") or []
    highs = [s for s in swings if s.get("kind") == "HIGH"]
    lows = [s for s in swings if s.get("kind") == "LOW"]
    high_prices = [_num(s.get("price")) for s in highs]
    low_prices = [_num(s.get("price")) for s in lows]
    high_prices = [x for x in high_prices if x is not None]
    low_prices = [x for x in low_prices if x is not None]

    lower_highs = _streak(high_prices, lambda cur, prev: cur < prev * 0.997)
    higher_highs = _streak(high_prices, lambda cur, prev: cur > prev * 1.003)
    lower_lows = _streak(low_prices, lambda cur, prev: cur < prev * 0.997)
    higher_lows = _streak(low_prices, lambda cur, prev: cur > prev * 1.003)

    if lower_highs >= 1 and lower_lows >= 1:
        state = "DOWNTREND STRUCTURE"
    elif higher_highs >= 1 and higher_lows >= 1:
        state = "UPTREND STRUCTURE"
    elif lower_highs >= 1:
        state = "LOWER-HIGH PRESSURE"
    elif higher_lows >= 1:
        state = "HIGHER-LOW SUPPORT"
    else:
        state = "MIXED / UNCONFIRMED"

    return {
        "structure_version": STRUCTURE_VERSION,
        "state": state,
        "confirmed_swing_count": len(swings),
        "confirmed_high_count": len(highs),
        "confirmed_low_count": len(lows),
        "lower_high_streak": int(lower_highs),
        "higher_high_streak": int(higher_highs),
        "lower_low_streak": int(lower_lows),
        "higher_low_streak": int(higher_lows),
        "latest_high": highs[-1] if highs else None,
        "latest_low": lows[-1] if lows else None,
        "market_structure": structure,
    }


def _established_range_breaks(rows, noise_pct, break_buffer_pct):
    """Causal breaks of tight ranges established entirely before the event bar."""
    if len(rows) < 5:
        return {"upside": [], "downside": []}

    max_range_width = _clamp(noise_pct * 3.0, 1.0, 4.0)
    touch_tolerance = _clamp(noise_pct * 0.35, 0.10, 0.60)
    upside = []
    downside = []

    for j in range(4, len(rows)):
        prior = rows[max(0, j - 12):j]
        if len(prior) < 4:
            continue
        ceiling = max(row["h"] for row in prior)
        floor = min(row["l"] for row in prior)
        if floor <= 0:
            continue
        width_pct = (ceiling / floor - 1.0) * 100.0
        if width_pct > max_range_width:
            continue

        ceiling_touches = sum(
            1 for row in prior
            if abs(row["h"] / ceiling - 1.0) * 100.0 <= touch_tolerance
        )
        floor_touches = sum(
            1 for row in prior
            if abs(row["l"] / floor - 1.0) * 100.0 <= touch_tolerance
        )
        row = rows[j]

        if (
            ceiling_touches >= 2
            and row["h"] >= ceiling * (1.0 + break_buffer_pct / 100.0)
        ):
            upside.append(
                {
                    "level_kind": "RANGE_HIGH",
                    "level_source": "established_range_ceiling",
                    "level": round(ceiling, 6),
                    "level_time": prior[-1].get("t"),
                    "level_confirmed_time": prior[-1].get("t"),
                    "range_start_time": prior[0].get("t"),
                    "range_width_pct": round(width_pct, 3),
                    "range_touches": int(ceiling_touches),
                    "event_index": j,
                    "event_source_index": int(row["source_index"]),
                    "event_time": row.get("t"),
                    "bars_since": int(len(rows) - 1 - j),
                }
            )

        if (
            floor_touches >= 2
            and row["l"] <= floor * (1.0 - break_buffer_pct / 100.0)
        ):
            downside.append(
                {
                    "level_kind": "RANGE_LOW",
                    "level_source": "established_range_floor",
                    "level": round(floor, 6),
                    "level_time": prior[-1].get("t"),
                    "level_confirmed_time": prior[-1].get("t"),
                    "range_start_time": prior[0].get("t"),
                    "range_width_pct": round(width_pct, 3),
                    "range_touches": int(floor_touches),
                    "event_index": j,
                    "event_source_index": int(row["source_index"]),
                    "event_time": row.get("t"),
                    "bars_since": int(len(rows) - 1 - j),
                }
            )

    return {"upside": upside, "downside": downside}


def break_of_structure_context(bars):
    """Canonical upside/downside breaks of already-confirmed swing levels.

    A level may only be broken after that swing was itself confirmed, so the
    event is available point-in-time and cannot rely on a future pivot.
    """
    structure = extract_market_structure(bars)
    rows = structure.get("rows") or []
    swings = structure.get("confirmed_swings") or []
    if len(rows) < 4:
        return {
            "status": "insufficient_data",
            "structure_version": STRUCTURE_VERSION,
            "upside": None,
            "downside": None,
            "market_structure": structure,
        }

    noise = _num(structure.get("local_noise_pct")) or 1.0
    break_buffer_pct = _clamp(noise * 0.08, 0.05, 0.35)
    hold_buffer_pct = _clamp(noise * 0.04, 0.03, 0.18)

    def latest_break(kind):
        events = []
        for swing in swings:
            if swing.get("kind") != kind:
                continue
            level = _num(swing.get("price"))
            confirmed_idx = swing.get("confirmed_index")
            if level is None or confirmed_idx is None:
                continue
            confirmed_idx = int(confirmed_idx)
            for j in range(confirmed_idx + 1, len(rows)):
                row = rows[j]
                broke = (
                    row["h"] >= level * (1.0 + break_buffer_pct / 100.0)
                    if kind == "HIGH"
                    else row["l"] <= level * (1.0 - break_buffer_pct / 100.0)
                )
                if broke:
                    events.append(
                        {
                            "level_kind": kind,
                            "level_source": (
                                "confirmed_swing_high"
                                if kind == "HIGH"
                                else "confirmed_swing_low"
                            ),
                            "level": round(level, 6),
                            "level_time": swing.get("time"),
                            "level_confirmed_time": swing.get("confirmed_time"),
                            "event_index": j,
                            "event_source_index": int(rows[j]["source_index"]),
                            "event_time": rows[j].get("t"),
                            "bars_since": int(len(rows) - 1 - j),
                        }
                    )
                    break
        return max(events, key=lambda e: e["event_index"]) if events else None

    upside = latest_break("HIGH")
    downside = latest_break("LOW")

    range_breaks = _established_range_breaks(
        rows,
        noise,
        break_buffer_pct,
    )
    range_up = max(
        range_breaks.get("upside") or [],
        key=lambda e: e["event_index"],
        default=None,
    )
    range_down = max(
        range_breaks.get("downside") or [],
        key=lambda e: e["event_index"],
        default=None,
    )
    if range_up and (
        upside is None or range_up["event_index"] >= upside["event_index"]
    ):
        upside = range_up
    if range_down and (
        downside is None or range_down["event_index"] >= downside["event_index"]
    ):
        downside = range_down

    current = rows[-1]["c"]

    if upside:
        level = float(upside["level"])
        upside["holding"] = bool(
            current >= level * (1.0 - hold_buffer_pct / 100.0)
        )
        upside["failed"] = bool(
            current < level * (1.0 - hold_buffer_pct / 100.0)
        )
        upside["extension_pct"] = round((current / level - 1.0) * 100.0, 3)
        upside["recent"] = bool(upside["bars_since"] <= 4)

    if downside:
        level = float(downside["level"])
        downside["holding"] = bool(
            current <= level * (1.0 + hold_buffer_pct / 100.0)
        )
        downside["reclaimed"] = bool(
            current > level * (1.0 + hold_buffer_pct / 100.0)
        )
        downside["extension_pct"] = round((current / level - 1.0) * 100.0, 3)
        downside["recent"] = bool(downside["bars_since"] <= 4)

    latest_highs = [s for s in swings if s.get("kind") == "HIGH"]
    latest_lows = [s for s in swings if s.get("kind") == "LOW"]

    return {
        "status": "ok",
        "structure_version": STRUCTURE_VERSION,
        "break_buffer_pct": round(break_buffer_pct, 4),
        "hold_buffer_pct": round(hold_buffer_pct, 4),
        "upside": upside,
        "downside": downside,
        "reference_high": latest_highs[-1] if latest_highs else None,
        "reference_low": latest_lows[-1] if latest_lows else None,
        "market_structure": structure,
    }


def breakout_behavior_context(bars):
    """Compatibility view for Scanner breakout features using shared levels."""
    ctx = break_of_structure_context(bars)
    rows = (ctx.get("market_structure") or {}).get("rows") or []
    upside = ctx.get("upside") or {}
    ref = ctx.get("reference_high") or {}
    current = rows[-1]["c"] if rows else None
    level = _num(upside.get("level"))
    if level is None:
        level = _num(ref.get("price"))

    return {
        "structure_version": STRUCTURE_VERSION,
        "breakout_recent": 1.0 if upside.get("recent") else 0.0,
        "breakout_holding": 1.0 if upside.get("holding") else 0.0,
        "failed_breakout": 1.0 if upside.get("failed") else 0.0,
        "breakout_extension_pct": (
            round((current / level - 1.0) * 100.0, 3)
            if current is not None and level
            else None
        ),
        "breakout_bars_since": (
            float(upside.get("bars_since"))
            if upside.get("bars_since") is not None
            else None
        ),
        "breakout_level": round(level, 6) if level is not None else None,
        "breakout_level_time": (
            upside.get("level_time") if upside else ref.get("time")
        ),
        "breakout_event_time": upside.get("event_time") if upside else None,
        "market_structure": ctx.get("market_structure"),
    }


def structural_reversal_context(bars):
    """Shared swing-trend and downside-break evidence for reversal consumers."""
    structure = extract_market_structure(bars)
    trend = swing_trend_context(structure)
    breaks = break_of_structure_context(bars)
    downside = breaks.get("downside") or {}
    upside = breaks.get("upside") or {}
    return {
        "structure_version": STRUCTURE_VERSION,
        "state": trend.get("state"),
        "lower_high_streak": trend.get("lower_high_streak", 0),
        "higher_high_streak": trend.get("higher_high_streak", 0),
        "lower_low_streak": trend.get("lower_low_streak", 0),
        "higher_low_streak": trend.get("higher_low_streak", 0),
        "downside_break_recent": bool(downside.get("recent")),
        "downside_break_holding": bool(downside.get("holding")),
        "downside_break_level": downside.get("level"),
        "upside_break_recent": bool(upside.get("recent")),
        "upside_break_holding": bool(upside.get("holding")),
        "failed_upside_break": bool(upside.get("failed")),
        "market_structure": structure,
    }


def _up_impulse_candidates(structure, min_impulse_pct):
    rows = structure.get("rows") or []
    swings = structure.get("confirmed_swings") or []
    candidates = []
    for i in range(len(swings) - 1):
        low = swings[i]
        high = swings[i + 1]
        if low.get("kind") != "LOW" or high.get("kind") != "HIGH":
            continue
        low_price = _num(low.get("price"))
        high_price = _num(high.get("price"))
        if not low_price or not high_price or high_price <= low_price:
            continue
        move_pct = _pct_up(low_price, high_price)
        if move_pct < min_impulse_pct:
            continue
        duration = int(high.get("index") or 0) - int(low.get("index") or 0)
        if duration < 2:
            continue
        age = max(0, len(rows) - 1 - int(high.get("index") or 0))
        recency = max(0.35, 1.0 - age / max(30.0, len(rows) * 0.9))
        candidates.append(
            {
                "low": low,
                "high": high,
                "low_idx": int(low["index"]),
                "peak_idx": int(high["index"]),
                "low_price": low_price,
                "peak_price": high_price,
                "move_pct": move_pct,
                "duration_bars": duration,
                "score": move_pct * recency,
            }
        )
    return candidates


def select_dominant_up_impulse(
    structure,
    *,
    atr_pct=None,
    min_impulse_pct=None,
):
    """Choose a canonical LOW->HIGH impulse without erasing earlier structure."""
    rows = structure.get("rows") or []
    if not rows:
        return None
    atrp = _num(atr_pct) or 8.0
    min_impulse = (
        float(min_impulse_pct)
        if min_impulse_pct is not None
        else max(6.0, min(16.0, atrp * 0.70))
    )
    candidates = _up_impulse_candidates(structure, min_impulse)
    if not candidates:
        return None

    best = max(candidates, key=lambda item: item["score"])

    # If an earlier impulse captured nearly all of a later marginal new high,
    # and a genuine confirmed LOW exists between those peaks, keep the earlier
    # impulse anchor. That prevents a rebound/new-high from erasing Bounce #1.
    earlier = [
        row for row in candidates
        if row["peak_idx"] < best["peak_idx"]
        and row["move_pct"] >= best["move_pct"] * 0.90
        and best["peak_price"] <= row["peak_price"] * 1.02
    ]
    swings = structure.get("confirmed_swings") or []
    for candidate in sorted(earlier, key=lambda item: item["peak_idx"]):
        between_lows = [
            swing for swing in swings
            if swing.get("kind") == "LOW"
            and candidate["peak_idx"] < int(swing.get("index") or -1) < best["peak_idx"]
        ]
        if between_lows:
            return {
                **candidate,
                "structure_version": STRUCTURE_VERSION,
                "anchor_reason": "earlier_impulse_preserved_across_confirmed_pullback",
            }

    return {
        **best,
        "structure_version": STRUCTURE_VERSION,
        "anchor_reason": "dominant_confirmed_up_leg",
    }


def impulse_pullback_context(
    bars,
    *,
    current_price=None,
    atr_pct=None,
    min_impulse_pct=None,
):
    structure = extract_market_structure(bars)
    rows = structure.get("rows") or []
    if len(rows) < 7:
        return {
            "status": "insufficient_data",
            "detected": False,
            "structure_version": STRUCTURE_VERSION,
        }

    impulse = select_dominant_up_impulse(
        structure,
        atr_pct=atr_pct,
        min_impulse_pct=min_impulse_pct,
    )
    if not impulse:
        return {
            "status": "no_clear_impulse",
            "detected": False,
            "structure_version": STRUCTURE_VERSION,
            "market_structure": structure,
        }

    price = _num(current_price) or rows[-1]["c"]
    low_idx = impulse["low_idx"]
    peak_idx = impulse["peak_idx"]
    low = impulse["low_price"]
    peak = impulse["peak_price"]
    run = peak - low
    if run <= 0:
        return {
            "status": "no_clear_impulse",
            "detected": False,
            "structure_version": STRUCTURE_VERSION,
        }

    swings = structure.get("confirmed_swings") or []
    post_lows = [
        swing for swing in swings
        if swing.get("kind") == "LOW" and int(swing.get("index") or -1) > peak_idx
    ]
    first_low = post_lows[0] if post_lows else None

    developing = structure.get("developing") or {}
    if first_low is not None:
        trough_idx = int(first_low["index"])
        trough = _num(first_low.get("price"))
        trough_confirmed = True
    elif (
        developing.get("kind") == "LOW"
        and int(developing.get("index") or -1) > peak_idx
    ):
        trough_idx = int(developing["index"])
        trough = _num(developing.get("price"))
        trough_confirmed = False
    else:
        post = rows[peak_idx + 1 :]
        if not post:
            trough_idx = peak_idx
            trough = peak
        else:
            rel = min(range(len(post)), key=lambda i: post[i]["l"])
            trough_idx = peak_idx + 1 + rel
            trough = post[rel]["l"]
        trough_confirmed = False

    trough = trough if trough is not None else peak
    max_retrace = (peak - trough) / run * 100.0
    current_retrace = (peak - price) / run * 100.0
    recovery = max_retrace - current_retrace

    rebound_high = None
    if first_low is not None:
        rebound_highs = [
            swing for swing in swings
            if swing.get("kind") == "HIGH"
            and int(swing.get("index") or -1) > int(first_low["index"])
        ]
        rebound_high = rebound_highs[0] if rebound_highs else None

    bounce_confirmed = bool(
        first_low is not None
        and rebound_high is not None
        and 15 <= max_retrace <= 80
    )

    impulse_vols = [
        rows[i]["v"]
        for i in range(low_idx, peak_idx + 1)
        if rows[i]["v"] > 0
    ]
    pull_vols = [
        rows[i]["v"]
        for i in range(peak_idx + 1, trough_idx + 1)
        if rows[i]["v"] > 0
    ]
    impulse_avg = sum(impulse_vols) / len(impulse_vols) if impulse_vols else None
    pull_avg = sum(pull_vols) / len(pull_vols) if pull_vols else None
    vol_ratio = (
        pull_avg / impulse_avg
        if impulse_avg and pull_avg is not None
        else None
    )

    levels = {}
    for label, frac in (
        ("25%", 0.25),
        ("33%", 1 / 3),
        ("38.2%", 0.382),
        ("50%", 0.50),
        ("61.8%", 0.618),
    ):
        levels[label] = round(peak - run * frac, 4)

    if max_retrace > 78 and current_retrace > 62:
        phase = "DEEP / POSSIBLE FAILURE"
    elif bounce_confirmed:
        phase = "BOUNCE CONFIRMED"
    elif first_low is not None or (
        developing.get("kind") == "HIGH"
        and int(developing.get("index") or -1) > trough_idx
    ):
        phase = "BOUNCE DEVELOPING"
    elif current_retrace < 20:
        phase = "STILL EXTENDED"
    elif current_retrace <= 62:
        phase = "PULLBACK FORMING"
    else:
        phase = "DEEP PULLBACK"

    return {
        "status": "ok",
        "detected": True,
        "phase": phase,
        "structure_version": STRUCTURE_VERSION,
        "impulse_anchor_reason": impulse.get("anchor_reason"),
        "impulse_low": round(low, 4),
        "impulse_low_index": int(low_idx),
        "impulse_low_time": rows[low_idx].get("t"),
        "impulse_high": round(peak, 4),
        "impulse_high_index": int(peak_idx),
        "impulse_high_time": rows[peak_idx].get("t"),
        "impulse_move_pct": round(impulse["move_pct"], 2),
        "impulse_duration_bars": int(impulse["duration_bars"]),
        "peak_bars_ago": int(len(rows) - 1 - peak_idx),
        "pullback_low": round(trough, 4),
        "pullback_low_index": int(trough_idx),
        "pullback_low_time": rows[trough_idx].get("t"),
        "pullback_low_confirmed": bool(trough_confirmed),
        "current_retracement_pct": round(current_retrace, 2),
        "max_retracement_pct": round(max_retrace, 2),
        "bounce_recovery_pct": round(recovery, 2),
        "bounce_confirmed": bounce_confirmed,
        "bounce_peak": (
            round(_num(rebound_high.get("price")), 4)
            if rebound_high and _num(rebound_high.get("price")) is not None
            else None
        ),
        "bounce_peak_time": rebound_high.get("time") if rebound_high else None,
        "pullback_volume_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
        "pullback_volume_contracting": bool(
            vol_ratio is not None and vol_ratio < 0.85
        ),
        "levels": levels,
        "default_zone_low": levels["50%"],
        "default_zone_high": levels["33%"],
        "run_size": round(run, 4),
        "market_structure": structure,
    }


def bounce_sequence_context(
    bars,
    *,
    current_price=None,
    atr_pct=None,
    min_impulse_pct=None,
):
    structure = extract_market_structure(bars)
    rows = structure.get("rows") or []
    if len(rows) < 8:
        return {
            "status": "insufficient_data",
            "detected": False,
            "completed_bounces": 0,
            "bounces": [],
            "structure_version": STRUCTURE_VERSION,
        }

    impulse = select_dominant_up_impulse(
        structure,
        atr_pct=atr_pct,
        min_impulse_pct=min_impulse_pct,
    )
    if not impulse:
        return {
            "status": "no_clear_impulse",
            "detected": False,
            "completed_bounces": 0,
            "bounces": [],
            "structure_version": STRUCTURE_VERSION,
            "market_structure": structure,
        }

    peak_idx = impulse["peak_idx"]
    impulse_high = impulse["peak_price"]
    impulse_low = impulse["low_price"]
    run_size = impulse_high - impulse_low
    swings = [
        swing for swing in (structure.get("confirmed_swings") or [])
        if int(swing.get("index") or -1) > peak_idx
    ]

    bounces = []
    prior_peak_idx = peak_idx
    prior_peak_price = impulse_high
    i = 0
    while i < len(swings) - 1:
        low_swing = swings[i]
        if low_swing.get("kind") != "LOW":
            i += 1
            continue
        high_swing = swings[i + 1]
        if high_swing.get("kind") != "HIGH":
            i += 1
            continue

        trough_idx = int(low_swing["index"])
        bounce_peak_idx = int(high_swing["index"])
        trough = _num(low_swing.get("price"))
        bounce_peak = _num(high_swing.get("price"))
        if (
            trough is None
            or bounce_peak is None
            or trough >= prior_peak_price
            or bounce_peak <= trough
        ):
            i += 2
            continue

        pullback_range = prior_peak_price - trough
        rebound_range = bounce_peak - trough
        recovery_fraction = (
            rebound_range / pullback_range if pullback_range > 0 else 0.0
        )

        # A shared pivot already requires a meaningful reversal. Requiring
        # recovery of the actual preceding pullback adds pattern semantics
        # without inventing another raw-candle zig-zag detector.
        if recovery_fraction < 0.35:
            i += 2
            continue

        pullback_drop_pct = (
            (prior_peak_price / trough - 1.0) * 100.0 if trough > 0 else None
        )
        bounce_pct = _pct_up(trough, bounce_peak)
        recovery_to_prior_peak_pct = (
            rebound_range / pullback_range * 100.0
            if pullback_range > 0
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
            sum(pullback_vols) / len(pullback_vols) if pullback_vols else None
        )
        bounce_avg_vol = (
            sum(bounce_vols) / len(bounce_vols) if bounce_vols else None
        )

        previous = bounces[-1] if bounces else None
        previous_pct = _num(previous.get("bounce_pct")) if previous else None
        decay = (
            bounce_pct / previous_pct
            if previous_pct and previous_pct > 0
            else None
        )

        bounces.append(
            {
                "number": len(bounces) + 1,
                "pullback_low": round(trough, 4),
                "pullback_low_index": trough_idx,
                "pullback_low_time": low_swing.get("time"),
                "bounce_peak": round(bounce_peak, 4),
                "bounce_peak_index": bounce_peak_idx,
                "bounce_peak_time": high_swing.get("time"),
                "confirmation_time": high_swing.get("confirmed_time"),
                "pullback_drop_pct": (
                    round(pullback_drop_pct, 2)
                    if pullback_drop_pct is not None
                    else None
                ),
                "bounce_pct": round(bounce_pct, 2),
                "recovery_fraction": round(recovery_fraction, 3),
                "recovery_to_prior_peak_pct": (
                    round(recovery_to_prior_peak_pct, 2)
                    if recovery_to_prior_peak_pct is not None
                    else None
                ),
                "lower_high": bool(bounce_peak < prior_peak_price * 0.995),
                "higher_high": bool(bounce_peak > prior_peak_price * 1.005),
                "new_session_high": bool(bounce_peak > impulse_high * 1.005),
                "pullback_bars": int(trough_idx - prior_peak_idx),
                "bounce_bars": int(bounce_peak_idx - trough_idx),
                "pullback_avg_volume": (
                    round(pullback_avg_vol, 2)
                    if pullback_avg_vol is not None
                    else None
                ),
                "bounce_avg_volume": (
                    round(bounce_avg_vol, 2)
                    if bounce_avg_vol is not None
                    else None
                ),
                "decay_vs_previous": round(decay, 3) if decay is not None else None,
            }
        )
        prior_peak_idx = bounce_peak_idx
        prior_peak_price = bounce_peak
        i += 2

    price = _num(current_price) or rows[-1]["c"]
    current_leg = "PULLBACK"
    current_dip_low = None
    current_bounce_high = None
    current_leg_move_pct = None
    current_pullback_pct = None
    ongoing_bounce_pct = None

    post_peak_rows = rows[prior_peak_idx + 1 :]
    if post_peak_rows:
        developing = structure.get("developing") or {}
        last_swing = (structure.get("confirmed_swings") or [])[-1:] or [None]
        last_swing = last_swing[0]

        if (
            last_swing
            and last_swing.get("kind") == "LOW"
            and int(last_swing.get("index") or -1) > prior_peak_idx
        ):
            current_leg = "BOUNCING"
            current_dip_low = _num(last_swing.get("price"))
            low_idx = int(last_swing.get("index"))
            current_bounce_high = max(row["h"] for row in rows[low_idx:])
            ongoing_bounce_pct = (
                _pct_up(current_dip_low, current_bounce_high)
                if current_dip_low
                else None
            )
            current_leg_move_pct = ongoing_bounce_pct
        else:
            current_leg = "PULLBACK"
            current_dip_low = min(row["l"] for row in post_peak_rows)
            current_pullback_pct = (
                (prior_peak_price / current_dip_low - 1.0) * 100.0
                if current_dip_low > 0
                else None
            )
            current_leg_move_pct = current_pullback_pct

    bounce1 = _num(bounces[0].get("bounce_pct")) if len(bounces) >= 1 else None
    bounce2 = _num(bounces[1].get("bounce_pct")) if len(bounces) >= 2 else None
    bounce3 = _num(bounces[2].get("bounce_pct")) if len(bounces) >= 3 else None
    last_bounce = bounces[-1] if bounces else None
    previous_bounce = bounces[-2] if len(bounces) >= 2 else None
    last_decay = (
        _num(last_bounce.get("decay_vs_previous")) if last_bounce else None
    )

    first_bounce_vol = (
        _num(bounces[0].get("bounce_avg_volume")) if bounces else None
    )
    last_bounce_vol = (
        _num(last_bounce.get("bounce_avg_volume")) if last_bounce else None
    )
    volume_decay = (
        last_bounce_vol / first_bounce_vol
        if first_bounce_vol and last_bounce_vol is not None
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
        for j in range(len(troughs) - 1, 0, -1):
            if troughs[j] is not None and troughs[j - 1] is not None and troughs[j] > troughs[j - 1] * 1.002:
                higher_low_streak += 1
            else:
                break

    health = 50.0
    if bounces:
        health += 8.0
    if last_bounce:
        recovery = _num(last_bounce.get("recovery_to_prior_peak_pct"))
        if recovery is not None:
            health += _clamp((recovery - 45.0) * 0.18, -8.0, 8.0)
    if last_decay is not None:
        if last_decay < 0.55:
            health -= 14.0
        elif last_decay < 0.75:
            health -= 7.0
        elif last_decay >= 0.95:
            health += 5.0
    health -= min(18.0, lower_high_streak * 6.0)
    health += min(12.0, higher_low_streak * 4.0)
    if volume_decay is not None and volume_decay < 0.55:
        health -= 6.0
    if current_leg == "BOUNCING" and ongoing_bounce_pct is not None:
        health += min(8.0, ongoing_bounce_pct * 0.5)
    health = _clamp(health, 0.0, 100.0)

    if len(bounces) == 0:
        sequence_state = "FIRST PULLBACK / BOUNCE FORMING"
    elif lower_high_streak >= 2 and (last_decay is None or last_decay < 0.85):
        sequence_state = "BOUNCES WEAKENING"
    elif higher_low_streak >= 1 and health >= 60:
        sequence_state = "HEALTHY MULTI-LEG CONTINUATION"
    elif health < 35:
        sequence_state = "DECAY / FAILURE RISK"
    else:
        sequence_state = "MIXED MULTI-BOUNCE STRUCTURE"

    return {
        "status": "ok",
        "detected": True,
        "structure_version": STRUCTURE_VERSION,
        "sequence_state": sequence_state,
        "impulse_low": round(impulse_low, 4),
        "impulse_low_index": int(impulse["low_idx"]),
        "impulse_high": round(impulse_high, 4),
        "impulse_peak_index": int(peak_idx),
        "impulse_peak_time": rows[peak_idx].get("t"),
        "impulse_move_pct": round(impulse["move_pct"], 2),
        "reference_peak": round(prior_peak_price, 4),
        "reference_peak_index": int(prior_peak_idx),
        "completed_bounces": len(bounces),
        "next_bounce_number": len(bounces) + 1,
        "current_leg": current_leg,
        "current_leg_move_pct": (
            round(current_leg_move_pct, 2)
            if current_leg_move_pct is not None
            else None
        ),
        "current_dip_low": round(current_dip_low, 4) if current_dip_low is not None else None,
        "current_bounce_high": round(current_bounce_high, 4) if current_bounce_high is not None else None,
        "bounces": bounces,
        "bounce1_pct": round(bounce1, 2) if bounce1 is not None else None,
        "bounce2_pct": round(bounce2, 2) if bounce2 is not None else None,
        "bounce3_pct": round(bounce3, 2) if bounce3 is not None else None,
        "latest_bounce_pct": _num(last_bounce.get("bounce_pct")) if last_bounce else None,
        "previous_bounce_pct": _num(previous_bounce.get("bounce_pct")) if previous_bounce else None,
        "bounce_decay_ratio": round(last_decay, 3) if last_decay is not None else None,
        "bounce_volume_decay_ratio": round(volume_decay, 3) if volume_decay is not None else None,
        "lower_high_streak": int(lower_high_streak),
        "higher_low_streak": int(higher_low_streak),
        "current_pullback_pct": round(current_pullback_pct, 2) if current_pullback_pct is not None else None,
        "ongoing_bounce_pct": round(ongoing_bounce_pct, 2) if ongoing_bounce_pct is not None else None,
        "sequence_health_score": round(health, 1),
        "swing_threshold_pct": structure.get("swing_threshold_pct"),
        "pullback_threshold_pct": structure.get("swing_threshold_pct"),
        "bar_spacing_minutes": structure.get("bar_spacing_minutes"),
        "min_leg_minutes": structure.get("min_leg_minutes"),
        "first_bounce_min_cycle_minutes": max(3.0, (structure.get("bar_spacing_minutes") or 1.0) * 1.5),
        "min_cycle_minutes": max(5.0, (structure.get("bar_spacing_minutes") or 1.0) * 2.0),
        "min_leg_bars": structure.get("min_leg_bars"),
        "first_bounce_min_cycle_bars": max(2, int(round(3.0 / (structure.get("bar_spacing_minutes") or 1.0)))),
        "min_cycle_bars": max(2, int(round(5.0 / (structure.get("bar_spacing_minutes") or 1.0)))),
        "min_recovery_fraction": 0.35,
        "market_structure": structure,
        "current_price": round(price, 4),
    }
