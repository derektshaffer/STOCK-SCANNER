"""Tracking-only live flags for historically robust Swing research patterns.

These flags are intentionally separate from production scoring. They identify
live setups that matched out-of-sample historical patterns so we can collect
forward outcomes before deciding whether any pattern deserves score weight.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

FLAG_VERSION = "swing-research-flags-v2-context-parity"
ET = ZoneInfo("America/New_York")

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



def _signal_context(metrics):
    raw = (metrics or {}).get("as_of")
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et = dt.astimezone(ET)
    except Exception:
        et = datetime.now(ET)

    if et.weekday() >= 5:
        phase = "closed"
    else:
        minute = et.hour * 60 + et.minute
        if 4 * 60 <= minute < 9 * 60 + 30:
            phase = "premarket"
        elif 9 * 60 + 30 <= minute < 16 * 60:
            phase = "regular_intraday"
        elif 16 * 60 <= minute < 20 * 60:
            phase = "afterhours"
        else:
            phase = "closed"

    price = _num((metrics or {}).get("price"))
    day_pct = _num((metrics or {}).get("day_pct"))
    volume = _num((metrics or {}).get("session_volume"))
    if volume is None:
        volume = _num((metrics or {}).get("volume"))
    dollar_volume = (
        price * volume
        if price is not None and volume is not None
        else None
    )

    # The historical research universe was restricted to $0.50-$60 stocks,
    # >=2% day movers and >=$500k current-day dollar volume. The historical
    # replay also selected only its top-ranked candidates, which cannot be
    # reproduced by a manual single-stock Analyzer call. This proxy therefore
    # improves comparability without pretending it is exact parity.
    universe_proxy_pass = bool(
        price is not None
        and 0.50 <= price <= 60.0
        and day_pct is not None
        and day_pct >= 2.0
        and dollar_volume is not None
        and dollar_volume >= 500_000
    )

    return {
        "phase": phase,
        "signal_time_et": et.isoformat(),
        "universe_proxy_pass": universe_proxy_pass,
        "dollar_volume": (
            round(dollar_volume, 2)
            if dollar_volume is not None
            else None
        ),
        "direct_historical_parity": False,
        "historical_reference": "end_of_day_daily_replay",
    }


def evaluate_swing_research_flags(metrics, timeframe):
    """Return live research matches without changing any production score."""
    metrics = metrics or {}
    timeframe = timeframe or {}
    daily = timeframe.get("daily_trend") or {}
    stair = metrics.get("stair_step") or {}

    context = _signal_context(metrics)
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
        "live_sampling_context": context["phase"],
        "historical_universe_proxy_pass": context["universe_proxy_pass"],
        "historical_universe_proxy_dollar_volume": context["dollar_volume"],
        "direct_historical_parity": False,
        "historical_reference_context": context["historical_reference"],
        "signal_time_et": context["signal_time_et"],
        "note": (
            "These live matches apply patterns discovered from end-of-day historical "
            "replay to an intraday Analyzer snapshot. They are useful exploratory "
            "forward samples, but the historical success rates are reference data, "
            "not live probabilities and not direct apples-to-apples validation. "
            "Only regular-session samples that also pass the historical-universe "
            "proxy are included in the live research calibration. The flags never "
            "change Swing fit, trade plans, or live rankings."
        ),
    }


def compact_flag_ids(payload):
    payload = payload or {}
    return [
        str(item.get("id"))
        for item in (payload.get("matches") or [])
        if item.get("id")
    ]
