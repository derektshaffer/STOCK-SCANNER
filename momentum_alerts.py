"""Actionable-momentum alert selection for the combined stock app.

Alerts are review prompts, not trade instructions. They only surface stocks
that newly enter the strongest existing Scanner state.
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
