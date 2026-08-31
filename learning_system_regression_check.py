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

from learning_system_audit import audit_observations, audit_source_contracts
from scanner_ml_ranker import _production_regular_session
import score_outcomes as so
import score_opportunity_outcomes as soo


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
    test_production_scanner_ml_is_regular_session_gated()
    test_opportunity_path_keeps_full_mfe_and_order()
    test_extended_provider_uses_all_sessions()
    print("LEARNING_SYSTEM_REGRESSIONS=passed")


if __name__ == "__main__":
    main()
