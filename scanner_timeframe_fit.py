"""Explainable scanner-level timeframe fit.

This module does not rank stocks and does not change Scanner ACTION or ML.
It classifies an already-discovered momentum candidate by the horizon its
current evidence appears to fit best:

- INTRADAY: minutes to same day
- SWING: roughly 2-10 trading days
- LONGER-TERM: roughly 2-8 weeks
- MIXED: two horizons are close enough that neither clearly dominates

The Analyzer remains the deeper confirmation layer, especially for
LONGER-TERM where fundamentals, dilution, filings, catalysts, and broader
context matter more than a lightweight scanner can safely judge.
"""

from __future__ import annotations

import math

TIMEFRAME_FIT_VERSION = "scanner-timeframe-fit-v1"
MIXED_MARGIN_POINTS = 5.0


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def _add(reasons, score, points, text):
    score += points
    if text:
        reasons.append(text)
    return score


def _intraday_score(c):
    score = 50.0
    reasons = []
    coverage = 0

    m5 = _num(c.get("momentum_5m"))
    if m5 is not None:
        coverage += 1
        if m5 >= 1.0:
            score = _add(reasons, score, 12, "strong 5-minute momentum")
        elif m5 >= 0.3:
            score = _add(reasons, score, 6, "positive 5-minute momentum")
        elif m5 < 0:
            score = _add(reasons, score, -8, "5-minute momentum is weakening")

    m15 = _num(c.get("momentum_15m"))
    if m15 is not None:
        coverage += 1
        if m15 >= 2.0:
            score = _add(reasons, score, 12, "strong 15-minute continuation")
        elif m15 >= 0.5:
            score = _add(reasons, score, 6, "positive 15-minute continuation")
        elif m15 < 0:
            score = _add(reasons, score, -8, "15-minute momentum is weakening")

    pace = _num(c.get("volume_pace_display"))
    if pace is None:
        pace = _num(c.get("volume_pace"))
    if pace is not None:
        coverage += 1
        if pace >= 2.0:
            score = _add(reasons, score, 11, "volume is running well above normal")
        elif pace >= 1.3:
            score = _add(reasons, score, 6, "volume participation is above normal")
        elif pace < 0.75:
            score = _add(reasons, score, -5, "volume participation is light")

    if c.get("vwap") is not None:
        coverage += 1
        if bool(c.get("above_vwap")):
            score = _add(reasons, score, 8, "price is holding above VWAP")
        else:
            score = _add(reasons, score, -8, "price is below VWAP")

    from_high = _num(c.get("distance_from_high_pct"))
    if from_high is not None:
        coverage += 1
        if from_high <= 3.0:
            score = _add(reasons, score, 6, "price remains close to the session high")
        elif from_high >= 10.0:
            score = _add(reasons, score, -7, "price has faded well off the session high")

    if bool(c.get("breakout_holding")):
        score = _add(reasons, score, 5, "recent breakout is holding")
    if bool(c.get("vwap_reclaim")):
        score = _add(reasons, score, 4, "VWAP reclaim is holding")
    if bool(c.get("failed_breakout")):
        score = _add(reasons, score, -10, "recent breakout failed")

    spread = _num(c.get("spread_pct"))
    if spread is not None:
        coverage += 1
        if spread <= 1.5:
            score = _add(reasons, score, 3, "spread is relatively clean")
        elif spread >= 5.0:
            score = _add(reasons, score, -7, "wide spread hurts intraday execution")

    return round(_clamp(score), 1), reasons, coverage


def _swing_score(c):
    score = 50.0
    reasons = []
    coverage = 0

    day_pct = _num(c.get("day_pct"))
    if day_pct is not None:
        coverage += 1
        if 4.0 <= day_pct <= 20.0:
            score = _add(reasons, score, 7, "current move is strong without being extreme")
        elif 20.0 < day_pct <= 40.0:
            score = _add(reasons, score, 3, "strong current move can support continuation")
        elif day_pct >= 60.0:
            score = _add(reasons, score, -8, "current move is very extended for a multi-day hold")

    r5 = _num(c.get("daily_return_5d_pct"))
    if r5 is not None:
        coverage += 1
        if r5 >= 8.0:
            score = _add(reasons, score, 10, "strong five-session trend")
        elif r5 > 0:
            score = _add(reasons, score, 5, "five-session trend is positive")
        elif r5 <= -10.0:
            score = _add(reasons, score, -7, "five-session trend is weak")

    r20 = _num(c.get("daily_return_20d_pct"))
    if r20 is not None:
        coverage += 1
        if r20 >= 12.0:
            score = _add(reasons, score, 11, "20-session trend is strong")
        elif r20 > 0:
            score = _add(reasons, score, 5, "20-session trend is positive")
        elif r20 <= -15.0:
            score = _add(reasons, score, -9, "20-session trend is damaged")

    stair = _num(c.get("stair_structure_score"))
    if stair is not None:
        coverage += 1
        if stair >= 68:
            score = _add(reasons, score, 10, "strong multi-session stair-step structure")
        elif stair >= 58:
            score = _add(reasons, score, 5, "constructive multi-session structure")
        elif stair < 40:
            score = _add(reasons, score, -7, "multi-session structure is weak")

    steps = _num(c.get("stair_step_count"))
    if steps is not None:
        coverage += 1
        if steps >= 2:
            score = _add(reasons, score, 5, "multiple higher momentum steps are visible")

    if bool(c.get("stair_reaccelerating")):
        score = _add(reasons, score, 5, "multi-session trend is reaccelerating")
    if bool(c.get("stair_breakdown")):
        score = _add(reasons, score, -12, "latest multi-session plateau has broken down")

    catalyst = _num(c.get("news_bonus"))
    if catalyst is not None and catalyst != 0:
        coverage += 1
        if catalyst >= 4:
            score = _add(reasons, score, 5, "fresh positive catalyst can support a multi-day move")
        elif catalyst <= -4:
            score = _add(reasons, score, -8, "negative catalyst raises multi-day risk")

    return round(_clamp(score), 1), reasons, coverage


def _longer_term_score(c):
    score = 50.0
    reasons = []
    coverage = 0

    r20 = _num(c.get("daily_return_20d_pct"))
    if r20 is not None:
        coverage += 1
        if r20 >= 15.0:
            score = _add(reasons, score, 10, "20-session trend is firmly positive")
        elif r20 >= 3.0:
            score = _add(reasons, score, 5, "20-session trend is constructive")
        elif r20 <= -15.0:
            score = _add(reasons, score, -9, "20-session trend is damaged")

    r40 = _num(c.get("daily_return_40d_pct"))
    if r40 is not None:
        coverage += 1
        if r40 >= 25.0:
            score = _add(reasons, score, 14, "roughly two-month trend is strong")
        elif r40 >= 7.0:
            score = _add(reasons, score, 8, "roughly two-month trend is positive")
        elif r40 <= -18.0:
            score = _add(reasons, score, -12, "roughly two-month trend is weak")

    above20 = c.get("daily_above_ma20")
    if above20 is not None:
        coverage += 1
        score = _add(
            reasons,
            score,
            7 if above20 else -7,
            "price is above its 20-session average"
            if above20
            else "price is below its 20-session average",
        )

    above40 = c.get("daily_above_ma40")
    if above40 is not None:
        coverage += 1
        score = _add(
            reasons,
            score,
            9 if above40 else -9,
            "price is above its 40-session average"
            if above40
            else "price is below its 40-session average",
        )

    alignment = str(c.get("daily_ma_alignment") or "")
    if alignment:
        coverage += 1
        if alignment == "BULLISH":
            score = _add(reasons, score, 9, "10/20/40-session averages are bullishly aligned")
        elif alignment == "BEARISH":
            score = _add(reasons, score, -9, "10/20/40-session averages are bearishly aligned")

    from_high = _num(c.get("daily_from_recent_high_pct"))
    if from_high is not None:
        coverage += 1
        if from_high >= -12.0:
            score = _add(reasons, score, 6, "price is still near its recent multi-week high")
        elif from_high <= -40.0:
            score = _add(reasons, score, -8, "price remains far below its recent multi-week high")

    stair = _num(c.get("stair_structure_score"))
    if stair is not None:
        coverage += 1
        if stair >= 65:
            score = _add(reasons, score, 6, "multi-session structure supports trend persistence")
        elif stair < 40:
            score = _add(reasons, score, -5, "multi-session structure is not yet durable")

    day_pct = _num(c.get("day_pct"))
    if day_pct is not None:
        coverage += 1
        if 3.0 <= day_pct <= 18.0:
            score = _add(reasons, score, 3, "current momentum is supportive without extreme extension")
        elif day_pct >= 45.0:
            score = _add(reasons, score, -7, "current move is stretched for a multi-week entry")

    catalyst = _num(c.get("news_bonus"))
    if catalyst is not None and catalyst != 0:
        coverage += 1
        if catalyst >= 4:
            score = _add(reasons, score, 4, "positive catalyst may have multi-week durability")
        elif catalyst <= -4:
            score = _add(reasons, score, -6, "negative catalyst raises multi-week risk")

    # Multi-week technical classification should not look authoritative when
    # the daily history is thin. The Analyzer has deeper fundamental/filing
    # coverage and remains the required confirmation layer.
    if coverage < 4:
        score = min(score, 57.0)
        reasons.append("multi-week history coverage is limited")

    return round(_clamp(score), 1), reasons, coverage


def classify_timeframe_fit(candidate):
    c = candidate or {}
    intraday, intraday_reasons, intraday_coverage = _intraday_score(c)
    swing, swing_reasons, swing_coverage = _swing_score(c)
    longer, longer_reasons, longer_coverage = _longer_term_score(c)

    scores = {
        "INTRADAY": intraday,
        "SWING": swing,
        "LONGER-TERM": longer,
    }
    reasons = {
        "INTRADAY": intraday_reasons,
        "SWING": swing_reasons,
        "LONGER-TERM": longer_reasons,
    }
    coverage = {
        "INTRADAY": intraday_coverage,
        "SWING": swing_coverage,
        "LONGER-TERM": longer_coverage,
    }

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    primary, primary_score = ranked[0]
    secondary, secondary_score = ranked[1]
    margin = round(primary_score - secondary_score, 1)

    if (
        primary_score >= 55.0
        and secondary_score >= 55.0
        and margin <= MIXED_MARGIN_POINTS
    ):
        best_fit = "MIXED"
        fit_horizons = [primary, secondary]
    else:
        best_fit = primary
        fit_horizons = [primary]

    if primary_score >= 72 and margin >= 8 and coverage.get(primary, 0) >= 4:
        confidence = "HIGH"
    elif primary_score >= 62 and coverage.get(primary, 0) >= 3:
        confidence = "MODERATE"
    else:
        confidence = "LOW"

    primary_reasons = reasons.get(primary) or []
    secondary_reasons = reasons.get(secondary) or []
    explanation = primary_reasons[:3]
    if best_fit == "MIXED" and secondary_reasons:
        explanation.append(f"{secondary.lower()} evidence is also close")

    return {
        "version": TIMEFRAME_FIT_VERSION,
        "best_fit": best_fit,
        "primary_fit": primary,
        "secondary_fit": secondary,
        "fit_horizons": fit_horizons,
        "scores": scores,
        "coverage": coverage,
        "confidence": confidence,
        "margin_points": margin,
        "reasons": reasons,
        "explanation": explanation,
        "horizon_guide": {
            "INTRADAY": "minutes to same day",
            "SWING": "roughly 2-10 trading days",
            "LONGER-TERM": "roughly 2-8 weeks",
        },
        "production_rank_impact": False,
        "note": (
            "Timeframe Fit is an explainable scanner classification only. It does "
            "not change momentum ranking, Scanner ACTION, or validated intraday ML. "
            "LONGER-TERM is a technical screen and should be confirmed in Analyzer "
            "with fundamentals, dilution/filings, catalyst durability, and risk."
        ),
    }


def attach_timeframe_fit(candidate):
    result = classify_timeframe_fit(candidate)
    candidate["timeframe_fit"] = result
    candidate["timeframe_fit_version"] = result["version"]
    candidate["timeframe_best_fit"] = result["best_fit"]
    candidate["timeframe_primary_fit"] = result["primary_fit"]
    candidate["timeframe_secondary_fit"] = result["secondary_fit"]
    candidate["timeframe_fit_horizons"] = result["fit_horizons"]
    candidate["timeframe_intraday_score"] = result["scores"]["INTRADAY"]
    candidate["timeframe_swing_score"] = result["scores"]["SWING"]
    candidate["timeframe_longer_term_score"] = result["scores"]["LONGER-TERM"]
    candidate["timeframe_fit_confidence"] = result["confidence"]
    candidate["timeframe_fit_reason"] = " · ".join(result["explanation"])
    return candidate
