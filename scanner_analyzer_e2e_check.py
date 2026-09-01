"""Deterministic Scanner -> Analyzer -> learning behavioral smoke checks.

This check avoids live provider calls. It exercises the real Streamlit workspace
handoff with a controlled scanner snapshot, then verifies the same analyzer data
can render a Plotly chart and that resolved scanner observations enter the ML
training extractor. Live-provider/deployment checks remain separate by design.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
LIVE_SCAN = ROOT / "scan_logs" / "latest_scan.json"
OFFHOURS_SCAN = ROOT / "scan_logs" / "offhours_timeframe_latest.json"


class _PendingProcess:
    def poll(self):
        return None


def _write_snapshots(symbols):
    now_et = datetime.now(ZoneInfo("America/New_York"))
    live_rows = []
    daily_rows = []
    for index, symbol in enumerate(symbols):
        score = 91.0 - index * 12.0
        live_rows.append(
            {
                "symbol": symbol,
                "setup_grade": "A" if index == 0 else "B",
                "score": score,
                "opportunity_score": score,
                "scanner_action": "ANALYZE NOW" if index == 0 else "WATCH",
                "scanner_action_tier": "ready" if index == 0 else "watch",
                "scanner_action_reason": "deterministic behavioral fixture",
                "timeframe_best_fit": "INTRADAY",
                "timeframe_fit_horizons": ["INTRADAY"],
                "day_pct": 8.0 - index,
                "volume_pace": 2.2 - index * 0.3,
                "volume_pace_display": 2.2 - index * 0.3,
                "volume_pace_display_source": "time_of_day_profile",
            }
        )
        daily_rows.append(
            {
                "symbol": symbol,
                "setup_grade": "A" if index == 0 else "B",
                "daily_discovery_score": score,
                "daily_review_action": "REVIEW",
                "daily_review_reason": "deterministic behavioral fixture",
                "timeframe_best_fit": "SWING",
                "timeframe_fit_horizons": ["SWING"],
                "day_pct": 8.0 - index,
                "daily_volume_ratio": 2.2 - index * 0.3,
            }
        )

    LIVE_SCAN.parent.mkdir(parents=True, exist_ok=True)
    LIVE_SCAN.write_text(
        json.dumps(
            {
                "scan_time_et": now_et.isoformat(),
                "session_phase": "regular",
                "candidates": live_rows,
            }
        ),
        encoding="utf-8",
    )
    OFFHOURS_SCAN.write_text(
        json.dumps(
            {
                "generated_at": now_et.isoformat(),
                "last_completed_session_date": now_et.date().isoformat(),
                "candidates": daily_rows,
            }
        ),
        encoding="utf-8",
    )


def _button_labels(app_test):
    return [button.label for button in app_test.button]


def test_workspace_handoff_refresh_and_return():
    """Exercise scanner render/rank, snapshot refresh, Analyze, and return."""
    from streamlit.testing.v1 import AppTest
    import analyzer_launch_runtime as runtime

    originals = {
        "start": runtime.start_analyzer_process,
        "poll": runtime.poll_analyzer_process,
        "cancel": runtime.cancel_analyzer_process,
    }
    backups = {
        path: path.read_bytes() if path.exists() else None
        for path in (LIVE_SCAN, OFFHOURS_SCAN)
    }

    def fake_start(symbol, **_kwargs):
        return {
            "started": True,
            "symbol": str(symbol).upper(),
            "process": _PendingProcess(),
            "started_at": time.time(),
        }

    runtime.start_analyzer_process = fake_start
    runtime.poll_analyzer_process = lambda _state: {
        "done": False,
        "runtime_seconds": 1.0,
    }
    runtime.cancel_analyzer_process = lambda state: {
        "cancelled": True,
        "message": f"Cancelled {state.get('symbol')}",
    }

    try:
        _write_snapshots(["AAPL", "MSFT"])
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        assert not app.exception, [str(item.value) for item in app.exception]
        labels = _button_labels(app)
        assert labels.index("Analyze AAPL") < labels.index("Analyze MSFT"), labels

        # A refreshed snapshot must replace the candidate order without a full
        # navigation or stale widget state.
        _write_snapshots(["MSFT", "AAPL"])
        app.run()
        labels = _button_labels(app)
        assert labels.index("Analyze MSFT") < labels.index("Analyze AAPL"), labels

        analyze = next(button for button in app.button if button.label == "Analyze MSFT")
        analyze.click().run()
        assert not app.exception, [str(item.value) for item in app.exception]
        assert app.radio[0].value == "Stock Analyzer"
        assert any("Loading deep analysis for MSFT" in item.value for item in app.markdown)
        assert any("Analyzing MSFT in the background" in item.value for item in app.info)

        cancel = next(button for button in app.button if button.label == "Cancel MSFT")
        cancel.click().run()
        assert not app.exception, [str(item.value) for item in app.exception]
        assert app.radio[0].value == "Momentum Scanner"
        assert "Analyze MSFT" in _button_labels(app)
    finally:
        runtime.start_analyzer_process = originals["start"]
        runtime.poll_analyzer_process = originals["poll"]
        runtime.cancel_analyzer_process = originals["cancel"]
        for path, payload in backups.items():
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(payload)


def test_analyzer_chart_and_scanner_data_agree():
    """Verify the Analyzer's production chart path uses the handed-off prices."""
    import analyzer_visuals as visuals

    bars = []
    closes = (10.00, 10.08, 10.14, 10.20)
    for index, close in enumerate(closes):
        bars.append(
            {
                "t": f"2026-09-01T14:{30 + index:02d}:00Z",
                "o": close - 0.03,
                "h": close + 0.08,
                "l": close - 0.08,
                "c": close,
                "v": 100_000 + index * 10_000,
            }
        )
    scanner_candidate = {
        "symbol": "AAPL",
        "price": closes[-1],
        "market_provider": "tradier",
        "live_quote_source": "tradier consolidated",
    }
    analyzer_result = {
        **scanner_candidate,
        "vwap": 10.10,
        "chart_data": {"intraday": bars},
        "trade_plan": {
            "selected": {
                "entry_low": 10.10,
                "entry_high": 10.18,
                "stop": 9.95,
                "target1": 10.45,
                "target2": 10.70,
            }
        },
    }

    figure = visuals.trade_plan_plotly_figure(analyzer_result)
    assert figure is not None
    assert figure.data and figure.data[0].type == "candlestick"
    assert float(figure.data[0].close[-1]) == scanner_candidate["price"]
    assert analyzer_result["symbol"] == scanner_candidate["symbol"]
    assert analyzer_result["market_provider"] == scanner_candidate["market_provider"]


def test_detected_setup_outcome_reaches_ml_extractor():
    """Verify a captured setup plus a resolved 60m outcome becomes an ML row."""
    import scanner_live_journal as journal
    import scanner_ml_ranker as ranker

    observed_at = datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    candidate = {
        "symbol": "AAPL",
        "price": 10.20,
        "score": 91.0,
        "opportunity_score": 88.0,
        "scanner_action": "ANALYZE NOW",
        "scanner_action_tier": "ready",
        "feature_version": ranker.CURRENT_FEATURE_VERSION,
        "market_provider": "tradier",
        "live_quote_source": "tradier consolidated",
        "momentum_5m": 1.2,
        "momentum_15m": 2.1,
        "volume_pace": 2.4,
        "above_vwap": True,
    }
    captured = journal.select_observations([candidate], observed_at)
    assert len(captured) == 1 and captured[0]["symbol"] == "AAPL", captured

    resolved = dict(captured[0])
    resolved.update(
        {
            "observation_id": "e2e:AAPL",
            "scan_id": "e2e",
            "scan_time_et": observed_at.isoformat(),
            "session_phase": "regular",
            "feature_version": ranker.CURRENT_FEATURE_VERSION,
            "market_provider": "tradier",
            "live_quote_source": "tradier consolidated",
            "return_60m_pct": 4.2,
        }
    )
    training_rows = ranker._extract_observations(
        {
            "source": "live_scan",
            "session_phase": "regular",
            "observations": [resolved],
        }
    )
    assert len(training_rows) == 1, training_rows
    assert training_rows[0]["symbol"] == "AAPL"
    assert training_rows[0]["label"] == 1
    assert training_rows[0]["features"]["momentum_5m"] == 1.2


if __name__ == "__main__":
    checks = (
        test_workspace_handoff_refresh_and_return,
        test_analyzer_chart_and_scanner_data_agree,
        test_detected_setup_outcome_reaches_ml_extractor,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print("ALL SCANNER/ANALYZER E2E CHECKS PASSED")
