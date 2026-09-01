from __future__ import annotations

from datetime import datetime, timedelta, timezone

import historical_hypothesis_challenge as hh


def _row(day, symbol, minute, *, path=True, endpoint=True, score=80, rank=5):
    dt = datetime(2026, 8, day, 14, 0, tzinfo=timezone.utc) + timedelta(
        minutes=minute
    )
    return {
        "scan_time_et": dt.isoformat(),
        "symbol": symbol,
        "rank": rank,
        "score": score,
        "opportunity_score": score,
        "passed_base_filters": rank <= 15,
        "failed_filters": ["distance_from_high"] if rank > 15 else [],
        "return_60m_pct": 4.0 if endpoint else 1.0,
        "opportunity_horizon_60m_complete": True,
        "opportunity_up_5_60m_before_stop": bool(path),
        "opportunity_up_10_60m_before_stop": False,
        "opportunity_mfe_60m_pct": 6.0 if path else 1.0,
        "opportunity_failure_stop_60m_hit": not bool(path),
        "regime_label": "RISK_ON" if day % 2 else "MIXED",
        "momentum_5m": 2.0 if path else -1.0,
        "momentum_15m": 3.0 if path else -1.5,
        "volume_pace": 3.0 if path else 1.0,
        "day_pct": 8.0 if path else 1.0,
        "above_vwap": bool(path),
    }


def test_same_symbol_horizon_dedup():
    rows = [
        _row(3, "AAA", 0),
        _row(3, "AAA", 30),
        _row(3, "AAA", 65),
        _row(3, "BBB", 10),
    ]
    kept = hh._independent_rows(rows)
    keys = [(row["symbol"], row["scan_time_et"]) for row in kept]
    assert len(keys) == 3, keys
    assert sum(symbol == "AAA" for symbol, _ in keys) == 2, keys


def test_path_target_can_pass_only_with_all_gates():
    rows = []
    symbols = [f"S{i:02d}" for i in range(20)]
    for day in range(3, 13):
        for i, symbol in enumerate(symbols):
            contradiction = i < 3
            rows.append(
                _row(
                    day,
                    symbol,
                    0,
                    path=(i % 2 == 0) or contradiction,
                    endpoint=not contradiction,
                    score=85 if i % 2 == 0 else 55,
                    rank=(i % 20) + 1,
                )
            )

    old = hh._walk_forward_model
    try:
        hh._walk_forward_model = lambda _rows, _label: {
            "status": "complete",
            "model_auc": 0.66,
            "model_brier": 0.18,
            "naive_brier": 0.24,
            "model_top_decile_lift_pp": 18.0,
            "stability": {
                "eligible_days": 6,
                "positive_lift_day_fraction": 0.67,
                "selected_distinct_symbols": 12,
                "selected_top_symbol_share_pct": 15.0,
                "regimes_represented": 2,
            },
        }
        result = hh._path_target_challenge(rows)
    finally:
        hh._walk_forward_model = old

    assert result["decision"] == "historically_supported_shadow_only", result
    assert result["gates"]["target_difference_real"] is True, result
    assert result["gates"]["model_skill"] is True, result
    assert result["gates"]["stability"] is True, result


def test_path_target_blocks_without_stability_coverage():
    rows = []
    for day in range(3, 13):
        for i in range(20):
            rows.append(
                _row(
                    day,
                    f"S{i:02d}",
                    0,
                    path=True if i < 10 else False,
                    endpoint=False if i < 3 else True,
                )
            )

    old = hh._walk_forward_model
    try:
        hh._walk_forward_model = lambda _rows, _label: {
            "status": "complete",
            "model_auc": 0.66,
            "model_brier": 0.18,
            "naive_brier": 0.24,
            "model_top_decile_lift_pp": 18.0,
            "stability": {
                "eligible_days": 6,
                "positive_lift_day_fraction": 0.67,
                "selected_distinct_symbols": 4,
                "selected_top_symbol_share_pct": 15.0,
                "regimes_represented": 2,
            },
        }
        result = hh._path_target_challenge(rows)
    finally:
        hh._walk_forward_model = old

    assert result["decision"] == "blocked_insufficient_stability_coverage", result
    assert result["gates"]["stability_coverage"] is False, result
    assert result["gates"]["regime_coverage"] is True, result


def test_path_target_blocks_without_regime_coverage():
    rows = []
    for day in range(3, 13):
        for i in range(20):
            rows.append(
                _row(
                    day,
                    f"S{i:02d}",
                    0,
                    path=True if i < 10 else False,
                    endpoint=False if i < 3 else True,
                )
            )

    old = hh._walk_forward_model
    try:
        hh._walk_forward_model = lambda _rows, _label: {
            "status": "complete",
            "model_auc": 0.66,
            "model_brier": 0.18,
            "naive_brier": 0.24,
            "model_top_decile_lift_pp": 18.0,
            "stability": {
                "eligible_days": 6,
                "positive_lift_day_fraction": 0.67,
                "selected_distinct_symbols": 12,
                "selected_top_symbol_share_pct": 15.0,
                "regimes_represented": 1,
            },
        }
        result = hh._path_target_challenge(rows)
    finally:
        hh._walk_forward_model = old

    assert result["decision"] == "blocked_insufficient_regime_coverage", result
    assert result["gates"]["stability_coverage"] is True, result
    assert result["gates"]["regime_coverage"] is False, result


def test_score_monotonicity_requires_out_of_sample_repeat():
    rows = []
    # Lower score bucket deliberately outperforms the higher bucket in both
    # discovery and confirmation periods.
    for day in range(3, 13):
        for i in range(40):
            rows.append(
                _row(
                    day,
                    f"L{day:02d}{i:02d}",
                    0,
                    path=(i < 32),
                    endpoint=True,
                    score=55,
                    rank=10,
                )
            )
            rows.append(
                _row(
                    day,
                    f"H{day:02d}{i:02d}",
                    0,
                    path=(i < 12),
                    endpoint=True,
                    score=65,
                    rank=10,
                )
            )
    result = hh._score_monotonicity_challenge(rows)
    assert result["decision"] == "historically_supported_shadow_only", result
    assert ["50-59", "60-69"] in result["repeated_inversions"], result


def test_session_hypothesis_blocks_without_extended_history():
    rows = [
        {**_row(3, f"S{i}", 0), "session_phase": "regular"}
        for i in range(120)
    ]
    result = hh._session_specific_challenge(rows)
    assert result["decision"] == "blocked_requires_extended_historical_replay", result


def test_empirical_hypothesis_uses_only_pre_evidence_replay():
    replay = {
        "observations": [
            _row(3, "AAA", 0),
            _row(9, "BBB", 0),
            _row(11, "CCC", 0),
        ],
        "replay": {"trading_days": 3},
    }
    audit = {
        "hypotheses": [
            {
                "id": "session_specific_calibration_candidate",
                "evidence_window": {
                    "start_date": "2026-08-10",
                    "end_date": "2026-08-12",
                    "historical_replay_used_to_generate": False,
                },
                "production_influence": False,
            }
        ]
    }
    report = hh.run_challenge(
        replay,
        audit,
        include_standing=False,
    )
    result = report["results"][0]
    independence = result["independence"]
    assert independence["challenge_rows_total_replay"] == 3, result
    assert independence["challenge_rows_before_evidence_window"] == 2, result
    assert independence["independent"] is True, result


def test_replay_generation_evidence_blocks_self_confirmation():
    replay = {
        "observations": [_row(3, "AAA", 0)],
        "replay": {"trading_days": 1},
    }
    audit = {
        "hypotheses": [
            {
                "id": "path_target_candidate",
                "evidence_window": {
                    "start_date": "2026-08-10",
                    "historical_replay_used_to_generate": True,
                },
                "production_influence": False,
            }
        ]
    }
    report = hh.run_challenge(
        replay,
        audit,
        include_standing=False,
    )
    result = report["results"][0]
    assert result["decision"] == "blocked_independence_violation", result


def test_standing_path_hypothesis_is_challenged():
    audit = {
        "source_findings": [
            {"id": "single_endpoint_primary_target", "status": "open"}
        ],
        "hypotheses": [],
    }
    hypotheses = hh._candidate_hypotheses(audit, include_standing=True)
    ids = {row["id"] for row in hypotheses}
    assert "path_target_candidate" in ids, hypotheses


def main():
    test_same_symbol_horizon_dedup()
    test_path_target_can_pass_only_with_all_gates()
    test_path_target_blocks_without_stability_coverage()
    test_path_target_blocks_without_regime_coverage()
    test_score_monotonicity_requires_out_of_sample_repeat()
    test_session_hypothesis_blocks_without_extended_history()
    test_empirical_hypothesis_uses_only_pre_evidence_replay()
    test_replay_generation_evidence_blocks_self_confirmation()
    test_standing_path_hypothesis_is_challenged()
    print("PHASE6_HISTORICAL_CHALLENGE_REGRESSIONS=passed")


if __name__ == "__main__":
    main()
