from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

# score_outcomes validates configuration at import time. Dummy values are
# sufficient for these synthetic, network-free regression tests.
os.environ.setdefault("GITHUB_REPOSITORY", "example/repo")
os.environ.setdefault("GH_TOKEN", "synthetic-token")
os.environ.setdefault("TRADIER_TOKEN", "synthetic-token")

from learning_system_audit import (
    audit_observations,
    audit_source_contracts,
    build_hypotheses,
    load_outcome_observations,
)
from scanner_ml_ranker import (
    _extract_observations,
    _extract_path_research_observations,
    _production_regular_session,
)
import score_outcomes as so
import score_opportunity_outcomes as soo
import scanner_live_journal as slj


def test_usde_like_endpoint_contradiction():
    rows = [
        {
            "symbol": "SYNTH",
            "scan_time_et": "2026-08-31T15:20:00-04:00",
            "session_phase": "regular",
            "score": 88,
            "return_60m_pct": 2.0,
            "mfe_60m_pct": 18.5,
            "mae_60m_pct": -1.4,
        },
        {
            "symbol": "CONTROL",
            "scan_time_et": "2026-08-31T14:00:00-04:00",
            "session_phase": "regular",
            "score": 60,
            "return_60m_pct": 4.0,
            "mfe_60m_pct": 5.0,
            "mae_60m_pct": -0.7,
        },
    ]
    result = audit_observations(rows)
    assert result["endpoint_label_contradictions_n"] == 1, result
    assert result["explosive_mfe_endpoint_misses_n"] == 1, result
    assert result["high_score_endpoint_misses_n"] == 1, result
    assert result["examples"][0]["symbol"] == "SYNTH", result


def test_shadow_research_fields_are_audited_too():
    rows = [
        {
            "symbol": "AFTER",
            "scan_time_et": "2026-08-31T18:10:00-04:00",
            "session_phase": "afterhours",
            "opportunity_score": 82,
            "research_return_60m_pct": 1.5,
            "research_mfe_60m_pct": 12.0,
            "research_mae_60m_pct": -2.0,
        }
    ]
    result = audit_observations(rows)
    assert result["endpoint_label_contradictions_n"] == 1, result
    assert result["contradictions_by_session_phase"]["afterhours"] == 1, result
    assert result["examples"][0]["source"] == "shadow_opportunity", result


def test_source_contract_audit_finds_specification_gaps():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".github/workflows").mkdir(parents=True)
        (root / "scanner_ml_ranker.py").write_text(
            'TARGET_DESCRIPTION = ">= +3% at 60 minutes"\n'
            'label = int(return_60 >= 3.0)\n',
            encoding="utf-8",
        )
        (root / "score_outcomes.py").write_text(
            'def load_regular_session_scans(): pass\n'
            'if payload.get("mode") != "regular_market_session": pass\n'
            'row["mfe_60m_pct"] = 12.0\n',
            encoding="utf-8",
        )
        (root / "stock_scanner.py").write_text(
            "SCAN_LOG_TOP = 30\n",
            encoding="utf-8",
        )
        (root / "app.py").write_text(
            "# 2-minute scanner ON\n",
            encoding="utf-8",
        )
        (root / ".github/workflows/stock-scanner.yml").write_text(
            "on:\n  schedule:\n    - cron: '7,37 8-23 * * 1-5'\n",
            encoding="utf-8",
        )

        findings = audit_source_contracts(root)
        ids = {row["id"] for row in findings}
        assert "single_endpoint_primary_target" in ids, findings
        assert "extended_session_outcomes_excluded" in ids, findings
        assert "path_information_not_primary_target" in ids, findings
        assert "top_n_observation_censoring" in ids, findings
        assert "live_vs_durable_cadence_gap" in ids, findings

        (root / "score_opportunity_outcomes.py").write_text(
            'MODE = "extended_market_session"\n'
            'FILTER = \'tradier_session_filter="all"\'\n'
            'FIELD = "research_mfe_60m_pct"\n',
            encoding="utf-8",
        )
        findings = audit_source_contracts(root)
        ids = {row["id"] for row in findings}
        assert "extended_session_outcomes_excluded" not in ids, findings
        assert "extended_session_shadow_capture" in ids, findings

        # Once the live journal is wired through scanner_runtime + stock_scanner,
        # the audit must stop claiming the 2-minute states are completely lost.
        (root / "scanner_live_journal.py").write_text(
            'BUCKET_MINUTES = 15\n'
            'BRANCH = "learning-journal"\n'
            'def capture_live_scan(rows, now_et): pass\n',
            encoding="utf-8",
        )
        (root / "scanner_runtime.py").write_text(
            'FLAG = "SCANNER_LIVE_JOURNAL_ENABLED"\n',
            encoding="utf-8",
        )
        with (root / "stock_scanner.py").open("a", encoding="utf-8") as handle:
            handle.write("\ncapture_live_scan(rows, now_et)\n")
        findings = audit_source_contracts(root)
        ids = {row["id"] for row in findings}
        assert "live_vs_durable_cadence_gap" not in ids, findings
        assert "high_frequency_live_journal" in ids, findings


def test_hypothesis_generation_excludes_historical_replay():
    import json

    with tempfile.TemporaryDirectory() as tmp:
        report_dir = Path(tmp)
        (report_dir / "outcomes_historical_replay.json").write_text(
            json.dumps(
                {
                    "source": "historical_scanner_replay",
                    "observations": [
                        {
                            "observation_source": "historical_replay",
                            "scan_time_et": "2026-08-01T10:00:00-04:00",
                            "symbol": "REPLAY",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (report_dir / "outcomes_2026-08-31.json").write_text(
            json.dumps(
                {
                    "source": "live_scanner",
                    "observations": [
                        {
                            "observation_source": "live_scan",
                            "scan_time_et": "2026-08-31T10:00:00-04:00",
                            "symbol": "LIVE",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        rows, reports = load_outcome_observations(report_dir)
        assert reports == 1, (reports, rows)
        assert [row["symbol"] for row in rows] == ["LIVE"], rows

        empirical = audit_observations(rows)
        assert empirical["evidence_start_date"] == "2026-08-31", empirical
        assert empirical["evidence_end_date"] == "2026-08-31", empirical

        hypotheses = build_hypotheses([], empirical)
        for hypothesis in hypotheses:
            window = hypothesis.get("evidence_window") or {}
            assert window.get("historical_replay_used_to_generate") is False


def test_production_scanner_ml_is_regular_session_gated():
    assert _production_regular_session(
        {"observation_source": "live_scan", "session_phase": "regular"},
        {},
    )
    assert not _production_regular_session(
        {"observation_source": "live_scan", "session_phase": "premarket"},
        {},
    )
    assert not _production_regular_session(
        {"observation_source": "live_scan", "session_phase": "afterhours"},
        {},
    )
    assert _production_regular_session(
        {
            "observation_source": "historical_replay",
            "session_phase": "afterhours",
        },
        {},
    )
    assert _production_regular_session(
        {
            "observation_source": "live_scan",
            "scan_time_et": "2026-08-31T10:00:00-04:00",
        },
        {},
    )
    assert not _production_regular_session(
        {
            "observation_source": "live_scan",
            "scan_time_et": "2026-08-31T18:00:00-04:00",
        },
        {},
    )


def test_opportunity_path_keeps_full_mfe_and_order():
    et = ZoneInfo("America/New_York")
    scan_time = datetime(2026, 8, 31, 15, 0, tzinfo=et)
    bars = [
        {"time": datetime(2026, 8, 31, 15, 1, tzinfo=et), "high": 101.0, "low": 99.0, "close": 100.5},
        {"time": datetime(2026, 8, 31, 15, 10, tzinfo=et), "high": 105.0, "low": 100.0, "close": 104.0},
        {"time": datetime(2026, 8, 31, 15, 20, tzinfo=et), "high": 111.0, "low": 103.0, "close": 109.0},
        {"time": datetime(2026, 8, 31, 15, 30, tzinfo=et), "high": 108.0, "low": 96.0, "close": 98.0},
    ]
    indexed = {
        "bars": bars,
        "times": [row["time"] for row in bars],
        "prices": [row["close"] for row in bars],
    }
    result = soo.opportunity_path_metrics(
        indexed,
        scan_time,
        datetime(2026, 8, 31, 20, 0, tzinfo=et),
        100.0,
        horizon_minutes=60,
    )
    assert result["horizon_complete"] is True, result
    assert result["mfe_pct"] == 11.0, result
    assert result["mae_pct"] == -4.0, result
    assert result["time_to_peak_minutes"] == 20.0, result
    assert result["failure_stop_hit"] is True, result
    assert result["failure_stop_time_minutes"] == 30.0, result
    assert result["up_5_hit"] is True, result
    assert result["up_10_hit"] is True, result
    assert result["up_10_before_stop"] is True, result
    assert result["up_20_hit"] is False, result


def test_path_target_captures_interim_winner_without_changing_endpoint_ml():
    et = ZoneInfo("America/New_York")
    scan_time = datetime(2026, 8, 31, 10, 0, tzinfo=et)
    bars = [
        {"time": datetime(2026, 8, 31, 10, 5, tzinfo=et), "high": 104.0, "low": 99.5, "close": 103.0},
        {"time": datetime(2026, 8, 31, 10, 30, tzinfo=et), "high": 105.0, "low": 101.0, "close": 104.0},
        {"time": datetime(2026, 8, 31, 11, 0, tzinfo=et), "high": 102.0, "low": 99.0, "close": 101.0},
    ]
    indexed = {
        "bars": bars,
        "times": [row["time"] for row in bars],
        "prices": [row["close"] for row in bars],
    }
    path = soo.opportunity_path_metrics(
        indexed,
        scan_time,
        datetime(2026, 8, 31, 20, 0, tzinfo=et),
        100.0,
        horizon_minutes=60,
    )
    assert soo.path_success_label(path) == 1, path

    base = {
        "feature_version": "scanner-features-v2-consolidated",
        "observation_id": "synthetic:path",
        "scan_id": "synthetic",
        "scan_time_et": scan_time.isoformat(),
        "session_phase": "regular",
        "market_provider": "tradier",
        "live_feed": "consolidated",
        "symbol": "SYNTH",
        "entry_price": 100.0,
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "volume_pace": 2.0,
        "return_60m_pct": 1.0,
        "research_horizon_60m_complete": True,
        "research_path_success_60m": 1,
        "research_endpoint_success_60m": 0,
        "research_mfe_60m_pct": 5.0,
        "research_mae_60m_pct": -1.0,
    }
    production = _extract_observations({"source": "live_scan", "observations": [base]})
    shadow = _extract_path_research_observations(
        {
            "production_influence": False,
            "source": "shadow_opportunity",
            "observations": [base],
        }
    )
    assert len(production) == 1, production
    assert production[0]["label"] == 0, production
    assert len(shadow) == 1, shadow
    assert shadow[0]["label"] == 1, shadow
    assert shadow[0]["endpoint_label"] == 0, shadow
    assert shadow[0]["endpoint_path_disagreement"] is True, shadow


def test_historical_replay_path_fields_enter_shadow_loader():
    row = {
        "feature_version": "scanner-features-v2-consolidated",
        "observation_source": "historical_replay",
        "observation_id": "replay:synthetic:path",
        "scan_id": "historical-replay:2026-08-28:1000",
        "scan_time_et": "2026-08-28T10:00:00-04:00",
        "symbol": "SYNTH",
        "entry_price": 100.0,
        "return_60m_pct": 1.0,
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "volume_pace": 2.0,
        "opportunity_horizon_60m_complete": True,
        "opportunity_up_3_60m_hit": True,
        "opportunity_up_3_60m_before_stop": True,
        "opportunity_mfe_60m_pct": 5.0,
        "opportunity_mae_60m_pct": -1.0,
        "opportunity_time_to_peak_60m": 18.0,
    }
    payload = {
        "source": "historical_scanner_replay",
        "replay": {"historical_feed": "TRADIER CONSOLIDATED HISTORICAL"},
        "observations": [row],
    }
    shadow = _extract_path_research_observations(payload)
    assert len(shadow) == 1, shadow
    assert shadow[0]["label"] == 1, shadow
    assert shadow[0]["endpoint_label"] == 0, shadow
    assert shadow[0]["endpoint_path_disagreement"] is True, shadow
    assert shadow[0]["mfe_60m_pct"] == 5.0, shadow


def test_path_target_excludes_incomplete_horizons():
    row = {
        "feature_version": "scanner-features-v2-consolidated",
        "observation_id": "synthetic:incomplete",
        "scan_id": "synthetic",
        "scan_time_et": "2026-08-31T15:30:00-04:00",
        "session_phase": "regular",
        "market_provider": "tradier",
        "live_feed": "consolidated",
        "symbol": "SYNTH",
        "entry_price": 100.0,
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "volume_pace": 2.0,
        "research_horizon_60m_complete": False,
        "research_path_success_60m": 1,
    }
    shadow = _extract_path_research_observations(
        {"production_influence": False, "observations": [row]}
    )
    assert shadow == [], shadow


def test_live_journal_keeps_strongest_state_and_controls():
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 8, 31, 15, 2, tzinfo=et)

    rows = []
    for index in range(1, 101):
        rows.append(
            {
                "symbol": f"T{index:03d}",
                "price": 10.0 + index / 100.0,
                "score": 80.0 if index == 12 else 60.0 - index / 10.0,
                "opportunity_score": 82.0 if index == 12 else 60.0 - index / 10.0,
                "scanner_action": "ANALYZE NOW" if index == 12 else "WATCH",
                "scanner_action_tier": "ready" if index == 12 else "watch",
            }
        )

    selected = slj.select_observations(rows, now)
    symbols = {row["symbol"] for row in selected}
    assert "T001" in symbols, selected
    assert "T012" in symbols, selected
    assert "T015" in symbols, selected
    assert "T030" in symbols, selected
    assert "T045" in symbols, selected

    first = {
        "bucket_key": "2026-08-31T15:00:00-04:00:TEST",
        "bucket_start_et": "2026-08-31T15:00:00-04:00",
        "symbol": "TEST",
        "sample_role": "top",
        "first_observed_at_et": "2026-08-31T15:02:00-04:00",
        "last_observed_at_et": "2026-08-31T15:02:00-04:00",
        "best_observed_at_et": "2026-08-31T15:02:00-04:00",
        "rank": 5,
        "rank_best": 5,
        "rank_worst": 5,
        "score": 72.0,
        "opportunity_score": 74.0,
        "scanner_action": "WATCH",
        "scanner_action_tier": "watch",
        "actions_seen": ["WATCH"],
    }
    later = dict(first)
    later.update(
        {
            "last_observed_at_et": "2026-08-31T15:08:00-04:00",
            "rank": 2,
            "rank_best": 2,
            "rank_worst": 2,
            "score": 86.0,
            "opportunity_score": 90.0,
            "scanner_action": "ANALYZE NOW",
            "scanner_action_tier": "ready",
            "actions_seen": ["ANALYZE NOW"],
        }
    )
    merged = slj._merge_groups([first], [later])
    assert len(merged) == 1, merged
    row = merged[0]
    assert row["scanner_action"] == "ANALYZE NOW", row
    assert row["rank_best"] == 2, row
    assert row["rank_worst"] == 5, row
    assert row["actions_seen"] == ["WATCH", "ANALYZE NOW"], row
    assert row["first_observed_at_et"].endswith("15:02:00-04:00"), row
    assert row["last_observed_at_et"].endswith("15:08:00-04:00"), row


def test_extended_provider_uses_all_sessions():
    et = ZoneInfo("America/New_York")
    start = datetime.combine(date(2026, 8, 31), time(16, 0), tzinfo=et)
    end = datetime.combine(date(2026, 8, 31), time(20, 0), tzinfo=et)

    def fake_timesales(
        symbol,
        token,
        start_utc,
        end_utc,
        interval="1min",
        session_filter="open",
    ):
        assert symbol == "TEST"
        assert token == "synthetic-token"
        assert interval == "1min"
        assert session_filter == "all"
        return [
            {"t": "2026-08-31T20:01:00Z", "h": 10.5, "l": 10.0, "c": 10.4},
            {"t": "2026-08-31T21:00:00Z", "h": 11.2, "l": 10.3, "c": 11.0},
        ]

    old_fetch = so.get_tradier_timesales_bars
    old_token = so.TRADIER_TOKEN
    old_alpaca = so.ALPACA_CONFIGURED
    old_provider = so.OUTCOME_MARKET_PROVIDER
    try:
        so.get_tradier_timesales_bars = fake_timesales
        so.TRADIER_TOKEN = "synthetic-token"
        so.ALPACA_CONFIGURED = False
        so.OUTCOME_MARKET_PROVIDER = "tradier"
        bars, source = so.get_outcome_bars(
            {"TEST"},
            start,
            end,
            tradier_session_filter="all",
        )
        assert source == "tradier_1min_all", source
        assert len(bars.get("TEST") or []) == 2, bars
    finally:
        so.get_tradier_timesales_bars = old_fetch
        so.TRADIER_TOKEN = old_token
        so.ALPACA_CONFIGURED = old_alpaca
        so.OUTCOME_MARKET_PROVIDER = old_provider


def main():
    test_usde_like_endpoint_contradiction()
    test_shadow_research_fields_are_audited_too()
    test_source_contract_audit_finds_specification_gaps()
    test_hypothesis_generation_excludes_historical_replay()
    test_production_scanner_ml_is_regular_session_gated()
    test_opportunity_path_keeps_full_mfe_and_order()
    test_path_target_captures_interim_winner_without_changing_endpoint_ml()
    test_historical_replay_path_fields_enter_shadow_loader()
    test_path_target_excludes_incomplete_horizons()
    test_live_journal_keeps_strongest_state_and_controls()
    test_extended_provider_uses_all_sessions()
    print("LEARNING_SYSTEM_REGRESSIONS=passed")


if __name__ == "__main__":
    main()
