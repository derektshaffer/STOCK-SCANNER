"""Momentum alert selection for the combined stock app.

Alerts are review prompts, not trade instructions. The app supports:
1) actionable alerts when a stock newly enters ANALYZE NOW, and
2) early pullback-watch alerts when a very high-quality setup is extended and
   waiting for a better entry.
"""

from __future__ import annotations


def actionable_alert_key(row):
    symbol = str((row or {}).get("symbol") or "").upper().strip()
    if not symbol:
        return None
    return symbol


def is_actionable_momentum_alert(row):
    row = row or {}
    grade = str(row.get("setup_grade") or "").upper().strip()
    action = str(row.get("scanner_action") or "").upper().strip()

    return bool(
        row.get("passed_base_filters")
        and row.get("alert_ready")
        and grade in {"A", "B"}
        and action == "ANALYZE NOW"
    )


def actionable_momentum_rows(payload):
    rows = []
    for row in (payload or {}).get("candidates") or []:
        if is_actionable_momentum_alert(row):
            rows.append(row)
    return rows


def newly_actionable(payload, previous_keys):
    previous = {str(x).upper().strip() for x in (previous_keys or []) if str(x).strip()}
    current_rows = actionable_momentum_rows(payload)
    current_keys = {
        actionable_alert_key(row)
        for row in current_rows
        if actionable_alert_key(row)
    }
    new_rows = [
        row for row in current_rows
        if actionable_alert_key(row) not in previous
    ]
    return new_rows, current_keys


PULLBACK_WATCH_MIN_SCORE = 90.0


def pullback_watch_alert_key(row):
    symbol = str((row or {}).get("symbol") or "").upper().strip()
    if not symbol:
        return None
    return symbol


def is_high_score_pullback_watch(row):
    row = row or {}
    grade = str(row.get("setup_grade") or "").upper().strip()
    action = str(row.get("scanner_action") or "").upper().strip()
    try:
        score = float(row.get("score"))
    except (TypeError, ValueError):
        return False

    # This is deliberately an early heads-up, not an entry alert. Only surface
    # rows that already passed the Scanner's base filters and live-data
    # integrity gate. DATA CHECK / stale / incomplete rows must never qualify.
    return bool(
        row.get("passed_base_filters")
        and row.get("action_data_integrity_ok") is True
        and grade in {"A", "B"}
        and action == "WAIT PULLBACK"
        and score >= PULLBACK_WATCH_MIN_SCORE
    )


def high_score_pullback_rows(payload):
    rows = []
    for row in (payload or {}).get("candidates") or []:
        if is_high_score_pullback_watch(row):
            rows.append(row)
    return rows


def newly_high_score_pullback(payload, previous_keys):
    previous = {
        str(x).upper().strip()
        for x in (previous_keys or [])
        if str(x).strip()
    }
    current_rows = high_score_pullback_rows(payload)
    current_keys = {
        pullback_watch_alert_key(row)
        for row in current_rows
        if pullback_watch_alert_key(row)
    }
    new_rows = [
        row for row in current_rows
        if pullback_watch_alert_key(row) not in previous
    ]
    return new_rows, current_keys


def pullback_watch_message(row):
    row = row or {}
    symbol = str(row.get("symbol") or "—").upper()
    grade = str(row.get("setup_grade") or "—").upper()
    fit = str(row.get("timeframe_best_fit") or "—")
    score = row.get("score")
    pace = (
        row.get("volume_pace_display")
        if row.get("volume_pace_display") is not None
        else row.get("volume_pace")
    )

    parts = [f"{symbol} high-score pullback watch", f"Grade {grade}"]
    if fit and fit != "—":
        parts.append(f"Best Fit {fit}")
    try:
        parts.append(f"Score {float(score):.0f}")
    except Exception:
        pass
    try:
        parts.append(f"Vol pace {float(pace):.1f}x")
    except Exception:
        pass
    parts.append("strong setup; wait for pullback confirmation")
    return " · ".join(parts)


def alert_message(row):
    row = row or {}
    symbol = str(row.get("symbol") or "—").upper()
    grade = str(row.get("setup_grade") or "—").upper()
    fit = str(row.get("timeframe_best_fit") or "—")
    score = row.get("score")
    pace = (
        row.get("volume_pace_display")
        if row.get("volume_pace_display") is not None
        else row.get("volume_pace")
    )

    parts = [f"{symbol} entered ANALYZE NOW", f"Grade {grade}"]
    if fit and fit != "—":
        parts.append(f"Best Fit {fit}")
    try:
        parts.append(f"Score {float(score):.0f}")
    except Exception:
        pass
    try:
        parts.append(f"Vol pace {float(pace):.1f}x")
    except Exception:
        pass
    return " · ".join(parts)
