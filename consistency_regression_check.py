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
    # Keep this regression deterministic on weekends/holidays. The production
    # helper intentionally filters Time & Sales to today's regular session;
    # this test is about provider preference, not the calendar.
    sa._tradier_regular_session_bars = lambda symbol, now: bars

    result = sa.analyze("TEST")
    assert result["market_provider"] == "tradier", result
    assert result["live_feed"] == "TRADIER CONSOLIDATED", result
    assert result["feature_version"] == "analyzer-features-v6-sequence-regimes", result
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
                    "scanner_action": "ANALYZE NOW",
                    "scanner_action_tier": "watch",
                    "scanner_action_reason": "test action",
                    "volume_pace_display": 2.4,
                    "volume_pace_display_source": "analyzer_aligned_regular",
                }
            ],
        }
    ]
    rows = so.build_observations(scans, target_date, {})
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["feature_version"] == so.SCANNER_FEATURE_VERSION, row
    assert row["observation_source"] == "live_scan", row
    assert row["market_provider"] == "tradier", row
    assert row["live_feed"] == "consolidated", row
    assert row["spread_pct"] == 0.11, row
    assert row["live_quote_source"] == "tradier_consolidated", row
    assert row["scanner_action"] == "ANALYZE NOW", row
    assert row["volume_pace_display"] == 2.4, row


def test_historical_trade_quality_path_is_conservative():
    import historical_scanner_replay as replay

    rows = [
        (600, {"h": 10.0, "l": 10.0, "c": 10.0}),
        (605, {"h": 10.2, "l": 9.9, "c": 10.05}),
        (610, {"h": 10.3, "l": 10.0, "c": 10.2}),
    ]
    result = replay._future_trade_quality(
        rows,
        0,
        10.0,
        minutes=60,
        target_pct=1.0,
        stop_pct=0.75,
    )
    assert result.get("trade_quality_barrier") == "stop_first", result
    assert result.get("target_before_stop") is False, result
    assert result.get("trade_quality_decisive") is True, result


def test_scanner_trade_quality_path_is_causal_and_conservative():
    import score_outcomes as so

    scan_time = datetime(2026, 8, 27, 10, 0, tzinfo=ET)
    session_close = datetime(2026, 8, 27, 16, 0, tzinfo=ET)

    target_first = so.index_bars({
        "TEST": [
            {"t": "2026-08-27T14:00:00Z", "c": 10.0, "h": 10.8, "l": 9.2},
            {"t": "2026-08-27T14:01:00Z", "c": 10.08, "h": 10.12, "l": 9.98},
            {"t": "2026-08-27T14:02:00Z", "c": 10.15, "h": 10.18, "l": 10.05},
        ]
    })["TEST"]
    result = so.trade_quality_path(
        target_first,
        scan_time,
        session_close,
        10.0,
    )
    assert result.get("trade_quality_barrier") == "target_first", result
    assert result.get("target_before_stop") is True, result

    # If both barriers are inside the same 1-minute OHLC bar, the true order
    # is unknowable; score the stop first rather than flattering the model.
    ambiguous = so.index_bars({
        "TEST": [
            {"t": "2026-08-27T14:01:00Z", "c": 10.0, "h": 10.2, "l": 9.9},
        ]
    })["TEST"]
    result = so.trade_quality_path(
        ambiguous,
        scan_time,
        session_close,
        10.0,
    )
    assert result.get("trade_quality_barrier") == "stop_first", result
    assert result.get("target_before_stop") is False, result


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


def test_analyzer_ui_preserves_historical_context_dependencies():
    from pathlib import Path

    source = Path("analyzer_ui_core.py").read_text(encoding="utf-8")
    momentum = source.find("Momentum & liquidity")
    support = source.find("Support</div>")
    resistance = source.find("Resistance</div>")
    hist_assign = source.find('h=r.get("historical_analogs") or {}')
    hist_use = source.find('if h.get("status")=="ok":')

    assert momentum >= 0, "Momentum & liquidity section is missing"
    assert support > momentum, "Support section is missing or out of order"
    assert resistance > support, "Resistance section is missing or out of order"
    assert hist_assign > resistance, "Historical analogs variable is not initialized"
    assert hist_use > hist_assign, "Historical analogs variable is used before assignment"


def test_analyzer_shared_button_styles_live_in_bootstrap():
    from pathlib import Path

    source = Path("analyzer_bootstrap.py").read_text(encoding="utf-8")
    required = [
        ".st-key-saved_stocks_top button[data-testid=\"stBaseButton-secondary\"]",
        ".st-key-analyzer_live_fragment button[data-testid=\"stBaseButton-secondary\"]",
        ".st-key-analyzer_live_fragment [data-testid=\"stPopover\"] button",
        "background: #11243a !important;",
        "color: #edf5ff !important;",
    ]
    missing = [item for item in required if item not in source]
    assert not missing, f"Missing shared Analyzer button styles: {missing}"


def test_scanner_aligned_volume_pace_matches_analyzer_baseline():
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
    import stock_scanner as ss

    now_et = datetime(2026, 8, 27, 14, 22, tzinfo=ET)
    avg_volume = 1_000_000.0
    session_volume = 750_000.0
    expected = avg_volume * ss.session_fraction(now_et)
    pace = ss.analyzer_aligned_volume_pace(
        session_volume,
        avg_volume,
        now_et,
    )
    assert pace is not None
    assert abs(pace - (session_volume / expected)) < 1e-9, pace


def _scanner_action_row(**overrides):
    row = {
        "market_session": "regular",
        "setup_grade": "A",
        "failed_count": 0,
        "critical_fail_count": 0,
        "failed_filters": [],
        "tradability_warnings": [],
        "spread_pct": 0.5,
        "day_pct": 20.0,
        "distance_from_high_pct": 2.0,
        "distance_from_vwap_pct": 3.0,
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "volume_pace_display": 3.0,
        "above_vwap": True,
    }
    row.update(overrides)
    return row


def test_scanner_action_avoids_chasing_extreme_mover():
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
    import stock_scanner as ss

    action = ss.scanner_action_signal(
        _scanner_action_row(day_pct=56.7, distance_from_vwap_pct=5.4)
    )
    assert action["label"] == "WAIT PULLBACK", action


def test_scanner_action_analyze_now_requires_aligned_conditions():
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
    import stock_scanner as ss

    action = ss.scanner_action_signal(_scanner_action_row())
    assert action["label"] == "ANALYZE NOW", action
    assert action["tier"] == "watch", action


def test_scanner_action_breakout_watch_near_high():
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
    import stock_scanner as ss

    action = ss.scanner_action_signal(
        _scanner_action_row(distance_from_high_pct=0.8)
    )
    assert action["label"] == "BREAKOUT WATCH", action


def test_scanner_action_reject_stays_no_trade():
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
    import stock_scanner as ss

    action = ss.scanner_action_signal(
        _scanner_action_row(
            setup_grade="REJECT",
            critical_fail_count=1,
            failed_count=1,
            failed_filters=["liquidity failure"],
        )
    )
    assert action["label"] == "NO TRADE", action


def test_scanner_ui_auto_surfaces_validated_ml():
    from pathlib import Path

    source = Path("app.py").read_text(encoding="utf-8")
    assert 'label = "ACTION"' in source
    assert 'label += f" · ML {probability:.0f}%"' in source
    assert "volume_pace_display" in source
    assert "combined-action-value" in source


def _scanner_action_behavior_base():
    return {
        "market_session": "regular",
        "setup_grade": "B",
        "failed_count": 0,
        "critical_fail_count": 0,
        "tradability_warnings": [],
        "spread_pct": 0.5,
        "day_pct": 18.0,
        "distance_from_high_pct": 2.0,
        "distance_from_vwap_pct": 2.0,
        "above_vwap": True,
        "momentum_5m": 0.8,
        "momentum_15m": 1.4,
        "volume_pace": 1.7,
        "volume_pace_display": 1.7,
    }


def test_scanner_action_failed_breakout_forces_wait():
    import stock_scanner as ss

    row = _scanner_action_behavior_base()
    row.update({
        "failed_breakout": 1.0,
        "breakout_holding": 0.0,
        "volume_accelerating": 1.0,
    })
    action = ss.scanner_action_signal(row, use_behavior=True)
    assert action.get("label") == "WAIT", action
    assert action.get("tier") == "caution", action
    assert "breakout failed" in str(action.get("reason") or "").lower(), action


def test_scanner_action_production_default_ignores_unvalidated_behavior():
    import stock_scanner as ss

    row = _scanner_action_behavior_base()
    row.update({
        "failed_breakout": 1.0,
        "vwap_rejection": 1.0,
        "bounce_leg_code": -1.0,
        "pullback_quality_score": 20.0,
    })
    default_action = ss.scanner_action_signal(row)
    legacy_action = ss.scanner_action_signal(row, use_behavior=False)
    research_action = ss.scanner_action_signal(row, use_behavior=True)
    assert default_action == legacy_action, (default_action, legacy_action)
    assert research_action.get("label") == "WAIT", research_action
    assert default_action.get("label") != "WAIT", default_action


def test_scanner_action_legacy_mode_ignores_behavior_state():
    import stock_scanner as ss

    row = _scanner_action_behavior_base()
    row.update({
        "failed_breakout": 1.0,
        "vwap_rejection": 1.0,
        "bounce_leg_code": -1.0,
        "pullback_quality_score": 20.0,
    })
    behavior_action = ss.scanner_action_signal(row, use_behavior=True)
    legacy_action = ss.scanner_action_signal(row, use_behavior=False)
    assert behavior_action.get("label") == "WAIT", behavior_action
    assert legacy_action.get("label") != "WAIT", legacy_action


def test_scanner_action_b_grade_vwap_reclaim_stays_bounce_watch():
    import stock_scanner as ss

    row = _scanner_action_behavior_base()
    row.update({
        "vwap_reclaim": 1.0,
        "bounce_leg_code": 1.0,
        "pullback_quality_score": 78.0,
        "volume_accelerating": 0.0,
        "breakout_holding": 0.0,
    })
    action = ss.scanner_action_signal(row, use_behavior=True)
    assert action.get("label") == "BOUNCE WATCH", action
    assert action.get("tier") == "breakout", action
    assert "still b-grade" in str(action.get("reason") or "").lower(), action


def test_scanner_action_a_grade_vwap_reclaim_can_be_analyze_now():
    import stock_scanner as ss

    row = _scanner_action_behavior_base()
    row["setup_grade"] = "A"
    row.update({
        "vwap_reclaim": 1.0,
        "bounce_leg_code": 1.0,
        "pullback_quality_score": 78.0,
        "volume_accelerating": 0.0,
        "breakout_holding": 0.0,
    })
    action = ss.scanner_action_signal(row, use_behavior=True)
    assert action.get("label") == "ANALYZE NOW", action
    assert action.get("tier") == "watch", action
    assert "a-grade pullback" in str(action.get("reason") or "").lower(), action


def test_scanner_action_active_pullback_waits_for_confirmation():
    import stock_scanner as ss

    row = _scanner_action_behavior_base()
    row.update({
        "bounce_leg_code": -1.0,
        "pullback_quality_score": 58.0,
        "vwap_reclaim": 0.0,
        "breakout_holding": 0.0,
    })
    action = ss.scanner_action_signal(row, use_behavior=True)
    assert action.get("label") == "WAIT PULLBACK", action
    assert action.get("tier") == "pullback", action
    assert "still in a pullback" in str(action.get("reason") or "").lower(), action


def test_scanner_action_behavior_never_overrides_reject():
    import stock_scanner as ss

    row = _scanner_action_behavior_base()
    row.update({
        "setup_grade": "REJECT",
        "failed_count": 2,
        "failed_filters": ["liquidity failure", "below VWAP"],
        "vwap_reclaim": 1.0,
        "bounce_leg_code": 1.0,
        "pullback_quality_score": 90.0,
        "breakout_holding": 1.0,
        "volume_accelerating": 1.0,
    })
    action = ss.scanner_action_signal(row, use_behavior=True)
    assert action.get("label") == "NO TRADE", action


def test_historical_replay_universe_uses_prior_days_only():
    import historical_scanner_replay as replay

    replay_day = datetime(2026, 8, 20, tzinfo=ET).date()
    prior_days = [
        datetime(2026, 8, day, tzinfo=ET).date()
        for day in range(10, 20)
    ]
    quiet_prior = [
        (day, {"c": 10.0, "v": 20_000})
        for day in prior_days
    ]
    liquid_prior = [
        (day, {"c": 10.0, "v": 2_000_000})
        for day in prior_days
    ]
    # Huge volume on the replay day itself must NOT influence universe choice.
    quiet_prior.append(
        (replay_day, {"c": 20.0, "v": 100_000_000})
    )
    daily_index = {
        "QUIET": quiet_prior,
        "LIQUID": liquid_prior,
    }

    selected, metrics = replay.select_daily_universe(
        daily_index,
        replay_day,
        1,
    )
    assert selected == ["LIQUID"], (selected, metrics)


def test_historical_replay_source_survives_ml_extraction():
    import scanner_ml_ranker as sm

    payload = {
        "source": "historical_scanner_replay",
        "observations": [
            {
                "observation_id": "replay:test",
                "observation_source": "historical_replay",
                "feature_version": sm.CURRENT_FEATURE_VERSION,
                "scan_id": "historical-replay:2026-08-20:1005",
                "scan_time_et": "2026-08-20T10:05:00-04:00",
                "symbol": "TEST",
                "return_60m_pct": 4.0,
                "momentum_5m": 1.0,
                "momentum_15m": 2.0,
                "volume_pace": 2.5,
            }
        ],
    }
    rows = sm._extract_observations(payload)
    assert len(rows) == 1, rows
    assert rows[0]["observation_source"] == "historical_replay", rows
    assert rows[0]["trading_date"] == "2026-08-20", rows


def test_replay_requires_live_confirmation_before_full_badge():
    import scanner_ml_ranker as sm

    assert sm.MIN_LIVE_CONFIRMATION_SAMPLES >= 30
    assert sm.MIN_LIVE_CONFIRMATION_DAYS >= 2
    assert sm.MIN_LIVE_CONFIRMATION_CLASS_COUNT >= 5


def test_analyzer_ml_validation_requires_probability_skill():
    import ml_predictor as ml

    actual = [0, 1] * 40
    strong = [0.15 if value == 0 else 0.85 for value in actual]
    naive = [0.5] * len(actual)
    good = ml._probability_validation_summary(actual, strong, naive)
    assert good["validated"] is True, good
    assert float(good["auc"]) > 0.95, good
    assert float(good["brier"]) < float(good["baseline_brier"]), good

    # High classification accuracy alone is not enough if probability quality
    # does not beat a proper naive probability forecast.
    imbalanced = [0] * 72 + [1] * 8
    weak = [0.10] * len(imbalanced)
    base = [0.10] * len(imbalanced)
    bad = ml._probability_validation_summary(imbalanced, weak, base)
    assert bad["accuracy"] >= 0.85, bad
    assert bad["validated"] is False, bad
    assert abs(float(bad["brier"]) - float(bad["baseline_brier"])) < 1e-12, bad


def test_impulse_detector_measures_fraction_of_run():
    import stock_analyzer as sa

    bars = []
    price = 10.0
    # Build a clear impulse from ~10 to ~20, then a 40% retracement to ~16,
    # followed by a partial bounce. Only past/current bars are supplied.
    for i in range(12):
        close = 10.0 + i * (10.0 / 11.0)
        bars.append({"h": close * 1.01, "l": close * 0.99, "c": close, "v": 1000 + i * 50})
    for close in (19.0, 18.0, 17.0, 16.0, 16.6, 17.2):
        bars.append({"h": close * 1.01, "l": close * 0.99, "c": close, "v": 700})

    ctx = sa.impulse_pullback_context(bars, current_price=17.2, atr_pct=8)
    assert ctx.get("detected"), ctx
    assert 25 <= float(ctx.get("max_retracement_pct")) <= 55, ctx
    assert float(ctx.get("bounce_recovery_pct")) > 0, ctx
    assert "38.2%" in (ctx.get("levels") or {}), ctx


def test_entry_readiness_penalizes_unconfirmed_shallow_retrace():
    import analyzer_v2_integration as v2

    metrics = {
        "price": 20.0,
        "vwap_position": "ABOVE",
        "vwap_extension_pct": 14.0,
        "momentum_5m": 1.0,
        "momentum_15m": 1.0,
        "liquidity": {"label": "HIGH"},
        "trade_plan": {
            "status": "WAIT",
            "action": "WAIT FOR REAL PULLBACK — price is extended",
            "selected": {
                "entry_low": 16.0,
                "entry_high": 17.0,
                "risk_reward": 2.0,
            },
        },
        "impulse_pullback": {
            "detected": True,
            "impulse_move_pct": 80.0,
            "current_retracement_pct": 12.0,
            "max_retracement_pct": 12.0,
            "bounce_recovery_pct": 0.0,
            "pullback_volume_ratio": 0.9,
            "bounce_confirmed": False,
        },
    }
    score, blockers, components = v2._entry_readiness(metrics)
    assert components.get("pullback_structure", 0) < 0, (score, blockers, components)
    assert any("retraced enough" in str(x) for x in blockers), blockers


def test_run_exhaustion_flags_rejected_mature_run():
    import stock_analyzer as sa

    bars=[]
    # Strong run, then repeated upper-wick rejection / lower highs.
    for i in range(16):
        close=10.0+i*0.55
        bars.append({"o":close-0.2,"h":close+0.25,"l":close-0.25,"c":close,"v":1000+i*80})
    peak=bars[-1]["h"]
    for j,(o,h,l,cc,v) in enumerate([
        (18.5,19.1,18.0,18.35,4200),
        (18.4,19.0,17.8,18.05,3600),
        (18.1,18.8,17.4,17.75,3000),
        (17.8,18.5,17.0,17.35,2500),
        (17.4,18.2,16.8,17.0,2100),
        (17.0,17.8,16.4,16.7,1800),
    ]):
        bars.append({"o":o,"h":h,"l":l,"c":cc,"v":v})

    impulse=sa.impulse_pullback_context(bars,current_price=16.7,atr_pct=8)
    ex=sa.run_exhaustion_context(bars,current_price=16.7,vwap=14.0,atr_pct=8,impulse=impulse)
    assert ex.get("score") is not None, ex
    assert float(ex.get("score")) >= 60, ex
    assert ex.get("label") in {"HIGH","VERY HIGH"}, ex


def test_full_spectrum_exposes_all_scenarios():
    import analyzer_v2_integration as v2

    metrics={
        "price":10.0,"day_pct":25.0,"vwap_position":"ABOVE","vwap_extension_pct":7.0,
        "momentum_5m":1.2,"momentum_15m":2.0,"momentum_30m":3.0,
        "volume_pace":2.2,"from_high_pct":4.0,"spread_pct":0.8,
        "score":70.0,"liquidity":{"label":"HIGH"},
        "impulse_pullback":{
            "detected":True,"current_retracement_pct":38.0,
            "max_retracement_pct":42.0,"bounce_recovery_pct":9.0,
            "bounce_confirmed":True,"pullback_volume_ratio":0.7,
        },
        "run_exhaustion":{"score":28.0},
        "historical_setup":{
            "status":"ok","bias_score":5.0,"breakout_failure_pct":35.0,
            "breakout_follow_through_pct":60.0,"impulse_bounce_5pct_rate":64.0,
        },
        "ml_prediction":{"ml_edge_score":62.0,"models":{"reversal_30":{"probability_pct":32.0,"validated":True}}},
        "decision_v2":{"potential_score":68.0},
        "market_provider":"tradier","live_feed":"TRADIER CONSOLIDATED",
    }
    fs=v2._full_spectrum_analysis(
        metrics,
        {"status":"ok","dilution_risk":"NONE FOUND"},
        {"label":"RISK-ON","broad_market_avg_pct":0.8,"sector_move_pct":1.2},
        {"score":4.0},
        {"float_turnover":0.6},
    )
    assert fs.get("version")=="full-spectrum-v3-sequence-regimes", fs
    assert set((fs.get("scenarios") or {}).keys())=={
        "continuation","pullback_bounce","stair_reacceleration",
        "reversal_failure","sideways_chop"
    }, fs
    total=sum(float(v.get("relative_weight_pct") or 0) for v in fs["scenarios"].values())
    assert 99.0 <= total <= 101.0, fs


def test_multi_bounce_detector_tracks_decay_and_lower_highs():
    from multi_bounce import detect_bounce_sequence, bounce_feature_values

    bars=[]
    # Initial impulse ~10 -> 20
    for i in range(12):
        close=10.0+i*(10.0/11.0)
        bars.append({"o":close-0.1,"h":close+0.15,"l":close-0.15,"c":close,"v":1000+i*80})
    # Pullback -> bounce #1 -> pullback -> weaker bounce #2 -> pullback
    closes=[18.8,17.4,16.0,16.8,17.8,18.6,18.2,17.1,16.5,17.0,17.5,17.9,17.3]
    for j,close in enumerate(closes):
        bars.append({
            "o":close-0.08,
            "h":close+0.18,
            "l":close-0.18,
            "c":close,
            "v":900 if j<6 else 650,
        })

    seq=detect_bounce_sequence(bars,current_price=17.3,atr_pct=8)
    assert seq.get("detected"), seq
    assert int(seq.get("completed_bounces") or 0) >= 2, seq
    assert seq.get("bounce1_pct") is not None and seq.get("bounce2_pct") is not None, seq
    assert float(seq.get("bounce2_pct")) < float(seq.get("bounce1_pct")), seq
    assert float(seq.get("bounce_decay_ratio")) < 1.0, seq
    assert int(seq.get("lower_high_streak") or 0) >= 1, seq
    features=bounce_feature_values(seq)
    assert features.get("bounce_count") >= 2, features
    assert features.get("bounce_decay_ratio") is not None, features


def test_multi_bounce_full_spectrum_accepts_sequence_state():
    import analyzer_v2_integration as v2

    metrics={
        "price":10.0,"day_pct":30.0,"vwap_position":"ABOVE","vwap_extension_pct":6.0,
        "momentum_5m":0.5,"momentum_15m":1.0,"momentum_30m":2.0,
        "volume_pace":2.0,"from_high_pct":8.0,"spread_pct":0.8,
        "score":68.0,"liquidity":{"label":"HIGH"},
        "impulse_pullback":{
            "detected":True,"current_retracement_pct":42.0,
            "max_retracement_pct":48.0,"bounce_recovery_pct":7.0,
            "bounce_confirmed":True,"pullback_volume_ratio":0.75,
        },
        "bounce_sequence":{
            "detected":True,"completed_bounces":2,"current_leg":"PULLING BACK",
            "sequence_health_score":44.0,"bounce_decay_ratio":0.62,
            "bounce_volume_decay_ratio":0.70,"lower_high_streak":2,
            "higher_low_streak":0,
        },
        "run_exhaustion":{"score":65.0},
        "historical_setup":{
            "status":"ok","bias_score":2.0,"breakout_failure_pct":45.0,
            "breakout_follow_through_pct":50.0,"impulse_bounce_5pct_rate":60.0,
            "second_bounce_rate_pct":58.0,"third_bounce_rate_pct":34.0,
        },
        "ml_prediction":{
            "ml_edge_score":52.0,
            "models":{
                "reversal_30":{"probability_pct":58.0,"validated":True},
                "repeat_bounce_30":{"probability_pct":55.0,"validated":True},
                "new_high_60":{"probability_pct":30.0,"validated":True},
            },
        },
        "decision_v2":{"potential_score":60.0},
        "market_provider":"tradier","live_feed":"TRADIER CONSOLIDATED",
    }
    fs=v2._full_spectrum_analysis(
        metrics,
        {"status":"ok","dilution_risk":"NONE FOUND"},
        {"label":"NEUTRAL","broad_market_avg_pct":0.0,"sector_move_pct":0.0},
        {"score":0.0},
        {"float_turnover":0.5},
    )
    assert "multi_bounce_sequence" in (fs.get("categories") or {}), fs
    assert fs["categories"]["multi_bounce_sequence"]["score"] == 44.0, fs
    assert fs.get("scenarios",{}).get("pullback_bounce"), fs


def test_stair_step_detector_finds_higher_plateau_sequence():
    from stair_step import detect_stair_step, stair_step_feature_values

    daily=[
        {"t":"2026-08-20","o":9.9,"h":10.2,"l":9.8,"c":10.0,"v":1000},
        {"t":"2026-08-21","o":10.0,"h":10.4,"l":9.9,"c":10.2,"v":1100},
        {"t":"2026-08-24","o":10.3,"h":12.2,"l":10.2,"c":12.0,"v":4200},
        {"t":"2026-08-25","o":12.0,"h":12.35,"l":11.8,"c":12.15,"v":1600},
        {"t":"2026-08-26","o":12.1,"h":12.3,"l":11.95,"c":12.10,"v":1350},
        {"t":"2026-08-27","o":12.2,"h":15.3,"l":12.15,"c":15.0,"v":5200},
        {"t":"2026-08-28","o":15.0,"h":15.45,"l":14.75,"c":15.2,"v":1800},
    ]
    ctx=detect_stair_step(daily,atr_pct=8)
    assert ctx.get("detected"), ctx
    assert int(ctx.get("step_count") or 0) >= 2, ctx
    assert float(ctx.get("structure_score") or 0) >= 55, ctx
    assert ctx.get("state") in {
        "HIGHER PLATEAU / COILING",
        "STAIR-STEP TREND",
        "REACCELERATING STAIR-STEP",
    }, ctx
    features=stair_step_feature_values(ctx)
    assert features.get("stair_step_count",0) >= 2, features
    assert features.get("stair_structure_score") is not None, features


def test_dedicated_repeat_bounce_trade_plan_uses_latest_dip():
    import stock_analyzer as sa

    metrics={
        "price":8.10,
        "vwap":8.00,
        "vwap_extension_pct":1.25,
        "day_pct":12.0,
        "momentum_5m":0.8,
        "momentum_15m":0.5,
        "volume_pace":2.0,
        "spread_pct":0.5,
        "score":75.0,
        "atr_14":0.81,
        "atr_14_pct":10.0,
        "supports":[{"price":7.85,"quality_score":70,"quality":"STRONG","side":"support"}],
        "resistances":[{"price":10.0,"quality_score":70,"quality":"STRONG","side":"resistance"}],
        "historical_analogs":{"status":"insufficient_history","samples":[]},
        "historical_setup":{
            "status":"ok",
            "sample_count":20,
            "next_day_up_pct":55.0,
            "intraday":{
                "median_bounce2_pct":7.0,
                "second_bounce_rate_pct":62.0,
                "median_bounce2_vs_bounce1_ratio":0.68,
            },
        },
        "impulse_pullback":{"detected":False},
        "bounce_sequence":{
            "detected":True,
            "completed_bounces":1,
            "next_bounce_number":2,
            "current_leg":"BOUNCING",
            "current_dip_low":8.0,
            "reference_peak":10.0,
            "latest_bounce_pct":10.0,
            "sequence_health_score":60.0,
            "bounce_decay_ratio":0.75,
            "lower_high_streak":1,
        },
        "stair_step":{"detected":False},
        "run_exhaustion":{"score":45.0},
        "liquidity":{"label":"HIGH","avg_dollar_volume":10_000_000},
        "news":[],
    }
    plan=sa.build_trade_plan(metrics,datetime.now(timezone.utc))
    rb=plan.get("repeat_bounce") or {}
    assert rb, plan
    assert int(rb.get("bounce_number") or 0)==2, rb
    assert abs(float(rb.get("dip_low"))-8.0)<1e-9, rb
    assert float(rb.get("confirmation_level"))>8.0, rb
    assert float(rb.get("target1"))>float(rb.get("entry_mid")), rb
    assert float(rb.get("stop"))<float(rb.get("entry_mid")), rb
    assert plan.get("preferred_plan")=="repeat_bounce", plan
    assert plan.get("status")=="ENTRY AVAILABLE", plan
    assert "BOUNCE #2" in str(plan.get("action") or ""), plan


def test_scanner_behavior_completed_bar_parity():
    import scanner_behavior as sb

    start = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(8):
        px = 10.0 + i * 0.01
        bars.append(
            {
                "t": (start + timedelta(minutes=i)).isoformat(),
                "o": px,
                "h": px + 0.02,
                "l": px - 0.02,
                "c": px,
                "v": 100 + i,
                "vw": px,
            }
        )

    at_1407 = sb.resample_to_5min(
        bars,
        as_of=datetime(2026, 8, 28, 14, 7, tzinfo=timezone.utc),
        completed_only=True,
    )
    assert len(at_1407) == 1, at_1407
    assert str(at_1407[-1]["t"]).startswith("2026-08-28T14:00"), at_1407

    at_1410 = sb.resample_to_5min(
        bars,
        as_of=datetime(2026, 8, 28, 14, 10, tzinfo=timezone.utc),
        completed_only=True,
    )
    assert len(at_1410) == 2, at_1410
    assert str(at_1410[-1]["t"]).startswith("2026-08-28T14:05"), at_1410


def test_scanner_behavior_detects_reclaim_acceleration_and_breakout():
    import scanner_behavior as sb

    bars = []
    closes = [10.00, 9.92, 9.84, 9.78, 9.72, 9.88, 10.04, 10.18, 10.26, 10.34]
    volumes = [100, 100, 100, 100, 110, 120, 180, 420, 520, 600]
    for i, (close, volume) in enumerate(zip(closes, volumes)):
        minute = 30 + i * 5
        hour = 9 + minute // 60
        minute = minute % 60
        bars.append({
            "t": f"2026-08-28T{hour + 4:02d}:{minute:02d}:00Z",
            "o": close - 0.03,
            "h": close + 0.08,
            "l": close - 0.08,
            "c": close,
            "v": volume,
            "vw": close,
        })

    features = sb.intraday_behavior_features(
        bars,
        current_price=closes[-1],
    )
    assert features.get("vwap_reclaim") == 1.0, features
    assert features.get("volume_accelerating") == 1.0, features
    assert float(features.get("volume_acceleration_ratio") or 0) > 1.35, features
    assert features.get("breakout_recent") == 1.0, features
    assert features.get("breakout_holding") == 1.0, features
    assert features.get("failed_breakout") == 0.0, features


def test_scanner_behavior_detects_failed_breakout():
    import scanner_behavior as sb

    bars = []
    closes = [10.0, 10.02, 10.01, 10.03, 10.02, 10.04, 10.16, 9.96]
    highs = [10.05, 10.06, 10.05, 10.07, 10.06, 10.08, 10.30, 10.02]
    for i, (close, high) in enumerate(zip(closes, highs)):
        minute = 30 + i * 5
        hour = 14 + minute // 60
        minute = minute % 60
        bars.append({
            "t": f"2026-08-28T{hour:02d}:{minute:02d}:00Z",
            "o": close,
            "h": high,
            "l": close - 0.06,
            "c": close,
            "v": 100 + i * 10,
            "vw": close,
        })

    features = sb.intraday_behavior_features(
        bars,
        current_price=closes[-1],
    )
    assert features.get("breakout_recent") == 1.0, features
    assert features.get("breakout_holding") == 0.0, features
    assert features.get("failed_breakout") == 1.0, features


def test_scanner_behavior_fields_survive_scan_logging():
    import stock_scanner as ss

    candidate = {
        "symbol": "TEST",
        "price": 10.0,
        "vwap_reclaim": 1.0,
        "volume_acceleration_ratio": 1.8,
        "breakout_holding": 1.0,
        "pullback_quality_score": 76.0,
        "bounce_count": 2.0,
        "stair_reaccelerating": 1.0,
    }
    row = ss.candidate_log_record(candidate, 1)
    assert row.get("vwap_reclaim") == 1.0, row
    assert row.get("volume_acceleration_ratio") == 1.8, row
    assert row.get("breakout_holding") == 1.0, row
    assert row.get("pullback_quality_score") == 76.0, row
    assert row.get("bounce_count") == 2.0, row
    assert row.get("stair_reaccelerating") == 1.0, row


def test_prediction_tracker_records_sequence_regime_fields():
    import prediction_tracker as pt

    original_load=pt._load
    original_save=pt._save
    try:
        pt._load=lambda: []
        pt._save=lambda rows, force_remote=False: True
        metrics={
            "symbol":"TEST",
            "feature_version":pt.ANALYZER_FEATURE_VERSION,
            "price":8.10,
            "trade_plan":{
                "status":"ENTRY AVAILABLE",
                "action":"ENTRY AVAILABLE — BOUNCE #2 SCALP CONFIRMED",
                "preferred_plan":"repeat_bounce",
                "confidence":70,
                "selected":{"entry_low":8.08,"entry_high":8.12,"target1":8.4,"stop":7.9},
                "repeat_bounce":{
                    "bounce_number":2,"entry_low":8.08,"entry_high":8.12,
                    "confirmation_level":8.08,"target1":8.4,"target2":8.55,
                    "stop":7.9,"risk_reward":1.5,"expected_bounce_pct":7.0,
                    "historical_bounce_rate_pct":62.0,
                },
            },
            "bounce_sequence":{
                "detected":True,"completed_bounces":1,"next_bounce_number":2,
                "current_leg":"BOUNCING","sequence_state":"MIXED MULTI-BOUNCE STRUCTURE",
                "sequence_health_score":60,"current_dip_low":8.0,"reference_peak":10.0,
            },
            "stair_step":{
                "detected":True,"state":"HIGHER PLATEAU / COILING","step_count":2,
                "structure_score":70,"current_plateau_days":1,
            },
            "ml_prediction":{
                "models":{
                    "repeat_bounce_30":{"probability_pct":64,"validated":True},
                    "post_bounce_failure_60":{"probability_pct":35,"validated":True},
                    "stair_reacceleration_60":{"probability_pct":61,"validated":True},
                },
            },
            "decision_v2":{
                "version":pt.DECISION_SCORE_VERSION,
                "full_spectrum":{
                    "scenarios":{
                        "continuation":{"relative_weight_pct":22},
                        "pullback_bounce":{"relative_weight_pct":30},
                        "stair_reacceleration":{"relative_weight_pct":24},
                        "reversal_failure":{"relative_weight_pct":14},
                        "sideways_chop":{"relative_weight_pct":10},
                    },
                },
            },
        }
        captured=[]
        def _capture(rows,force_remote=False):
            captured.extend(rows)
            return True
        pt._save=_capture
        result=pt.record_prediction(
            metrics,
            now=datetime(2026,8,28,15,0,tzinfo=timezone.utc),
        )
        assert result.get("recorded"), result
        assert captured, result
        row=captured[-1]
        assert row.get("repeat_bounce_plan_available") is True, row
        assert row.get("repeat_bounce_plan_number")==2, row
        assert row.get("repeat_bounce_30_probability_pct")==64.0, row
        assert row.get("scenario_stair_reacceleration_weight")==24.0, row
        assert row.get("stair_step_count")==2, row
    finally:
        pt._load=original_load
        pt._save=original_save


def test_sec_fundamental_snapshot_extracts_comparable_periods():
    import analyzer_v2_integration as v2

    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "val": 100.0,
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "filed": "2025-08-01",
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q2",
                            },
                            {
                                "val": 150.0,
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                            },
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "val": 15.0,
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                            }
                        ]
                    }
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            {
                                "val": 80.0,
                                "end": "2026-06-30",
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                            }
                        ]
                    }
                },
                "LongTermDebt": {
                    "units": {
                        "USD": [
                            {
                                "val": 40.0,
                                "end": "2026-06-30",
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                            }
                        ]
                    }
                },
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            {
                                "val": 120.0,
                                "end": "2026-06-30",
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                            }
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"val": 1000.0, "end": "2025-06-30", "filed": "2025-08-01"},
                            {"val": 1100.0, "end": "2026-06-30", "filed": "2026-08-01"},
                        ]
                    }
                }
            },
        }
    }

    result = v2._fundamental_snapshot(facts)
    assert result["status"] == "ok", result
    assert result["revenue_yoy_pct"] == 50.0, result
    assert result["net_margin_pct"] == 10.0, result
    assert result["cash_to_debt"] == 2.0, result
    assert result["shares_change_yoy_pct"] == 10.0, result


def test_timeframe_analysis_caps_long_term_when_fundamentals_are_sparse():
    import analyzer_v2_integration as v2

    original = v2._daily_trend_context
    try:
        v2._daily_trend_context = lambda sa, symbol, metrics: {
            "status": "ok",
            "trend_score": 90.0,
            "return_20d_pct": 20.0,
            "return_60d_pct": 40.0,
            "return_120d_pct": 60.0,
        }
        metrics = {
            "price": 10.0,
            "day_pct": 0.0,
            "volume_pace": 1.0,
            "momentum_5m": 0.0,
            "momentum_15m": 0.0,
            "vwap_position": "ABOVE",
            "liquidity": {"label": "HIGH"},
            "historical_setup": {"status": "insufficient_history"},
            "stair_step": {"structure_score": 50.0},
        }
        result = v2._timeframe_analysis(
            object(),
            "TEST",
            metrics,
            {"status": "ok", "dilution_risk": "NONE FOUND", "fundamentals": {"coverage_count": 0}},
            {"label": "MIXED"},
            {"score": 0.0},
            50.0,
            50.0,
        )
        assert result["scores"]["long_term"] <= 57.0, result
        assert result["fundamental_coverage_count"] == 0, result
        assert "fundamental coverage is limited" in " ".join(result["long_term_reasons"]), result
    finally:
        v2._daily_trend_context = original



def test_prediction_tracker_records_timeframe_scores():
    import prediction_tracker as pt

    original_load = pt._load
    original_save = pt._save
    captured = []
    try:
        pt._load = lambda: []

        def _capture(rows, force_remote=False):
            captured.extend(rows)
            return True

        pt._save = _capture
        metrics = {
            "symbol": "TEST",
            "feature_version": pt.ANALYZER_FEATURE_VERSION,
            "price": 10.0,
            "trade_plan": {"selected": {}},
            "decision_v2": {
                "version": pt.DECISION_SCORE_VERSION,
                "timeframe_analysis": {
                    "version": pt.TIMEFRAME_SCORE_VERSION,
                    "best_fit": "SWING",
                    "scores": {
                        "intraday": 58.0,
                        "swing": 74.0,
                        "long_term": 62.0,
                    },
                    "fundamental_quality_score": 66.0,
                    "fundamental_coverage_count": 5,
                    "daily_trend": {
                        "trend_score": 71.0,
                        "return_20d_pct": 12.0,
                        "return_60d_pct": 25.0,
                        "return_120d_pct": 40.0,
                    },
                },
            },
        }
        result = pt.record_prediction(
            metrics,
            now=datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc),
        )
        assert result.get("recorded"), result
        row = captured[-1]
        assert row.get("timeframe_score_version") == pt.TIMEFRAME_SCORE_VERSION, row
        assert row.get("timeframe_best_fit") == "SWING", row
        assert row.get("timeframe_swing_score") == 74.0, row
        assert row.get("timeframe_fundamental_coverage_count") == 5, row
        assert row.get("timeframe_trend_score") == 71.0, row
    finally:
        pt._load = original_load
        pt._save = original_save


def test_timeframe_trading_day_outcomes_skip_weekends():
    import prediction_tracker as pt

    row = {
        "timestamp": "2026-08-28T15:00:00+00:00",
        "price": 10.0,
        "outcomes": {},
    }
    bars = [
        {"t": "2026-08-31T04:00:00Z", "c": 10.5},
        {"t": "2026-09-01T04:00:00Z", "c": 10.8},
        {"t": "2026-09-02T04:00:00Z", "c": 11.0},
        {"t": "2026-09-03T04:00:00Z", "c": 10.7},
        {"t": "2026-09-04T04:00:00Z", "c": 11.5},
    ]
    changed = pt._resolve_trading_day_returns(row, bars)
    assert changed is True, row
    outcomes = row["outcomes"]
    assert outcomes.get("return_1d_pct") == 5.0, outcomes
    assert outcomes.get("return_3d_pct") == 10.0, outcomes
    assert outcomes.get("return_5d_pct") == 15.0, outcomes
    assert outcomes.get("return_20d_pct") is None, outcomes


def test_timeframe_calibration_uses_matched_horizons():
    import score_analyzer_outcomes as sao

    rows = [
        {
            "timeframe_score_version": sao.TIMEFRAME_SCORE_VERSION,
            "timeframe_best_fit": "SWING",
            "timeframe_swing_score": 80.0,
            "outcomes": {"return_5d_pct": 8.0},
        },
        {
            "timeframe_score_version": sao.TIMEFRAME_SCORE_VERSION,
            "timeframe_best_fit": "SWING",
            "timeframe_swing_score": 82.0,
            "outcomes": {"return_5d_pct": -2.0},
        },
        {
            "timeframe_score_version": sao.TIMEFRAME_SCORE_VERSION,
            "timeframe_best_fit": "LONGER-TERM",
            "timeframe_long_term_score": 70.0,
            "outcomes": {"return_20d_pct": 12.0},
        },
    ]
    swing = sao._timeframe_calibrate(
        rows, "timeframe_swing_score", "return_5d_pct"
    )
    assert swing["80-100"]["n"] == 2, swing
    assert swing["80-100"]["higher_rate"] == 50.0, swing
    best = sao._timeframe_best_fit_calibration(rows)
    assert best["SWING"]["resolved"] == 2, best
    assert best["SWING"]["horizon"] == "5 trading days", best
    assert best["LONGER-TERM"]["resolved"] == 1, best



def test_point_in_time_fundamentals_exclude_future_filings():
    import analyzer_v2_integration as v2

    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "val": 100.0,
                                "start": "2024-01-01",
                                "end": "2024-06-30",
                                "filed": "2024-08-01",
                                "form": "10-Q",
                                "fy": 2024,
                                "fp": "Q2",
                            },
                            {
                                "val": 120.0,
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "filed": "2025-08-01",
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q2",
                            },
                            {
                                "val": 999.0,
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                            },
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "val": 12.0,
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "filed": "2025-08-01",
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q2",
                            },
                            {
                                "val": 500.0,
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q2",
                            },
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"val": 1000.0, "end": "2024-06-30", "filed": "2024-08-01"},
                            {"val": 1100.0, "end": "2025-06-30", "filed": "2025-08-01"},
                            {"val": 9000.0, "end": "2026-06-30", "filed": "2026-08-01"},
                        ]
                    }
                }
            },
        }
    }

    result = v2._fundamental_snapshot(facts, as_of="2025-12-31")
    assert result["revenue_latest"] == 120.0, result
    assert result["revenue_yoy_pct"] == 20.0, result
    assert result["net_income_latest"] == 12.0, result
    assert result["shares_change_yoy_pct"] == 10.0, result


def test_shared_timeframe_horizon_weights_match_live_formula():
    import analyzer_v2_integration as v2

    swing, long_term = v2._timeframe_horizon_scores(
        trend_score=70.0,
        stair_score=60.0,
        history_score=55.0,
        catalyst_score=50.0,
        market_score=52.0,
        fundamental_score=65.0,
        fundamental_coverage=5,
    )
    expected_swing = round(
        70.0 * 0.34
        + 60.0 * 0.22
        + 55.0 * 0.16
        + 50.0 * 0.12
        + 52.0 * 0.08
        + 65.0 * 0.08,
        1,
    )
    expected_long = round(
        65.0 * 0.58
        + 70.0 * 0.30
        + 50.0 * 0.07
        + 52.0 * 0.05,
        1,
    )
    assert swing == expected_swing, (swing, expected_swing)
    assert long_term == expected_long, (long_term, expected_long)

    _swing, capped = v2._timeframe_horizon_scores(
        95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 0
    )
    assert capped == 57.0, capped



def test_swing_timeframe_ml_features_ignore_future_outcome_fields():
    import timeframe_ml_ranker as tml

    base = {
        "day_pct": 8.0,
        "gap_pct": 2.0,
        "relative_volume": 3.0,
        "current_dollar_volume": 2_000_000,
        "trend_score": 70.0,
        "stair_score": 65.0,
        "history_score": 60.0,
        "market_score": 55.0,
        "trend_context": {
            "return_5d_pct": 4.0,
            "return_20d_pct": 12.0,
            "return_60d_pct": 25.0,
            "return_120d_pct": 40.0,
            "from_52w_high_pct": -8.0,
        },
        "stair_context": {"stair_step_count": 2, "stair_structure_score": 65.0},
        "historical_context": {"bias_score": 4.0, "next_day_up_pct": 58.0, "sample_count": 20},
        "market_context": {"broad_market_avg_pct": 0.4},
        "outcomes": {
            "return_5d_pct": 99.0,
            "swing_target_before_stop_5d": 1,
            "swing_mfe_5d_pct": 12.0,
            "swing_mae_5d_pct": -2.0,
        },
        "swing_score": 99.0,
    }
    changed = dict(base)
    changed["outcomes"] = {
        "return_5d_pct": -99.0,
        "swing_target_before_stop_5d": 0,
        "swing_mfe_5d_pct": 1.0,
        "swing_mae_5d_pct": -20.0,
    }
    changed["swing_score"] = 1.0

    assert tml._feature_dict(base) == tml._feature_dict(changed)


def test_swing_timeframe_ml_folds_never_mix_same_replay_date():
    import timeframe_ml_ranker as tml

    rows = []
    for day in range(1, 49):
        date_text = f"2026-01-{day:02d}" if day <= 31 else f"2026-02-{day-31:02d}"
        for symbol_index in range(20):
            rows.append(
                {
                    "date": date_text,
                    "symbol": f"T{symbol_index}",
                    "return_5d_pct": 1.0 if symbol_index % 2 else -1.0,
                    "label": int(symbol_index % 2),
                    "swing_score": 50.0,
                    "features": {name: 0.0 for name in tml.FEATURES},
                }
            )

    folds = tml._chronological_folds(rows)
    assert len(folds) >= 3, len(folds)
    for train, test, train_dates, test_dates in folds:
        assert set(train_dates).isdisjoint(set(test_dates))
        assert max(train_dates) < min(test_dates)
        assert set(row["date"] for row in train).isdisjoint(
            set(row["date"] for row in test)
        )


def test_swing_path_target_orders_daily_events_conservatively():
    import historical_timeframe_replay as htr

    entry = ("2026-01-02", {"c": 100.0, "h": 101.0, "l": 99.0})
    target_first = [
        entry,
        ("2026-01-05", {"h": 106.0, "l": 98.0, "c": 105.0}),
        ("2026-01-06", {"h": 107.0, "l": 95.0, "c": 96.0}),
        ("2026-01-07", {"h": 101.0, "l": 97.0, "c": 100.0}),
        ("2026-01-08", {"h": 102.0, "l": 98.0, "c": 101.0}),
        ("2026-01-09", {"h": 103.0, "l": 99.0, "c": 102.0}),
    ]
    result = htr._swing_path_outcomes(target_first, 0, 100.0)
    assert result["swing_target_before_stop_5d"] == 1, result
    assert result["swing_first_event_5d"] == "TARGET", result
    assert result["swing_first_hit_session"] == 1, result
    assert result["swing_mfe_5d_pct"] == 7.0, result
    assert result["swing_mae_5d_pct"] == -5.0, result

    stop_first = [
        entry,
        ("2026-01-05", {"h": 103.0, "l": 95.0, "c": 96.0}),
        ("2026-01-06", {"h": 108.0, "l": 97.0, "c": 107.0}),
        ("2026-01-07", {"h": 106.0, "l": 99.0, "c": 105.0}),
        ("2026-01-08", {"h": 104.0, "l": 98.0, "c": 103.0}),
        ("2026-01-09", {"h": 105.0, "l": 99.0, "c": 104.0}),
    ]
    result = htr._swing_path_outcomes(stop_first, 0, 100.0)
    assert result["swing_target_before_stop_5d"] == 0, result
    assert result["swing_first_event_5d"] == "STOP", result


def test_swing_path_target_excludes_same_day_order_ambiguity():
    import historical_timeframe_replay as htr

    rows = [
        ("2026-01-02", {"c": 100.0}),
        ("2026-01-05", {"h": 106.0, "l": 95.0, "c": 101.0}),
        ("2026-01-06", {"h": 103.0, "l": 98.0, "c": 102.0}),
        ("2026-01-07", {"h": 102.0, "l": 99.0, "c": 101.0}),
        ("2026-01-08", {"h": 102.0, "l": 99.0, "c": 101.0}),
        ("2026-01-09", {"h": 102.0, "l": 99.0, "c": 101.0}),
    ]
    result = htr._swing_path_outcomes(rows, 0, 100.0)
    assert result["swing_target_before_stop_5d"] is None, result
    assert result["swing_first_event_5d"] == "AMBIGUOUS_SAME_DAY", result
    assert result["swing_ambiguous_same_day_5d"] is True, result


def test_swing_path_target_treats_no_target_as_non_success():
    import historical_timeframe_replay as htr

    rows = [
        ("2026-01-02", {"c": 100.0}),
        ("2026-01-05", {"h": 103.0, "l": 98.0, "c": 101.0}),
        ("2026-01-06", {"h": 104.0, "l": 97.0, "c": 103.0}),
        ("2026-01-07", {"h": 104.5, "l": 97.5, "c": 104.0}),
        ("2026-01-08", {"h": 104.0, "l": 98.0, "c": 102.0}),
        ("2026-01-09", {"h": 103.0, "l": 98.0, "c": 102.0}),
    ]
    result = htr._swing_path_outcomes(rows, 0, 100.0)
    assert result["swing_target_before_stop_5d"] == 0, result
    assert result["swing_first_event_5d"] == "NEITHER", result
    assert result["swing_mfe_5d_pct"] == 4.5, result


def test_market_regime_context_ignores_future_benchmark_bars():
    import historical_timeframe_replay as htr
    from datetime import date, timedelta

    start = date(2025, 1, 1)
    replay_index = 219
    replay_day = start + timedelta(days=replay_index)

    def series(multiplier):
        rows = []
        for i in range(230):
            day = start + timedelta(days=i)
            close = 100.0 + multiplier * i * 0.08
            rows.append(
                (
                    day,
                    {
                        "o": close - 0.2,
                        "h": close + 0.5,
                        "l": close - 0.5,
                        "c": close,
                        "v": 1_000_000,
                    },
                )
            )
        return rows

    baseline = {
        "SPY": series(1.0),
        "QQQ": series(1.2),
        "IWM": series(0.8),
    }
    changed_future = {
        symbol: [(day, dict(bar)) for day, bar in rows]
        for symbol, rows in baseline.items()
    }
    for rows in changed_future.values():
        for i in range(replay_index + 1, len(rows)):
            day, bar = rows[i]
            bar["c"] *= 5.0
            bar["h"] *= 5.0
            bar["l"] *= 5.0
            rows[i] = (day, bar)

    before = htr._market_context(baseline, replay_day)
    after = htr._market_context(changed_future, replay_day)
    assert before == after, (before, after)
    assert before.get("regime_label") in {
        "RISK_ON",
        "RISK_OFF",
        "VOLATILE",
        "MIXED",
    }, before
    assert before.get("spy_return_20d_pct") is not None, before
    assert before.get("spy_realized_vol_20d_pct") is not None, before


def test_swing_ml_regime_features_are_separate_from_baseline():
    import timeframe_ml_ranker as tml

    assert set(tml.BASE_FEATURES).isdisjoint(set(tml.REGIME_FEATURES))
    assert tml.FEATURES == tml.BASE_FEATURES + tml.REGIME_FEATURES
    for required in (
        "spy_return_20d_pct",
        "spy_realized_vol_20d_pct",
        "iwm_minus_spy_20d_pct",
        "benchmark_positive_20d_frac",
    ):
        assert required in tml.REGIME_FEATURES, required


def test_multiyear_swing_ml_uses_more_walk_forward_eras():
    import timeframe_ml_ranker as tml

    rows = []
    for day_index in range(150):
        year = 2021 + day_index // 30
        month = 1 + (day_index % 30) // 3
        day = 1 + (day_index % 3)
        date_text = f"{year:04d}-{month:02d}-{day:02d}"
        for symbol_index in range(20):
            rows.append(
                {
                    "date": date_text,
                    "symbol": f"M{symbol_index}",
                    "return_5d_pct": 1.0 if symbol_index % 2 else -1.0,
                    "label": int(symbol_index % 2),
                    "swing_score": 50.0,
                    "features": {name: 0.0 for name in tml.FEATURES},
                }
            )

    folds = tml._chronological_folds(rows)
    assert len(folds) >= 5, len(folds)
    for train, test, train_dates, test_dates in folds:
        assert max(train_dates) < min(test_dates)
        assert len(train) >= 500
        assert len(test) >= 150


def test_multiyear_replay_reports_calendar_year_results():
    import historical_timeframe_replay as htr

    rows = [
        {
            "as_of": "2022-06-01T16:00:00-04:00",
            "market_context": {"regime_label": "RISK_OFF"},
            "outcomes": {
                "swing_target_before_stop_5d": 1,
                "swing_mfe_5d_pct": 7.0,
                "swing_mae_5d_pct": -2.0,
                "excess_return_vs_spy_5d_pct": 3.0,
            },
        },
        {
            "as_of": "2022-09-01T16:00:00-04:00",
            "market_context": {"regime_label": "MIXED"},
            "outcomes": {
                "swing_target_before_stop_5d": 0,
                "swing_mfe_5d_pct": 2.0,
                "swing_mae_5d_pct": -5.0,
                "excess_return_vs_spy_5d_pct": -1.0,
            },
        },
        {
            "as_of": "2023-03-01T16:00:00-04:00",
            "market_context": {"regime_label": "RISK_ON"},
            "outcomes": {
                "swing_target_before_stop_5d": 1,
                "swing_mfe_5d_pct": 8.0,
                "swing_mae_5d_pct": -1.0,
                "excess_return_vs_spy_5d_pct": 2.0,
            },
        },
    ]
    summary = htr._year_outcome_summary(rows)
    assert summary["2022"]["n"] == 2, summary
    assert summary["2022"]["target_before_stop_rate_pct"] == 50.0, summary
    assert summary["2022"]["regime_counts"]["RISK_OFF"] == 1, summary
    assert summary["2023"]["target_before_stop_rate_pct"] == 100.0, summary


if __name__ == "__main__":
    tests = [
        test_analyzer_prefers_tradier,
        test_analyzer_falls_back_cleanly,
        test_scanner_ml_version_gate,
        test_analyzer_calibration_version_gate,
        test_scanner_outcome_metadata,
        test_historical_trade_quality_path_is_conservative,
        test_scanner_trade_quality_path_is_causal_and_conservative,
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
        test_analyzer_ui_preserves_historical_context_dependencies,
        test_analyzer_shared_button_styles_live_in_bootstrap,
        test_scanner_aligned_volume_pace_matches_analyzer_baseline,
        test_scanner_action_avoids_chasing_extreme_mover,
        test_scanner_action_analyze_now_requires_aligned_conditions,
        test_scanner_action_breakout_watch_near_high,
        test_scanner_action_reject_stays_no_trade,
        test_scanner_action_failed_breakout_forces_wait,
        test_scanner_action_production_default_ignores_unvalidated_behavior,
        test_scanner_action_legacy_mode_ignores_behavior_state,
        test_scanner_action_b_grade_vwap_reclaim_stays_bounce_watch,
        test_scanner_action_a_grade_vwap_reclaim_can_be_analyze_now,
        test_scanner_action_active_pullback_waits_for_confirmation,
        test_scanner_action_behavior_never_overrides_reject,
        test_scanner_ui_auto_surfaces_validated_ml,
        test_historical_replay_universe_uses_prior_days_only,
        test_historical_replay_source_survives_ml_extraction,
        test_replay_requires_live_confirmation_before_full_badge,
        test_analyzer_ml_validation_requires_probability_skill,
        test_impulse_detector_measures_fraction_of_run,
        test_entry_readiness_penalizes_unconfirmed_shallow_retrace,
        test_run_exhaustion_flags_rejected_mature_run,
        test_full_spectrum_exposes_all_scenarios,
        test_multi_bounce_detector_tracks_decay_and_lower_highs,
        test_multi_bounce_full_spectrum_accepts_sequence_state,
        test_stair_step_detector_finds_higher_plateau_sequence,
        test_scanner_behavior_completed_bar_parity,
        test_scanner_behavior_detects_reclaim_acceleration_and_breakout,
        test_scanner_behavior_detects_failed_breakout,
        test_scanner_behavior_fields_survive_scan_logging,
        test_dedicated_repeat_bounce_trade_plan_uses_latest_dip,
        test_prediction_tracker_records_sequence_regime_fields,
        test_sec_fundamental_snapshot_extracts_comparable_periods,
        test_timeframe_analysis_caps_long_term_when_fundamentals_are_sparse,
        test_prediction_tracker_records_timeframe_scores,
        test_timeframe_trading_day_outcomes_skip_weekends,
        test_timeframe_calibration_uses_matched_horizons,
        test_point_in_time_fundamentals_exclude_future_filings,
        test_shared_timeframe_horizon_weights_match_live_formula,
        test_swing_timeframe_ml_features_ignore_future_outcome_fields,
        test_swing_timeframe_ml_folds_never_mix_same_replay_date,
        test_swing_path_target_orders_daily_events_conservatively,
        test_swing_path_target_excludes_same_day_order_ambiguity,
        test_swing_path_target_treats_no_target_as_non_success,
        test_market_regime_context_ignores_future_benchmark_bars,
        test_swing_ml_regime_features_are_separate_from_baseline,
        test_multiyear_swing_ml_uses_more_walk_forward_eras,
        test_multiyear_replay_reports_calendar_year_results,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL CONSISTENCY REGRESSION CHECKS PASSED")
