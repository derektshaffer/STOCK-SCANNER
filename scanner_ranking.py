"""Execution quality and eligibility-aware final Scanner opportunity ranking.

The legacy tradeability_score field remains the raw execution signal for
research/recorder compatibility. Never overwrite it with the gated rating.
"""
import math

REVIEW_ACTIONS = frozenset({
    "ANALYZE NOW", "BREAKOUT WATCH", "BOUNCE WATCH", "WATCH", "CAUTION",
    "WAIT", "WAIT PULLBACK", "HIGH-RISK REVIEW", "SUB-$1 EXPLOSIVE WATCH",
    "EXPLOSIVE WATCH", "EXTENDED WATCH",
})


def execution_quality_value(row):
    """Read legacy snapshots too; malformed/out-of-range scores are unknown."""
    value = row.get("execution_quality_score", row.get("tradeability_score"))
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 100 else None


# Compatibility for consumers of the original raw execution metric.
tradeability_value = execution_quality_value


def execution_quality_rank_key(row):
    score = execution_quality_value(row)
    return score if score is not None else float("-inf")


def tradability_status(row, *, live=False):
    """Final review status; this never changes grade, ACTION or integrity gates.

    live=True reuses the existing price-display freshness policy for UI views.
    Publications use their recorded integrity decision, independent of read time.
    """
    grade = str(row.get("setup_grade") or row.get("grade") or "").strip().upper()
    action = str(row.get("scanner_action") or "").strip().upper()
    tier = str(row.get("scanner_action_tier") or "").strip().upper()
    if grade == "REJECT":
        return "REJECT"
    if "NO TRADE" in action or action == "REJECT" or tier == "AVOID":
        return "NO TRADE"
    if action == "DATA CHECK" or tier == "BLOCKED" or row.get("action_data_integrity_ok") is False or row.get("radar_only"):
        return "DATA CHECK"
    # Grade C / CAUTION can intentionally mean one noncritical base-filter
    # near miss. Respect the existing grade/action decision rather than turning
    # every passed_base_filters=False row into a new hard rejection.
    if grade not in {"A", "B", "C"} or action not in REVIEW_ACTIONS or row.get("action_data_integrity_ok") is not True:
        return "UNKNOWN"
    if live:
        from live_price_quality import price_view
        if not price_view(row)["current"]:
            return "DATA CHECK"
    return action


def tradability_value(row, *, live=False):
    status = tradability_status(row, live=live)
    if status == "UNKNOWN":
        return None
    return execution_quality_value(row) if status in REVIEW_ACTIONS else 0.0


def tradability_text(row, *, live=False):
    status = tradability_status(row, live=live)
    score = tradability_value(row, live=live)
    return f"{status} / {score:.0f}" if score is not None else f"{status} / —"


def tradeability_rank_key(row, *, live=False):
    """Eligible reviews first, then gated score; stable ties preserve order.

    Raw execution orders only the ineligible tail, never ahead of eligible rows.
    Even an eligible zero or unknown execution score beats a rejected 100.
    """
    eligible = tradability_status(row, live=live) in REVIEW_ACTIONS
    return (eligible, execution_quality_rank_key(row))


def rank_candidates(records, *, live=False):
    """Sorted view only: preserve source rows, scores and publication identity."""
    return sorted(records, key=lambda row: tradeability_rank_key(row, live=live), reverse=True)
