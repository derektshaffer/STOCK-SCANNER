"""Regression checks for consolidated live-data/provider consistency.

These checks are deliberately network-free. They exercise the provider-selection
and feature-version boundaries that protect live decisions and calibration data.
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# stock_analyzer decides provider availability at import time.
os.environ["TRADIER_ACCESS_TOKEN"] = "test-token"

import stock_analyzer as sa

ET = ZoneInfo("America/New_York")


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _regular_bars():
    today = datetime.now(ET).date()
    start = datetime(today.year, today.month, today.day, 10, 0, tzinfo=ET)
    bars = []
    for i in range(45):
        px = 10.0 + i * 0.002
        bars.append(
            {
                "t": _iso(start + timedelta(minutes=i)),
                "o": px - 0.01,
                "h": px + 0.02,
                "l": px - 0.02,
                "c": px,
                "v": 1000 + i,
                "vw": px + 0.001,
            }
        )
    return bars


def _daily_bars():
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(35, 0, -1):
        px = 8.5 + (35 - i) * 0.04
        rows.append(
            {
                "t": _iso(now - timedelta(days=i)),
                "o": px - 0.05,
                "h": px + 0.15,
                "l": px - 0.12,
                "c": px,
                "v": 1_000_000,
            }
        )
    return rows


def _install_common_analyzer_stubs():
    now = datetime.now(timezone.utc)
    sa.snapshot = lambda symbol, feed=None: {
        "latestTrade": {"p": 9.0, "t": _iso(now - timedelta(seconds=4))},
        "latestQuote": {
            "bp": 8.95,
            "ap": 9.05,
            "t": _iso(now - timedelta(seconds=2)),
        },
        "dailyBar": {"c": 9.0, "h": 9.2, "l": 8.7, "v": 50_000},
        "prevDailyBar": {"c": 8.8},
    }
    sa.avg_daily_volume = lambda symbol, now: (1_000_000.0, "delayed SIP")
    sa.try_sip_delayed_bars = (
        lambda symbol, timeframe, start, end, limit=1000: (_daily_bars(), "delayed SIP")
    )
    sa.support_resistance_touch_bars = lambda symbol, now, live_session_bars=None: []
    sa.historical_spikes = lambda symbol, now, current_day_pct, threshold=None: {
        "status": "insufficient_history",
        "feed": "delayed SIP",
        "samples": [],
    }
    sa.news = lambda symbol, now, hours=96, limit=50: []


def test_analyzer_prefers_tradier():
    _install_common_analyzer_stubs()
    bars = _regular_bars()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    sa.get_tradier_quotes = lambda symbols, token: {
        "TEST": {
            "symbol": "TEST",
            "last": 10.10,
            "bid": 10.09,
            "ask": 10.11,
            "prevclose": 9.70,
            "high": 10.20,
            "low": 9.60,
            "trade_date": now_ms,
            "bid_date": now_ms,
            "ask_date": now_ms,
        }
    }
    sa.get_tradier_timesales_bars = (
        lambda symbol, token, start, end, interval="1min", session_filter="all": bars
    )

    result = sa.analyze("TEST")
    assert result["market_provider"] == "tradier", result
    assert result["live_feed"] == "TRADIER CONSOLIDATED", result
    assert result["feature_version"] == "analyzer-features-v2-consolidated", result
    assert abs(result["price"] - 10.10) < 1e-9, result
    assert result["bid"] == 10.09 and result["ask"] == 10.11, result
    assert result["volume_source"] == "TRADIER CONSOLIDATED", result
    assert result["vwap"] is not None, result
    assert result["trade_plan"], result


def test_analyzer_falls_back_cleanly():
    _install_common_analyzer_stubs()
    fallback_bars = _regular_bars()
    sa.latest_session_bars = lambda symbol, now: fallback_bars

    def _fail(*args, **kwargs):
        raise RuntimeError("simulated Tradier outage")

    sa.get_tradier_quotes = _fail

    result = sa.analyze("TEST")
    assert result["market_provider"] == "alpaca", result
    assert result["live_feed"] == sa.LIVE_FEED.upper(), result
    assert result["live_provider_error"], result
    assert abs(result["price"] - 9.0) < 1e-9, result


def test_scanner_ml_version_gate():
    import scanner_ml_ranker as sm

    base = {
        "scan_id": "scan-1",
        "symbol": "TEST",
        "scan_time_et": "2026-08-27T10:00:00-04:00",
        "return_60m_pct": 4.0,
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "volume_pace": 2.5,
        "spread_pct": 0.12,
    }
    legacy = dict(base, observation_id="legacy", feature_version="legacy-scanner-features-v1")
    current = dict(
        base,
        observation_id="current",
        feature_version=sm.CURRENT_FEATURE_VERSION,
    )
    rows = sm._extract_observations({"observations": [legacy, current]})
    assert len(rows) == 1, rows
    assert rows[0]["observation_id"] == "current", rows
    assert "spread_pct" in sm.FEATURES
    assert "iex_spread_pct" not in sm.FEATURES


def test_analyzer_calibration_version_gate():
    import prediction_tracker as pt

    pt._load_durable_calibration = lambda: {}
    current = {
        "symbol": "TEST",
        "timestamp": "2026-08-27T14:00:00+00:00",
        "feature_version": pt.ANALYZER_FEATURE_VERSION,
        "potential_score": 75,
        "entry_readiness": 65,
        "outcomes": {"return_60m_pct": 3.0},
    }
    legacy = {
        "symbol": "OLD",
        "timestamp": "2026-08-27T15:00:00+00:00",
        "feature_version": "legacy-analyzer-features-v1",
        "potential_score": 95,
        "entry_readiness": 95,
        "outcomes": {"return_60m_pct": -5.0},
    }
    summary = pt.tracker_summary(rows=[legacy, current])
    assert summary["total_predictions"] == 1, summary
    assert summary["legacy_predictions_excluded"] == 1, summary
    assert summary["resolved_60m"] == 1, summary
    assert summary["higher_60m_rate"] == 100.0, summary


def test_scanner_outcome_metadata():
    # score_outcomes validates environment at import time, but this regression
    # check does not make any network calls.
    os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")
    os.environ.setdefault("GH_TOKEN", "test-token")
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")

    import score_outcomes as so

    target_date = datetime.now(ET).date()
    scan_time = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        10,
        0,
        tzinfo=ET,
    )
    scans = [
        {
            "scan_id": "scan-current",
            "scan_time_et": scan_time.isoformat(),
            "feature_version": so.SCANNER_FEATURE_VERSION,
            "data": {
                "live_provider": "tradier",
                "live_feed": "consolidated",
            },
            "candidates": [
                {
                    "rank": 1,
                    "symbol": "TEST",
                    "price": 10.0,
                    "live_spread_pct": 0.11,
                    "liquidity_source": "tradier_consolidated",
                    "live_quote_source": "tradier_consolidated",
                    "live_intraday_source": "tradier_timesales",
                }
            ],
        }
    ]
    rows = so.build_observations(scans, target_date, {})
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["feature_version"] == so.SCANNER_FEATURE_VERSION, row
    assert row["market_provider"] == "tradier", row
    assert row["live_feed"] == "consolidated", row
    assert row["spread_pct"] == 0.11, row
    assert row["live_quote_source"] == "tradier_consolidated", row


def test_stream_seed_rejects_non_tradier_metrics():
    import tradier_live_stream as tls

    stream = tls._TradierStream()
    stream.state = stream._blank()
    stream.state["symbol"] = "TEST"
    stream._seed(
        {
            "symbol": "TEST",
            "market_provider": "alpaca",
            "live_feed": "IEX",
            "session_volume": 999999,
            "vwap": 7.77,
            "day_high": 8.0,
            "day_low": 7.0,
            "as_of": "2026-08-27T14:00:00+00:00",
        }
    )
    assert stream.state["session_volume"] is None, stream.state
    assert stream.state["session_vwap"] is None, stream.state
    assert stream.state["seed_provider"] is None, stream.state

    stream._seed(
        {
            "symbol": "TEST",
            "market_provider": "tradier",
            "live_feed": "TRADIER CONSOLIDATED",
            "session_volume": 1000,
            "vwap": 10.0,
            "day_high": 10.5,
            "day_low": 9.5,
            "as_of": "2026-08-27T14:00:00+00:00",
            "latest_trade_time": "2026-08-27T13:59:59+00:00",
            "latest_quote_time": "2026-08-27T13:59:59.500000+00:00",
        }
    )
    assert stream.state["session_volume"] == 1000, stream.state
    assert stream.state["vwap_volume"] == 1000, stream.state
    assert stream.state["session_vwap"] == 10.0, stream.state
    assert stream.state["seed_provider"] == "tradier", stream.state


def test_stream_vwap_ignores_cvol_as_denominator():
    import tradier_live_stream as tls

    stream = tls._TradierStream()
    stream.state = stream._blank()
    stream.state.update(
        {
            "symbol": "TEST",
            "session_volume": 1000.0,
            "vwap_volume": 1000.0,
            "session_pv": 10000.0,
            "session_vwap": 10.0,
            "seed_cutoff": 100.0,
            "seed_provider": "tradier",
        }
    )
    stream._accumulate_trade(
        {"size": 10, "date": 101000, "cvol": 5000},
        11.0,
    )
    assert stream.state["session_volume"] == 5000.0, stream.state
    assert stream.state["vwap_volume"] == 1010.0, stream.state
    expected = (10000.0 + 110.0) / 1010.0
    assert abs(stream.state["session_vwap"] - expected) < 1e-9, stream.state


def test_stream_reports_trade_and_quote_freshness():
    import time
    import tradier_live_stream as tls

    stream = tls._TradierStream()
    stream.state = stream._blank()
    now = time.time()
    stream.state["last_trade_at"] = now - 2.0
    stream.state["last_quote_at"] = now - 0.25
    public = stream._public()
    assert public["trade_age_seconds"] is not None, public
    assert public["quote_age_seconds"] is not None, public
    assert public["trade_age_seconds"] > public["quote_age_seconds"], public


def test_evidence_recognizes_tradier_consolidated():
    import analyzer_v2_integration as v2

    metrics = {
        "market_provider": "tradier",
        "live_feed": "TRADIER CONSOLIDATED",
        "trade_age_seconds": 1.0,
        "historical_setup": {"sample_count": 0},
        "ml_prediction": {"validated_edge_model_count": 0},
    }
    score, reasons = v2._evidence_strength(
        metrics,
        {"status": "unavailable"},
        {"label": "UNKNOWN"},
        {"article_count": 0},
    )
    assert score >= 20.0, (score, reasons)
    assert "Tradier consolidated live feed" in reasons, reasons
    assert "IEX-only live feed" not in reasons, reasons


if __name__ == "__main__":
    tests = [
        test_analyzer_prefers_tradier,
        test_analyzer_falls_back_cleanly,
        test_scanner_ml_version_gate,
        test_analyzer_calibration_version_gate,
        test_scanner_outcome_metadata,
        test_stream_seed_rejects_non_tradier_metrics,
        test_stream_vwap_ignores_cvol_as_denominator,
        test_stream_reports_trade_and_quote_freshness,
        test_evidence_recognizes_tradier_consolidated,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL CONSISTENCY REGRESSION CHECKS PASSED")
