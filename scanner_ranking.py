"""Shared default ordering for analyzed momentum candidates and their UI views."""

import math


def tradeability_value(row):
    """Return a finite score, or None when no usable score was published."""
    value = row.get("tradeability_score")
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) else None


def tradeability_rank_key(row):
    """Highest Tradeability first with reverse=True; unavailable scores last.

    Python's stable sort preserves equal-score order. Explosion, ML and setup
    scores do not break ties or override this display/publication ranking.
    This key never changes a candidate's scores, ACTION or integrity flags.
    """
    score = tradeability_value(row)
    return score if score is not None else float("-inf")


def rank_candidates(records):
    """Return a sorted view without mutating source rows or publication order."""
    return sorted(records, key=tradeability_rank_key, reverse=True)
