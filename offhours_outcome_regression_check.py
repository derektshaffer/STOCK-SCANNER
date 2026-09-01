"""Regression checks for off-hours multi-day outcome target parity."""

from offhours_outcome_tracker import _horizon_outcome, _needs_horizon, _summary_rows


def _bar(day, high, low, close):
    return {
        "t": f"{day}T20:00:00Z",
        "h": high,
        "l": low,
        "c": close,
    }


def main():
    target_first = [
        _bar("2026-09-01", 10.30, 9.90, 10.20),
        _bar("2026-09-02", 10.60, 10.00, 10.55),
        _bar("2026-09-03", 10.70, 10.20, 10.40),
        _bar("2026-09-04", 10.50, 10.10, 10.30),
        _bar("2026-09-08", 10.40, 10.00, 10.20),
    ]
    outcome = _horizon_outcome(10.0, target_first, 5)
    assert outcome["swing_target_before_stop_5d"] == 1, outcome
    assert outcome["swing_first_event_5d"] == "TARGET", outcome
    assert outcome["swing_first_hit_session"] == 2, outcome

    summary = _summary_rows([{"outcomes": {"5": outcome}}], 5)
    assert summary["swing_target_resolved"] == 1, summary
    assert summary["swing_target_before_stop_rate_pct"] == 100.0, summary
    assert summary["swing_ambiguous_same_day"] == 0, summary

    ambiguous = [
        _bar("2026-09-01", 10.60, 9.50, 10.10),
        _bar("2026-09-02", 10.20, 9.90, 10.00),
        _bar("2026-09-03", 10.10, 9.80, 9.95),
        _bar("2026-09-04", 10.00, 9.75, 9.90),
        _bar("2026-09-08", 10.05, 9.80, 9.95),
    ]
    ambiguous_outcome = _horizon_outcome(10.0, ambiguous, 5)
    assert ambiguous_outcome["swing_target_before_stop_5d"] is None, ambiguous_outcome
    assert ambiguous_outcome["swing_ambiguous_same_day_5d"] is True, ambiguous_outcome

    ambiguous_summary = _summary_rows(
        [{"outcomes": {"5": ambiguous_outcome}}],
        5,
    )
    assert ambiguous_summary["swing_target_resolved"] == 0, ambiguous_summary
    assert ambiguous_summary["swing_target_before_stop_rate_pct"] is None, ambiguous_summary
    assert ambiguous_summary["swing_ambiguous_same_day"] == 1, ambiguous_summary

    old_v1 = {"outcomes": {"5": {"return_pct": 3.0}}}
    assert _needs_horizon(old_v1, 5) is True
    assert _needs_horizon({"outcomes": {"1": {"return_pct": 1.0}}}, 1) is False

    print("OFFHOURS_SWING_TARGET_PARITY=passed")


if __name__ == "__main__":
    main()
