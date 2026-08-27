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
        "decision_score_version": pt.DECISION_SCORE_VERSION,
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


def test_potential_does_not_recount_setup_score():
    import analyzer_v2_integration as v2

    metrics = {
        "score": 20,
        "day_pct": 18,
        "volume_pace": 2.5,
        "vwap_position": "ABOVE",
        "from_high_pct": 2.0,
        "historical_setup": {
            "status": "ok",
            "bias_score": 6,
            "sample_count": 20,
        },
        "ml_prediction": {
            "ml_edge_score": 68,
            "validated_edge_model_count": 2,
        },
    }
    sec = {"dilution_risk": "LOW"}
    market = {"label": "MIXED", "sector_move_pct": 0.5}
    catalyst = {"score": 3.0}

    low_setup = v2._potential_score(metrics, sec, market, catalyst)
    metrics["score"] = 95
    high_setup = v2._potential_score(metrics, sec, market, catalyst)

    assert low_setup[0] == high_setup[0], (low_setup, high_setup)
    assert low_setup[2] == high_setup[2], (low_setup, high_setup)
    assert "technical_momentum" in low_setup[2], low_setup


def test_entry_does_not_recount_spread_after_liquidity():
    import analyzer_v2_integration as v2

    metrics = {
        "price": 10.0,
        "vwap_position": "ABOVE",
        "momentum_5m": 0.5,
        "momentum_15m": 1.0,
        "vwap_extension_pct": 2.0,
        "spread_pct": 0.1,
        "liquidity": {"label": "HIGH"},
        "trade_plan": {
            "status": "ENTRY AVAILABLE",
            "action": "ENTRY AVAILABLE — pullback zone",
            "selected": {
                "entry_low": 9.95,
                "entry_high": 10.05,
                "risk_reward": 2.1,
            },
        },
    }
    tight = v2._entry_readiness(metrics)
    metrics["spread_pct"] = 4.9
    same_liquidity = v2._entry_readiness(metrics)

    assert tight[0] == same_liquidity[0], (tight, same_liquidity)
    assert tight[2] == same_liquidity[2], (tight, same_liquidity)


def test_entry_plan_status_is_safety_cap_not_double_count():
    import analyzer_v2_integration as v2

    metrics = {
        "price": 10.0,
        "vwap_position": "ABOVE",
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "vwap_extension_pct": 1.0,
        "liquidity": {"label": "HIGH"},
        "trade_plan": {
            "status": "NO TRADE",
            "action": "NO TRADE — external risk",
            "selected": {
                "entry_low": 9.95,
                "entry_high": 10.05,
                "risk_reward": 3.0,
            },
        },
    }
    score, blockers, components = v2._entry_readiness(metrics)
    assert score == 35.0, (score, blockers, components)
    assert components.get("plan_status_cap", 0) < 0, components


def _position_metrics(**overrides):
    metrics = {
        "price": 10.0,
        "vwap": 9.8,
        "vwap_position": "ABOVE",
        "vwap_extension_pct": 2.04,
        "momentum_5m": 0.5,
        "momentum_15m": 1.0,
        "day_pct": 18.0,
        "from_high_pct": 2.0,
        "score": 72.0,
        "atr_14": 0.50,
        "atr_14_pct": 5.0,
        "liquidity": {"label": "HIGH"},
        "supports": [
            {"price": 9.45, "quality": "Strong", "quality_score": 78},
            {"price": 8.90, "quality": "Moderate", "quality_score": 50},
        ],
        "resistances": [
            {"price": 10.55, "quality": "Moderate", "quality_score": 58},
            {"price": 11.20, "quality": "Strong", "quality_score": 75},
        ],
        "day_high": 10.60,
        "trade_plan": {
            "selected": {
                "target1": 10.60,
                "target2": 11.15,
                "stretch_target": 11.80,
            }
        },
        "decision_v2": {"potential_score": 70},
    }
    metrics.update(overrides)
    return metrics


def test_position_exit_profitable_hold():
    from position_exit import build_position_exit_plan

    plan = build_position_exit_plan(_position_metrics(), 8.50, 100)
    assert plan["status"] == "ok", plan
    assert plan["read"] in {"HOLD", "TRIM"}, plan
    assert plan["pnl_pct"] > 0, plan
    assert plan["total_pnl"] == 150.0, plan
    assert plan["protective_exit"] < plan["price"], plan
    assert plan["trailing_exit"] < plan["price"], plan
    assert plan["trailing_exit"] >= plan["protective_exit"], plan
    assert plan["first_trim"] > plan["price"], plan


def test_position_exit_underwater_weakness():
    from position_exit import build_position_exit_plan

    metrics = _position_metrics(
        price=9.0,
        vwap=9.4,
        vwap_position="BELOW",
        vwap_extension_pct=-4.26,
        momentum_5m=-1.0,
        momentum_15m=-2.0,
        score=44.0,
        supports=[{"price": 8.75, "quality": "Moderate", "quality_score": 50}],
        resistances=[{"price": 9.60, "quality": "Moderate", "quality_score": 55}],
        day_high=9.7,
    )
    plan = build_position_exit_plan(metrics, 10.0)
    assert plan["status"] == "ok", plan
    assert plan["read"] in {"EXIT", "REDUCE"}, plan
    assert plan["pnl_pct"] < 0, plan
    assert "EXIT" in plan["action"] or "REDUCE" in plan["action"], plan


def test_position_exit_profit_floor():
    from position_exit import build_position_exit_plan

    plan = build_position_exit_plan(_position_metrics(price=12.0), 9.0)
    assert plan["status"] == "ok", plan
    assert plan["protective_exit"] > 9.0, plan
    assert plan["protective_exit_return_pct"] > 0, plan


def test_position_live_overlay_recomputes_derived_fields():
    from position_exit import merge_live_position_metrics

    metrics = {
        "price": 10.0,
        "prev_close": 9.0,
        "vwap": 9.5,
        "vwap_position": "ABOVE",
        "vwap_extension_pct": 5.26,
        "day_high": 10.5,
        "from_high_pct": 4.76,
        "day_pct": 11.11,
    }
    overlay = {
        "provider": "tradier",
        "status": "streaming",
        "price": 11.0,
        "vwap": 10.0,
        "day_high": 11.2,
        "quote_age_seconds": 0.4,
        "trade_age_seconds": 1.2,
    }
    merged = merge_live_position_metrics(metrics, overlay)
    assert merged["position_live_provider"] == "tradier", merged
    assert merged["position_live_status"] == "streaming", merged
    assert merged["vwap_position"] == "ABOVE", merged
    assert abs(merged["vwap_extension_pct"] - 10.0) < 0.01, merged
    assert abs(merged["day_pct"] - 22.222) < 0.01, merged
    assert abs(merged["from_high_pct"] - 1.786) < 0.01, merged
    assert merged["quote_age_seconds"] == 0.4, merged


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
        test_potential_does_not_recount_setup_score,
        test_entry_does_not_recount_spread_after_liquidity,
        test_entry_plan_status_is_safety_cap_not_double_count,
        test_position_exit_profitable_hold,
        test_position_exit_underwater_weakness,
        test_position_exit_profit_floor,
        test_position_live_overlay_recomputes_derived_fields,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL CONSISTENCY REGRESSION CHECKS PASSED")
