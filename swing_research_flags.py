"""Tracking-only live flags for historically robust Swing research patterns.

These flags are intentionally separate from production scoring. They identify
live setups that matched out-of-sample historical patterns so we can collect
forward outcomes before deciding whether any pattern deserves score weight.
"""

from __future__ import annotations

import math

FLAG_VERSION = "swing-research-flags-v1"

# Frozen from the 2021-2026 discovery/confirmation study.
REVERSAL_20D_MAX_PCT = -1.77
DEEP_REVERSAL_20D_MAX_PCT = -10.70
STRONG_DAY_MIN_PCT = 6.7967
EXTREME_DAY_MIN_PCT = 9.2328
STRONG_STAIR_LAST_STEP_MIN_PCT = 5.92

HISTORICAL_CONFIRMATION = {
    "reversal_ignition": {
        "confirmation_n": 296,
        "confirmation_success_pct": 52.0,
        "comparison_success_pct": 38.9,
        "confirmation_lift_pp": 13.1,
        "positive_years": 5,
        "eligible_years": 5,
    },
    "strong_stair_step": {
        "confirmation_n": 1340,
        "confirmation_success_pct": 44.7,
        "comparison_success_pct": 33.7,
        "confirmation_lift_pp": 11.0,
        "positive_years": 6,
        "eligible_years": 6,
    },
    "strong_momentum_day": {
        "confirmation_n": 940,
        "confirmation_success_pct": 47.2,
        "comparison_success_pct": 35.2,
        "confirmation_lift_pp": 12.1,
        "positive_years": 6,
        "eligible_years": 6,
    },
}

FLAG_LABELS = {
    "reversal_ignition": "Reversal Ignition",
    "strong_stair_step": "Strong Stair-Step",
    "strong_momentum_day": "Strong Momentum Day",
}


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def evaluate_swing_research_flags(metrics, timeframe):
    """Return live research matches without changing any production score."""
    metrics = metrics or {}
    timeframe = timeframe or {}
    daily = timeframe.get("daily_trend") or {}
    stair = metrics.get("stair_step") or {}

    day_pct = _num(metrics.get("day_pct"))
    return_20d = _num(daily.get("return_20d_pct"))
    stair_last_step = _num(stair.get("last_step_pct"))
    stair_step_count = int(stair.get("step_count") or 0)

    matches = []

    if (
        day_pct is not None
        and return_20d is not None
        and day_pct >= STRONG_DAY_MIN_PCT
        and return_20d <= REVERSAL_20D_MAX_PCT
    ):
        variant = "STANDARD"
        research = dict(HISTORICAL_CONFIRMATION["reversal_ignition"])
        if day_pct >= EXTREME_DAY_MIN_PCT:
            variant = "EXTREME IGNITION"
            research.update(
                {
                    "confirmation_n": 172,
                    "confirmation_success_pct": 54.7,
                    "comparison_success_pct": 39.6,
                    "confirmation_lift_pp": 15.1,
                    "positive_years": 4,
                    "eligible_years": 4,
                }
            )
        elif return_20d <= DEEP_REVERSAL_20D_MAX_PCT:
            variant = "DEEP REVERSAL"
            research.update(
                {
                    "confirmation_n": 199,
                    "confirmation_success_pct": 52.3,
                    "comparison_success_pct": 39.6,
                    "confirmation_lift_pp": 12.7,
                    "positive_years": 5,
                    "eligible_years": 5,
                }
            )
        matches.append(
            {
                "id": "reversal_ignition",
                "label": FLAG_LABELS["reversal_ignition"],
                "variant": variant,
                "tracking_only": True,
                "signal_values": {
                    "day_pct": day_pct,
                    "return_20d_pct": return_20d,
                },
                "rule": (
                    f"20d return <= {REVERSAL_20D_MAX_PCT:.2f}% and "
                    f"current day >= +{STRONG_DAY_MIN_PCT:.2f}%"
                ),
                "historical_confirmation": research,
            }
        )

    if (
        stair_last_step is not None
        and stair_last_step >= STRONG_STAIR_LAST_STEP_MIN_PCT
    ):
        matches.append(
            {
                "id": "strong_stair_step",
                "label": FLAG_LABELS["strong_stair_step"],
                "variant": "PRIMARY",
                "tracking_only": True,
                "signal_values": {
                    "stair_last_step_pct": stair_last_step,
                    "stair_step_count": stair_step_count,
                },
                "rule": (
                    f"latest multi-session stair step >= "
                    f"{STRONG_STAIR_LAST_STEP_MIN_PCT:.2f}%"
                ),
                "historical_confirmation": dict(
                    HISTORICAL_CONFIRMATION["strong_stair_step"]
                ),
            }
        )

    if day_pct is not None and day_pct >= STRONG_DAY_MIN_PCT:
        matches.append(
            {
                "id": "strong_momentum_day",
                "label": FLAG_LABELS["strong_momentum_day"],
                "variant": "PRIMARY",
                "tracking_only": True,
                "signal_values": {"day_pct": day_pct},
                "rule": f"current day >= +{STRONG_DAY_MIN_PCT:.2f}%",
                "historical_confirmation": dict(
                    HISTORICAL_CONFIRMATION["strong_momentum_day"]
                ),
            }
        )

    return {
        "version": FLAG_VERSION,
        "tracking_only": True,
        "matched": bool(matches),
        "match_count": len(matches),
        "matches": matches,
        "outcome_target": "+5% before -4% within 5 trading sessions",
        "note": (
            "Historical research matches are forward-tracking flags only. "
            "They do not change Swing fit, trade plans, or live rankings."
        ),
    }


def compact_flag_ids(payload):
    payload = payload or {}
    return [
        str(item.get("id"))
        for item in (payload.get("matches") or [])
        if item.get("id")
    ]
