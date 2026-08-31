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
    assert str(result["volume_source"]).startswith("TRADIER"), result
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
        observation_source="live_scan",
        market_provider="tradier",
        live_feed="consolidated",
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


def test_scanner_outcome_horizon_rejects_late_gap_bars():
    import score_outcomes as so

    target = datetime(2026, 8, 27, 10, 15, tzinfo=ET)
    close = datetime(2026, 8, 27, 16, 0, tzinfo=ET)
    accepted = so.index_bars({
        "TEST": [{"t": "2026-08-27T14:17:30Z", "c": 10.2}]
    })["TEST"]
    price, ts = so.price_at_or_after(accepted, target, close)
    assert price == 10.2 and ts is not None

    too_late = so.index_bars({
        "TEST": [{"t": "2026-08-27T14:18:01Z", "c": 11.0}]
    })["TEST"]
    price, ts = so.price_at_or_after(too_late, target, close)
    assert price is None and ts is None


def test_scanner_outcomes_expose_deduplicated_actionable_events():
    import score_outcomes as so

    rows = []
    for hour, minute in ((10, 0), (10, 10), (11, 5)):
        rows.append({
            "symbol": "AAA",
            "scan_time_et": datetime(2026, 8, 27, hour, minute, tzinfo=ET).isoformat(),
            "setup_grade": "A",
            "scanner_action": "ANALYZE NOW",
            "passed_base_filters": True,
            "alert_ready": True,
            "return_15m_pct": 1.0,
            "return_30m_pct": 1.0,
            "return_60m_pct": 1.0,
        })
    events = so.deduplicate_actionable_events(rows, cooldown_minutes=60)
    assert len(events) == 2, events
    summary = so.summarize(rows)
    assert summary["observation_count"] == 3, summary
    assert summary["deduplicated_actionable_events"]["event_count"] == 2, summary


def test_scanner_historical_returns_are_causal_and_timestamp_matched():
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
    import stock_scanner as ss

    day = datetime(2026, 8, 27, 0, 0, tzinfo=ET)
    bars = [
        (day.replace(hour=9, minute=45), {"c": 10.0}),
        (day.replace(hour=10, minute=0), {"c": 10.5}),
        (day.replace(hour=10, minute=15), {"c": 10.7}),
    ]
    idx = ss.completed_bar_index(bars, 10 * 60 + 7)
    assert idx == 0, idx
    ret = ss.timestamp_forward_return(bars, idx, 15)
    assert ret is not None and ret > 0, ret

    gap_bars = [
        (day.replace(hour=9, minute=45), {"c": 10.0}),
        (day.replace(hour=11, minute=0), {"c": 12.0}),
    ]
    assert ss.timestamp_forward_return(gap_bars, 0, 15) is None


def test_scanner_enrichment_pool_is_not_display_watchlist_truncated():
    os.environ.setdefault("ALPACA_API_KEY", "test-key")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
    import stock_scanner as ss

    rows = [
        {"symbol": f"T{i:02d}", "critical_fail_count": 0, "failed_count": 0}
        for i in range(50)
    ]
    selected = ss.select_enrichment_targets(rows, "regular")
    assert len(selected) == ss.REGULAR_ENRICH_POOL_MAX == 40, len(selected)
    assert selected[-1]["symbol"] == "T39", selected[-1]
    assert ss.NEWS_TOP == ss.REGULAR_ENRICH_POOL_MAX


def test_scanner_snapshot_preserves_action_data_integrity_for_watch_alerts():
    from pathlib import Path

    source = Path("stock_scanner.py").read_text(encoding="utf-8")
    assert '"action_data_integrity_ok": bool(c.get("action_data_integrity_ok"))' in source
    assert '"action_data_integrity_reasons": c.get("action_data_integrity_reasons") or []' in source


def test_scanner_latest_snapshot_write_is_atomic():
    from pathlib import Path

    source = Path("stock_scanner.py").read_text(encoding="utf-8")
    assert 'tmp_path = out_dir / f".latest_scan_{scan_id}.tmp"' in source
    assert "os.replace(tmp_path, latest_path)" in source
    assert 'latest_path.write_text(json.dumps(payload' not in source


def test_scanner_and_analyzer_use_midpoint_spread_formula():
    from pathlib import Path

    scanner_source = Path("stock_scanner.py").read_text(encoding="utf-8")
    analyzer_source = Path("stock_analyzer.py").read_text(encoding="utf-8")
    assert "midpoint = (bid + ask) / 2.0" in scanner_source
    assert "midpoint=(ask+bid)/2.0" in analyzer_source
    assert "spread_pct=spread_pct/(1+spread_pct/100)" not in analyzer_source


def test_offhours_outcomes_include_two_day_horizon():
    import offhours_outcome_tracker as tracker

    assert tracker.HORIZONS == (1, 2, 3, 5, 10, 20, 40), tracker.HORIZONS



def test_offhours_score_is_labeled_trend_candidate_score():
    from pathlib import Path

    app_source = Path("app.py").read_text(encoding="utf-8")
    scanner_source = Path("scanner_app.py").read_text(encoding="utf-8")
    assert '"Trend Candidate Score"' in app_source
    assert '"Trend Candidate Score": row.get("daily_discovery_score")' in scanner_source
    assert "A high Trend Candidate Score means" in scanner_source



def test_uncapped_trend_candidate_score_is_preserved_in_outcome_cohort():
    import offhours_outcome_tracker as tracker

    seed = tracker._candidate_seed({
        "symbol": "TEST",
        "price": 10.0,
        "daily_discovery_score": 100.0,
        "trend_candidate_raw_score": 103.4,
        "trend_candidate_score_version": "trend-candidate-score-v1",
    })
    assert seed["daily_discovery_score"] == 100.0, seed
    assert seed["trend_candidate_raw_score"] == 103.4, seed
    assert seed["trend_candidate_score_version"] == "trend-candidate-score-v1", seed


def test_combined_candidate_list_uses_shared_trade_horizon_filter():
    from pathlib import Path

    app_source = Path("app.py").read_text(encoding="utf-8")
    assert 'st.session_state["scanner_trade_horizon"] = "ALL"' in app_source
    assert "def _trade_horizon_matches(row, selected):" in app_source
    assert 'trade_horizon = st.session_state.get("scanner_trade_horizon", "ALL")' in app_source
    assert "_trade_horizon_matches(row, trade_horizon)" in app_source



def test_mixed_timeframe_is_labeled_multiple_timeframes():
    from pathlib import Path

    scanner_source = Path("scanner_app.py").read_text(encoding="utf-8")
    app_source = Path("app.py").read_text(encoding="utf-8")
    assert '"MIXED": "Multiple Timeframes"' in scanner_source
    assert '"MULTIPLE TIMEFRAMES" if fit == "MIXED" else fit' in scanner_source
    assert '"MULTIPLE TIMEFRAMES" if fit == "MIXED" else fit' in app_source
    assert "<b>Multiple Timeframes</b> means two horizons scored similarly." in scanner_source



def test_glass_theme_styles_selectboxes_and_trade_horizon():
    from pathlib import Path

    source = Path("glass_theme.py").read_text(encoding="utf-8")
    scanner_source = Path("scanner_app.py").read_text(encoding="utf-8")
    assert 'div[data-testid="stSelectbox"] div[data-baseweb="select"] > div' in source
    assert 'div[role="listbox"]' in source
    assert 'ul[data-testid="stSelectboxVirtualDropdown"]' in source
    assert 'input[role="combobox"]' in source
    assert 'div[role="option"][aria-selected="true"]' in source
    assert 'li[role="option"][aria-selected="true"]' in source
    assert '.st-key-scanner_trade_horizon' in source
    assert '"Trade Horizon Focus"' in scanner_source
    assert '[1.10, 1.35, 2.65, 1.40]' in scanner_source



def test_streamlit_version_is_pinned_for_ui_stability():
    from pathlib import Path

    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert "streamlit==1.62.0" in requirements.splitlines(), requirements


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


def test_analyzer_white_tables_are_collapsible():
    from pathlib import Path

    core = Path("analyzer_ui_core.py").read_text(encoding="utf-8")
    historical = Path("historical_ui.py").read_text(encoding="utf-8")

    required_core = [
        'with st.expander("Momentum & liquidity", expanded=False):',
        'with st.expander("Support & resistance levels", expanded=False):',
        'with st.expander("Historical spike table", expanded=False):',
        'with st.expander("Live plan inputs + research context", expanded=False):',
    ]
    missing_core = [item for item in required_core if item not in core]
    assert not missing_core, f"Missing Analyzer collapsible tables: {missing_core}"
    assert (
        'with st.expander("Historical setup match table", expanded=False):'
        in historical
    ), "Historical setup match table is not collapsible"


def test_analyzer_page_leads_with_actionable_decision_hierarchy():
    from pathlib import Path

    core = Path("analyzer_ui_core.py").read_text(encoding="utf-8")
    historical = Path("historical_ui.py").read_text(encoding="utf-8")

    decision = core.find("Decision first")
    trade_plan = core.find("SUGGESTED TRADE PLAN")
    snapshot = core.find("Live market snapshot")
    v2 = core.find('render_v2_decision(st, r)')
    impulse = core.find("Impulse / pullback structure")

    assert decision >= 0, "Decision-first section is missing"
    assert trade_plan > decision, "Trade plan must follow the decision-first strip"
    assert snapshot > trade_plan, "Supporting market snapshot should follow the trade plan"
    assert v2 > snapshot, "Decision-v2 explanations should follow the primary plan"
    assert impulse > v2, "Pattern engines should remain below decision-level information"

    assert "Priority order: trust the data" in core
    assert "Research-only context; these completed historical matches are not included" in historical
    assert "Live 33–50% impulse zone" in core


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
                "liquidity_source": "historical_tradier_replay",
                "live_intraday_source": "tradier_historical_5min_open",
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


def test_analyzer_ml_walk_forward_never_splits_one_trading_day():
    import ml_predictor as ml

    rows = []
    for day_index in range(12):
        day = f"2026-08-{day_index + 10:02d}"
        for sample_index in range(8):
            rows.append(
                {
                    "trading_date": day,
                    "timestamp": float(day_index * 100 + sample_index),
                }
            )

    folds = ml._walk_forward_day_splits(rows)
    assert folds, folds
    for train_idx, val_idx, train_cut, val_cut in folds:
        train_days = {rows[i]["trading_date"] for i in train_idx}
        val_days = {rows[i]["trading_date"] for i in val_idx}
        assert train_days.isdisjoint(val_days), (train_days, val_days)
        assert max(train_days) == train_cut
        assert max(train_days) < min(val_days)
        assert max(val_days) == val_cut


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


def test_multi_bounce_ignores_micro_wiggles_and_waits_for_distinct_second_swing():
    from multi_bounce import detect_bounce_sequence
    from datetime import datetime, timedelta, timezone

    start=datetime(2026,8,31,11,0,tzinfo=timezone.utc)
    bars=[]

    # Dominant impulse into 07:15 PT (14:15 UTC): roughly $0.36 -> $0.95.
    for i in range(16):
        close=0.36 + (0.59 * i / 15.0)
        bars.append({
            "t":(start+timedelta(minutes=i)).isoformat(),
            "o":close-0.005,
            "h":close+0.008,
            "l":close-0.008,
            "c":close,
            "v":1000+i*150,
        })

    def add(minute, o, h, l, close, v=5000):
        bars.append({
            "t":datetime(2026,8,31,14,minute,tzinfo=timezone.utc).isoformat(),
            "o":o,"h":h,"l":l,"c":close,"v":v,
        })

    # First real pullback/bounce: low ~07:17 PT, peak ~07:19 PT.
    add(16,0.93,0.94,0.87,0.89)
    add(17,0.89,0.90,0.82,0.84)
    add(18,0.84,0.87,0.83,0.86)
    add(19,0.86,0.90,0.85,0.89)
    add(20,0.89,0.90,0.83,0.84)  # confirms bounce #1

    # Micro wiggle around 07:21-07:23. Old price-only zig-zag logic could
    # incorrectly call the 07:22 high a new bounce.
    add(21,0.84,0.85,0.82,0.83)
    add(22,0.83,0.88,0.82,0.87)
    add(23,0.87,0.87,0.81,0.82)

    # The larger pullback continues; true second swing low is 07:29 PT.
    add(24,0.82,0.83,0.80,0.81)
    add(25,0.81,0.82,0.79,0.80)
    add(26,0.80,0.81,0.78,0.79)
    add(27,0.79,0.80,0.77,0.78)
    add(28,0.78,0.79,0.76,0.77)
    add(29,0.77,0.78,0.74,0.75)

    # True second rebound builds for several minutes and peaks 07:33 PT.
    add(30,0.75,0.79,0.74,0.78)
    add(31,0.78,0.82,0.77,0.81)
    add(32,0.81,0.84,0.80,0.83)
    add(33,0.83,0.85,0.82,0.84)
    add(34,0.84,0.84,0.77,0.78)  # confirms bounce #2

    seq=detect_bounce_sequence(bars,current_price=0.78,atr_pct=30)
    assert seq.get("detected"), seq
    completed=seq.get("bounces") or []
    assert len(completed) == 2, seq
    assert completed[0].get("bounce_peak_time","").startswith("2026-08-31T14:19"), completed
    assert completed[1].get("pullback_low_time","").startswith("2026-08-31T14:29"), completed
    assert completed[1].get("bounce_peak_time","").startswith("2026-08-31T14:33"), completed
    assert all(
        not str(row.get("bounce_peak_time") or "").startswith("2026-08-31T14:22")
        for row in completed
    ), completed
    assert float(seq.get("min_cycle_minutes") or 0) >= 5.0, seq
    assert float(seq.get("min_recovery_fraction") or 0) >= 0.35, seq


def test_distinct_bounce_semantics_are_version_isolated_for_peer_ml():
    import peer_ml_predictor as peer
    import scanner_behavior as behavior

    assert behavior.BEHAVIOR_FEATURE_VERSION == "scanner-behavior-v3-distinct-swings"
    assert peer.PEER_MODEL_VERSION == "analyzer-peer-v5-distinct-swing-bounces"

    rows=[
        {"symbol":"OLD","behavior_feature_version":"scanner-behavior-v2-completed-bars"},
        {"symbol":"NEW","behavior_feature_version":behavior.BEHAVIOR_FEATURE_VERSION},
        {"symbol":"MISSING"},
    ]
    kept=peer._matching_behavior_rows(rows)
    assert [row.get("symbol") for row in kept] == ["NEW"], kept


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


def test_low_rr_repeat_bounce_cannot_replace_primary_plan():
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
        "score":88.0,
        "atr_14":0.81,
        "atr_14_pct":10.0,
        "supports":[{"price":7.85,"quality_score":70,"quality":"STRONG","side":"support"}],
        "resistances":[{"price":10.0,"quality_score":70,"quality":"STRONG","side":"resistance"}],
        "historical_analogs":{"status":"insufficient_history","samples":[]},
        "historical_setup":{"status":"ok","sample_count":20,"intraday":{}},
        "impulse_pullback":{"detected":False},
        "bounce_sequence":{
            "detected":True,
            "completed_bounces":1,
            "next_bounce_number":2,
            "current_leg":"BOUNCING",
            "current_dip_low":8.0,
            "reference_peak":10.0,
            "latest_bounce_pct":2.0,
            "sequence_health_score":60.0,
            "bounce_decay_ratio":0.40,
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
    assert float(rb.get("risk_reward") or 0) < 1.25, rb
    assert plan.get("preferred_plan") != "repeat_bounce", plan
    assert plan.get("selected_plan_role") == "primary", plan
    assert "WATCH ONLY" in str(plan.get("repeat_bounce_status") or ""), plan
    assert "secondary" in str(plan.get("plan_selection_note") or "").lower(), plan


def test_analyzer_bounce_progress_and_plan_change_are_explicit():
    from pathlib import Path

    source=Path("analyzer_ui_core.py").read_text(encoding="utf-8")
    assert 'f"BOUNCE #{_idx}"' in source
    assert '"✓ CONFIRMED"' in source
    assert '"… DEVELOPING"' in source
    assert '"○ FORMING"' in source
    assert "PLAN CHANGED:" in source
    assert "PRIMARY PLAN ·" in source
    assert "ACTIVE ALTERNATIVE · BOUNCE #" in source


def test_analyzer_exposes_same_evidence_bars_for_visual_snapshots():
    from pathlib import Path

    source=Path("stock_analyzer.py").read_text(encoding="utf-8")
    assert '"chart_data":{' in source
    assert '"intraday":_chart_bars(intraday,420)' in source
    assert '"daily":_chart_bars(' in source
    assert "list(daily or []) +" in source


def test_analyzer_visual_specs_show_real_pattern_markers():
    import analyzer_visuals as av

    intraday=[]
    for i, price in enumerate((10.0, 11.0, 9.5, 10.4, 9.8, 10.5, 10.2)):
        intraday.append({
            "t": f"2026-08-31T14:{30+i:02d}:00Z",
            "o": price,
            "h": price + 0.2,
            "l": price - 0.2,
            "c": price,
            "v": 1000 + i * 100,
        })
    daily=[
        {"t":"2026-08-26","o":8,"h":8.5,"l":7.8,"c":8.2,"v":1000},
        {"t":"2026-08-27","o":8.2,"h":10.2,"l":8.1,"c":10.0,"v":4000},
        {"t":"2026-08-28","o":10.0,"h":10.4,"l":9.8,"c":10.2,"v":1500},
        {"t":"2026-08-31","o":10.3,"h":12.0,"l":10.2,"c":11.8,"v":5000},
    ]
    result={
        "chart_data":{"intraday":intraday,"daily":daily},
        "vwap":10.1,
        "trade_plan":{
            "selected":{
                "entry_low":9.8,"entry_high":10.0,"stop":9.4,
                "target1":10.8,"target2":11.2,"stretch_target":11.8,
            }
        },
        "bounce_sequence":{
            "detected":True,
            "completed_bounces":1,
            "next_bounce_number":2,
            "current_leg":"BOUNCING",
            "reference_peak_index":3,
            "current_dip_low":9.6,
            "bounces":[{
                "number":1,
                "pullback_low":9.3,
                "pullback_low_index":2,
                "bounce_peak":10.6,
                "bounce_peak_index":3,
            }],
        },
        "stair_step":{
            "detected":True,
            "reaccelerating":True,
            "current_plateau_center":10.2,
            "current_plateau_range_pct":2.0,
            "steps":[
                {"date":"2026-08-27","step_close":10.0,"step_pct":22.0},
                {"date":"2026-08-31","step_close":11.8,"step_pct":15.7},
            ],
        },
        "impulse_pullback":{
            "detected":True,
            "impulse_low":9.3,
            "impulse_high":11.2,
            "bounce_confirmed":True,
        },
        "supports":[{"price":9.5}],
        "resistances":[{"price":11.0}],
    }

    trade=av.trade_plan_chart_spec(result)
    bounce=av.multi_bounce_chart_spec(result)
    stair=av.stair_step_chart_spec(result)
    impulse=av.impulse_pullback_chart_spec(result)
    sr=av.support_resistance_chart_spec(result)

    for spec in (trade,bounce,stair,impulse,sr):
        assert spec and spec.get("layer"), spec

    bounce_text=str(bounce)
    assert "Bounce #1 ✓" in bounce_text
    assert "Bounce #2 developing" in bounce_text
    assert "B2 forming dip" in bounce_text

    stair_text=str(stair)
    assert "Step 1 +22.0%" in stair_text
    assert "Reacceleration active" in stair_text

    trade_text=str(trade)
    assert "Target 1" in trade_text
    assert "VWAP" in trade_text


def test_analyzer_visuals_use_dark_high_contrast_theme():
    import analyzer_visuals as av

    config=av._config()
    assert config.get("background") == "#08111f", config
    view=config.get("view") or {}
    assert view.get("fill") == "#08111f", view
    axis=config.get("axis") or {}
    assert axis.get("labelColor") == "#b8c9dc", axis
    assert axis.get("titleColor") == "#dcecff", axis
    assert axis.get("gridColor") == "#28435d", axis

    candles=[
        {"t":"2026-08-31T14:30:00Z","o":9.95,"c":10.0,"h":10.1,"l":9.9,"v":1000},
        {"t":"2026-08-31T14:31:00Z","o":10.0,"c":10.2,"h":10.3,"l":10.0,"v":1200},
    ]
    layers=av._candlestick_layers(candles,line_overlay=False)
    assert any((layer.get("mark") or {}).get("type")=="rule" for layer in layers), layers
    assert any((layer.get("mark") or {}).get("type")=="bar" for layer in layers), layers
    assert not any((layer.get("mark") or {}).get("type")=="line" for layer in layers), layers
    overlay=av._candlestick_layers(candles,line_overlay=True)
    assert any((layer.get("mark") or {}).get("type")=="line" for layer in overlay), overlay


def test_analyzer_visual_snapshots_are_collapsible_and_contextual():
    from pathlib import Path

    source=Path("analyzer_ui_core.py").read_text(encoding="utf-8")
    for label in (
        "📈 Trade plan visual · entry · stop · targets",
        "📈 Impulse / pullback visual",
        "📈 Multi-bounce visual · dips · confirmed bounces",
        "📈 Stair-step visual · steps · plateau · reacceleration",
    ):
        assert label in source, label

    assert "trade_plan_chart_spec(r, line_overlay=overlay)" in source
    assert "multi_bounce_chart_spec(r, line_overlay=overlay)" in source
    assert "stair_step_chart_spec(r, line_overlay=overlay)" in source
    assert "impulse_pullback_chart_spec(r, line_overlay=overlay)" in source
    assert "support_resistance_chart_spec(r,line_overlay=_sr_line)" in source
    assert "Close-line overlay" in source
    assert "Candlesticks are the primary chart" in source


def test_analyzer_long_context_text_is_collapsible():
    from pathlib import Path

    core=Path("analyzer_ui_core.py").read_text(encoding="utf-8")
    ml=Path("ml_ui.py").read_text(encoding="utf-8")

    for text in (
        'with st.expander("Impulse / pullback context", expanded=False):',
        'with st.expander("Multi-bounce context", expanded=False):',
        'with st.expander("Support / resistance timing note", expanded=False):',
        'with st.expander("Historical analog context", expanded=False):',
        'f"Scenario context · dominant: {_dominant or \'—\'}"',
    ):
        assert text in core, text

    assert 'with st.expander("ML sequence context", expanded=False):' in ml
    assert 'with st.expander("Peer cohort context", expanded=False):' in ml


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
            # Friday 11:00 AM ET: regular-session fixture. The prior fixture
            # used Saturday, which is now correctly rejected by the tracker.
            now=datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc),
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


def test_swing_feature_research_freezes_thresholds_before_confirmation():
    import swing_feature_research as sfr

    rows = []
    for day_index in range(40):
        date_text = f"2025-01-{day_index + 1:02d}"
        for symbol_index in range(20):
            features = {name: 0.0 for name in sfr.tml.BASE_FEATURES}
            features["trend_score"] = float(day_index + symbol_index)
            rows.append(
                {
                    "date": date_text,
                    "symbol": f"R{symbol_index}",
                    "label": int(symbol_index % 2),
                    "swing_score": 50.0,
                    "features": features,
                }
            )

    discovery, confirmation, _cutoff = sfr._discovery_split(rows)
    before = [
        rule
        for rule in sfr._single_rule_pool(discovery)
        if rule["feature"] == "trend_score"
    ]

    for row in confirmation:
        row["features"]["trend_score"] = 99999.0

    after = [
        rule
        for rule in sfr._single_rule_pool(discovery)
        if rule["feature"] == "trend_score"
    ]
    assert before == after, (before, after)
    assert set(row["date"] for row in discovery).isdisjoint(
        set(row["date"] for row in confirmation)
    )


def test_swing_feature_research_requires_holdout_confirmation():
    import swing_feature_research as sfr

    candidate = {
        "rules": [
            {
                "feature": "trend_score",
                "op": ">=",
                "threshold": 60.0,
                "threshold_source": "q65",
            }
        ],
        "text": "trend_score >= 60",
        "discovery": {
            "n": 500,
            "rest_n": 500,
            "lift_pp": 8.0,
            "z_score": 3.0,
        },
        "confirmation": {
            "n": 300,
            "rest_n": 300,
            "lift_pp": -2.0,
            "z_score": -0.8,
        },
        "year_stability": {
            "eligible_years": 6,
            "positive_lift_years": 5,
            "worst_year_lift_pp": -3.0,
        },
    }
    assert sfr._is_robust(candidate) is False


def test_swing_feature_research_preserves_market_regime_labels():
    import swing_feature_research as sfr

    candidate = {
        "rules": [
            {
                "feature": "trend_score",
                "op": ">=",
                "threshold": 50.0,
                "threshold_source": "q50",
            }
        ],
        "text": "trend_score >= 50",
    }
    rows = [
        {
            "date": "2026-01-02",
            "label": 1,
            "market_regime_label": "RISK_ON",
            "features": {"trend_score": 60.0},
        },
        {
            "date": "2026-01-03",
            "label": 0,
            "market_regime_label": "RISK_OFF",
            "features": {"trend_score": 40.0},
        },
    ]
    breakdown = sfr._regime_breakdown(rows, candidate)
    assert set(breakdown) == {"RISK_ON", "RISK_OFF"}, breakdown


def test_live_swing_research_flags_match_frozen_rules():
    import swing_research_flags as srf

    metrics = {
        "day_pct": 8.2,
        "stair_step": {
            "last_step_pct": 6.4,
            "step_count": 3,
        },
    }
    timeframe = {
        "daily_trend": {
            "return_20d_pct": -12.0,
        }
    }
    result = srf.evaluate_swing_research_flags(metrics, timeframe)
    ids = {item["id"] for item in result["matches"]}
    assert result["tracking_only"] is True, result
    assert ids == {
        "reversal_ignition",
        "strong_stair_step",
        "strong_momentum_day",
    }, result
    reversal = next(
        item for item in result["matches"]
        if item["id"] == "reversal_ignition"
    )
    assert reversal["variant"] == "DEEP REVERSAL", reversal
    assert (
        reversal["historical_confirmation"]["confirmation_success_pct"]
        == 52.3
    ), reversal


def test_live_swing_research_flags_never_change_scores():
    import copy
    import swing_research_flags as srf

    metrics = {
        "day_pct": 10.0,
        "score": 71.0,
        "trade_plan": {"confidence": 66.0},
        "stair_step": {"last_step_pct": 7.0, "step_count": 2},
    }
    timeframe = {
        "scores": {"intraday": 61.0, "swing": 58.0, "long_term": 49.0},
        "daily_trend": {"return_20d_pct": -5.0},
    }
    before_metrics = copy.deepcopy(metrics)
    before_timeframe = copy.deepcopy(timeframe)
    result = srf.evaluate_swing_research_flags(metrics, timeframe)
    assert result["matched"] is True, result
    assert metrics == before_metrics, (metrics, before_metrics)
    assert timeframe == before_timeframe, (timeframe, before_timeframe)


def test_live_swing_research_calibration_dedupes_ticker_day():
    import prediction_tracker as pt
    import swing_research_flags as srf

    rows = [
        {
            "symbol": "TEST",
            "timestamp": "2026-08-24T14:00:00+00:00",
            "swing_research_flag_version": srf.FLAG_VERSION,
            "swing_research_flag_ids": ["reversal_ignition"],
            "swing_research_sampling_context": "regular_intraday",
            "swing_research_universe_proxy_pass": True,
            "outcomes": {
                "swing_target_before_stop_5d": 1,
                "swing_mfe_5d_pct": 8.0,
                "swing_mae_5d_pct": -2.0,
            },
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-24T14:05:00+00:00",
            "swing_research_flag_version": srf.FLAG_VERSION,
            "swing_research_flag_ids": ["reversal_ignition"],
            "swing_research_sampling_context": "regular_intraday",
            "swing_research_universe_proxy_pass": True,
            "outcomes": {
                "swing_target_before_stop_5d": 0,
                "swing_mfe_5d_pct": 1.0,
                "swing_mae_5d_pct": -5.0,
            },
        },
        {
            "symbol": "NEXT",
            "timestamp": "2026-08-24T15:00:00+00:00",
            "swing_research_flag_version": srf.FLAG_VERSION,
            "swing_research_flag_ids": ["reversal_ignition"],
            "swing_research_sampling_context": "regular_intraday",
            "swing_research_universe_proxy_pass": True,
            "outcomes": {
                "swing_target_before_stop_5d": 0,
                "swing_mfe_5d_pct": 2.0,
                "swing_mae_5d_pct": -4.5,
            },
        },
    ]
    summary = pt._swing_research_flag_summary(rows)
    item = summary["reversal_ignition"]
    assert item["signals"] == 2, item
    assert item["resolved"] == 2, item
    assert item["target_before_stop_rate_pct"] == 50.0, item


def test_analyzer_daily_history_prefers_tradier():
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, tzinfo=timezone.utc)
    expected = [
        {
            "t": "2026-08-27T20:00:00Z",
            "o": 10.0,
            "h": 10.5,
            "l": 9.8,
            "c": 10.3,
            "v": 100000,
        }
    ]

    sa.USE_TRADIER_HISTORY = True
    sa.get_tradier_history_bars = (
        lambda symbol, token, s, e, interval="daily": expected
    )
    rows, source = sa.try_sip_delayed_bars(
        "TEST",
        "1Day",
        start,
        end,
        320,
    )
    assert rows == expected, rows
    assert source == "Tradier consolidated daily", source


def test_monday_readiness_blocks_stale_scan_handoffs():
    from pathlib import Path

    source = Path("app.py").read_text(encoding="utf-8")
    assert "latest_scan_stale" in source
    assert "latest_scan_age > 4 * 60" in source
    assert "disabled=latest_scan_stale" in source
    assert "old setup cannot be mistaken for a current one" in source


def test_live_scanner_uses_two_minute_cadence():
    from pathlib import Path

    source = Path("scanner_app.py").read_text(encoding="utf-8")
    assert "AUTO_SCAN_SECONDS = 120" in source
    assert "AUTO_STATUS_REFRESH_SECONDS = 15" in source
    assert "Auto scan every 2 minutes" in source
    assert "Automatic 2-minute scan running" in source
    assert "scan_age_seconds > 4 * 60" in source


def test_analyzer_live_test_status_exposes_tracking_health():
    from pathlib import Path

    source = Path("analyzer_v2_ui.py").read_text(encoding="utf-8")
    assert "Live test status" in source
    assert "Durable tracking **ON**" in source
    assert "Swing forward tracking **" in source


def test_analyzer_tradier_does_not_block_on_alpaca_snapshot():
    _install_common_analyzer_stubs()
    bars = _regular_bars()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    calls = {"snapshot": 0}

    def _fail_snapshot(*args, **kwargs):
        calls["snapshot"] += 1
        raise RuntimeError("simulated Alpaca snapshot outage")

    sa.snapshot = _fail_snapshot
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
    sa._tradier_regular_session_bars = lambda symbol, now: bars

    result = sa.analyze("TEST")
    assert result["market_provider"] == "tradier", result
    assert result["price"] == 10.10, result
    assert calls["snapshot"] == 0, calls
    assert result.get("alpaca_fallback_error") is None, result


def test_analyzer_reports_actual_historical_provider():
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
    sa._tradier_regular_session_bars = lambda symbol, now: bars
    sa.try_sip_delayed_bars = (
        lambda symbol, timeframe, start, end, limit=1000:
        (_daily_bars(), "Tradier consolidated daily")
    )

    result = sa.analyze("TEST")
    assert result["historical_provider"] == "tradier", result
    assert result["historical_feed"] == "Tradier consolidated daily", result


def test_swing_research_live_context_is_not_historical_parity():
    import swing_research_flags as srf

    metrics = {
        "as_of": "2026-08-28T15:00:00+00:00",
        "price": 10.0,
        "day_pct": 8.0,
        "session_volume": 100_000,
        "stair_step": {"last_step_pct": 6.5, "step_count": 2},
    }
    timeframe = {"daily_trend": {"return_20d_pct": -5.0}}
    result = srf.evaluate_swing_research_flags(metrics, timeframe)
    assert result["live_sampling_context"] == "regular_intraday", result
    assert result["historical_universe_proxy_pass"] is True, result
    assert result["direct_historical_parity"] is False, result
    assert "end-of-day" in result["note"], result


def test_swing_research_calibration_excludes_wrong_context():
    import prediction_tracker as pt
    import swing_research_flags as srf

    rows = [
        {
            "symbol": "GOOD",
            "timestamp": "2026-08-28T15:00:00+00:00",
            "swing_research_flag_version": srf.FLAG_VERSION,
            "swing_research_flag_ids": ["strong_momentum_day"],
            "swing_research_sampling_context": "regular_intraday",
            "swing_research_universe_proxy_pass": True,
            "outcomes": {"swing_target_before_stop_5d": 1},
        },
        {
            "symbol": "AFTER",
            "timestamp": "2026-08-28T21:00:00+00:00",
            "swing_research_flag_version": srf.FLAG_VERSION,
            "swing_research_flag_ids": ["strong_momentum_day"],
            "swing_research_sampling_context": "afterhours",
            "swing_research_universe_proxy_pass": True,
            "outcomes": {"swing_target_before_stop_5d": 0},
        },
        {
            "symbol": "THIN",
            "timestamp": "2026-08-28T15:05:00+00:00",
            "swing_research_flag_version": srf.FLAG_VERSION,
            "swing_research_flag_ids": ["strong_momentum_day"],
            "swing_research_sampling_context": "regular_intraday",
            "swing_research_universe_proxy_pass": False,
            "outcomes": {"swing_target_before_stop_5d": 0},
        },
    ]
    summary = pt._swing_research_flag_summary(rows)
    item = summary["strong_momentum_day"]
    assert item["signals"] == 1, item
    assert item["resolved"] == 1, item
    assert item["target_before_stop_rate_pct"] == 100.0, item
    assert item["direct_historical_parity"] is False, item


def test_scanner_visibly_marks_stale_snapshot():
    from pathlib import Path

    source = Path("scanner_app.py").read_text(encoding="utf-8")
    assert "STALE SCAN — do not treat these rankings as current" in source
    assert "scan_age_seconds > 4 * 60" in source
    assert "⚠ STALE SNAPSHOT" in source


def test_swing_research_ui_disclaims_historical_probability():
    from pathlib import Path

    source = Path("analyzer_v2_ui.py").read_text(encoding="utf-8")
    assert "Historical EOD reference only" in source
    assert "not a live success probability" in source
    assert "exploratory evidence" in source


def test_legacy_analyzer_entrypoint_cannot_drift():
    from pathlib import Path

    source = Path("analyzer_app_fixed.py").read_text(encoding="utf-8")
    assert "analyzer_app.py" in source
    assert "ALPACA_API_KEY" not in source
    assert "Single Stock Analyzer" not in source


def test_scanner_ui_accepts_tradier_without_alpaca_credentials():
    from pathlib import Path

    ui_source = Path("scanner_app.py").read_text(encoding="utf-8")
    runtime_source = Path("scanner_runtime.py").read_text(encoding="utf-8")
    assert "run_scanner_process" in ui_source
    assert "if not has_alpaca and not tradier_token" in runtime_source
    assert "if has_alpaca:" in runtime_source
    assert 'env["TRADIER_ACCESS_TOKEN"] = tradier_token' in runtime_source
    assert "No market-data provider is configured" in runtime_source


def test_live_scanner_matches_scheduled_tradier_discovery():
    from pathlib import Path

    ui_source = Path("scanner_app.py").read_text(encoding="utf-8")
    runtime_source = Path("scanner_runtime.py").read_text(encoding="utf-8")
    assert "discovery_universe_size=" in ui_source
    assert 'env["SCANNER_TRADIER_DISCOVERY"] = "1"' in runtime_source
    assert 'env["SCANNER_DISCOVERY_UNIVERSE_SIZE"]' in runtime_source


def test_analyzer_session_filter_uses_current_extended_session():
    # 2026-08-31 is a Monday in EDT.
    pre_now = datetime(2026, 8, 31, 12, 30, tzinfo=timezone.utc)  # 8:30 AM ET
    pre_raw = [
        {"t": "2026-08-31T07:55:00Z", "c": 9.9},  # 3:55 ET, before premarket
        {"t": "2026-08-31T12:05:00Z", "c": 10.0}, # 8:05 ET, premarket
        {"t": "2026-08-31T12:20:00Z", "c": 10.1}, # 8:20 ET, premarket
        {"t": "2026-08-28T19:55:00Z", "c": 9.8},  # prior regular session
    ]
    pre = sa._filter_session_bars(pre_raw, pre_now)
    assert [row["t"] for row in pre] == [
        "2026-08-31T12:05:00Z",
        "2026-08-31T12:20:00Z",
    ], pre

    after_now = datetime(2026, 8, 31, 21, 30, tzinfo=timezone.utc)  # 5:30 PM ET
    after_raw = [
        {"t": "2026-08-31T19:55:00Z", "c": 10.2}, # 3:55 ET regular
        {"t": "2026-08-31T20:05:00Z", "c": 10.3}, # 4:05 ET after-hours
        {"t": "2026-08-31T21:00:00Z", "c": 10.4}, # 5:00 ET after-hours
    ]
    after = sa._filter_session_bars(after_raw, after_now)
    assert [row["t"] for row in after] == [
        "2026-08-31T20:05:00Z",
        "2026-08-31T21:00:00Z",
    ], after


def test_analyzer_closed_preview_uses_latest_regular_session():
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)  # Saturday
    raw = [
        {"t": "2026-08-28T12:00:00Z", "c": 9.7},  # Friday premarket
        {"t": "2026-08-28T14:00:00Z", "c": 10.0}, # Friday regular
        {"t": "2026-08-28T19:55:00Z", "c": 10.2}, # Friday regular
        {"t": "2026-08-28T20:10:00Z", "c": 10.3}, # Friday after-hours
    ]
    result = sa._filter_session_bars(raw, now)
    assert [row["t"] for row in result] == [
        "2026-08-28T14:00:00Z",
        "2026-08-28T19:55:00Z",
    ], result


def test_analyzer_does_not_fake_extended_hours_volume_pace():
    from pathlib import Path

    source = Path("stock_analyzer.py").read_text(encoding="utf-8")
    assert 'if avgvol and session_phase=="regular":' in source
    assert 'f"TRADIER {session_phase.upper()}"' in source
    assert '"market_session":session_phase' in source


def test_intraday_calibration_excludes_offhours_and_weekends():
    import score_analyzer_outcomes as sao

    rows = [
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T13:15:00+00:00",  # 9:15 AM ET
            "outcomes": {"return_60m_pct": 1.0},
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T13:35:00+00:00",  # 9:35 AM ET
            "outcomes": {"return_60m_pct": 2.0},
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T13:50:00+00:00",  # same ET hour
            "outcomes": {"return_60m_pct": 3.0},
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T14:05:00+00:00",  # 10:05 AM ET
            "outcomes": {"return_60m_pct": 4.0},
        },
        {
            "symbol": "WEEKEND",
            "timestamp": "2026-08-29T15:00:00+00:00",
            "outcomes": {"return_60m_pct": 9.0},
        },
    ]
    selected = sao._independent_calibration_rows(rows)
    assert len(selected) == 2, selected
    assert [row["outcomes"]["return_60m_pct"] for row in selected] == [2.0, 4.0], selected


def test_timeframe_calibration_uses_one_latest_regular_row_per_ticker_day():
    import score_analyzer_outcomes as sao

    rows = [
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T14:00:00+00:00",  # 10 AM ET
            "timeframe_score_version": sao.TIMEFRAME_SCORE_VERSION,
            "timeframe_swing_score": 60.0,
            "outcomes": {"return_5d_pct": 1.0},
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T18:00:00+00:00",  # 2 PM ET
            "timeframe_score_version": sao.TIMEFRAME_SCORE_VERSION,
            "timeframe_swing_score": 80.0,
            "outcomes": {"return_5d_pct": 5.0},
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T21:00:00+00:00",  # 5 PM ET
            "timeframe_score_version": sao.TIMEFRAME_SCORE_VERSION,
            "timeframe_swing_score": 95.0,
            "outcomes": {"return_5d_pct": -8.0},
        },
        {
            "symbol": "WEEKEND",
            "timestamp": "2026-08-29T15:00:00+00:00",
            "timeframe_score_version": sao.TIMEFRAME_SCORE_VERSION,
            "timeframe_swing_score": 99.0,
            "outcomes": {"return_5d_pct": 20.0},
        },
    ]
    daily = sao._timeframe_daily_calibration_rows(rows)
    assert len(daily) == 1, daily
    assert daily[0]["timeframe_swing_score"] == 80.0, daily
    calibrated = sao._timeframe_calibrate(
        daily, "timeframe_swing_score", "return_5d_pct"
    )
    assert calibrated["80-100"]["n"] == 1, calibrated
    assert calibrated["80-100"]["avg_return_pct"] == 5.0, calibrated


def test_prediction_tracker_mirrors_daily_timeframe_sampling():
    import prediction_tracker as pt

    rows = [
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T14:00:00+00:00",
            "timeframe_score_version": pt.TIMEFRAME_SCORE_VERSION,
            "timeframe_swing_score": 61.0,
            "outcomes": {"return_5d_pct": 1.0},
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T19:00:00+00:00",
            "timeframe_score_version": pt.TIMEFRAME_SCORE_VERSION,
            "timeframe_swing_score": 79.0,
            "outcomes": {"return_5d_pct": 4.0},
        },
        {
            "symbol": "TEST",
            "timestamp": "2026-08-28T20:30:00+00:00",
            "timeframe_score_version": pt.TIMEFRAME_SCORE_VERSION,
            "timeframe_swing_score": 99.0,
            "outcomes": {"return_5d_pct": -9.0},
        },
    ]
    daily = pt._timeframe_daily_calibration_rows(rows)
    assert len(daily) == 1, daily
    assert daily[0]["timeframe_swing_score"] == 79.0, daily


def test_old_calibration_schema_is_rejected():
    from pathlib import Path

    source = Path("prediction_tracker.py").read_text(encoding="utf-8")
    assert 'int(durable.get("schema_version") or 0) < 7' in source
    outcome_source = Path("score_analyzer_outcomes.py").read_text(encoding="utf-8")
    assert '"schema_version": 7' in outcome_source


def test_outcome_tracker_runs_after_extended_hours():
    from pathlib import Path

    source = Path(".github/workflows/outcome-tracker.yml").read_text(encoding="utf-8")
    assert "- cron: '30 1 * * 2-6'" in source
    assert "after the full 4:00 AM-8:00 PM ET extended session" in source


def test_scanner_table_volume_pace_formatter_matches_column():
    from pathlib import Path

    source = Path("scanner_app.py").read_text(encoding="utf-8")
    assert '"TOD Vol Pace": lambda x:' in source
    assert '"Vol Pace": lambda x:' not in source


def test_combined_scanner_uses_display_volume_pace_source():
    from pathlib import Path

    source = Path("app.py").read_text(encoding="utf-8")
    assert 'row.get("volume_pace_display_source")' in source


def test_prediction_tracker_skips_closed_market_records():
    import prediction_tracker as pt

    # Sunday noon ET: should not create a prediction row or touch storage.
    result = pt.record_prediction(
        {"symbol": "TEST", "market_session": "closed"},
        now=datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc),
    )
    assert result["recorded"] is False, result
    assert result["reason"] == "market_closed_not_recorded", result
    assert result["market_session"] == "closed", result


def test_scanner_outcome_report_does_not_call_gross_returns_trade_wins():
    from pathlib import Path

    source = Path("score_outcomes.py").read_text(encoding="utf-8")
    assert "Positive-return rate" in source
    assert "not realized trade P/L" in source
    assert '"execution_adjusted": False' in source
    assert '"spread_applied_to_returns": False' in source
    assert '"slippage_applied": False' in source
    assert '"fees_applied": False' in source
    assert "Use an execution-aware simulation before making profitability claims." in source


def test_late_scanner_report_has_explicit_no_horizon_status():
    from pathlib import Path

    source = Path("score_outcomes.py").read_text(encoding="utf-8")
    assert "def outcome_report_status(rows, summary):" in source
    assert '"complete_no_resolvable_horizons"' in source
    assert "too close to the 4:00 PM ET close" in source
    assert "This is not a market-data failure." in source


def test_manual_scanner_refreshes_combined_candidates_after_success():
    from pathlib import Path

    source = Path("scanner_app.py").read_text(encoding="utf-8")
    assert 'st.session_state["_scanner_flash_success"] = msg' in source
    assert "newly written latest_scan.json" in source
    assert "st.rerun()" in source


def test_v2_skips_alpaca_sip_probe_when_tradier_primary():
    import analyzer_v2_integration as v2

    class FakeAnalyzer:
        USE_TRADIER = True
        LIVE_FEED = "iex"

        @staticmethod
        def snapshot(*args, **kwargs):
            raise AssertionError("Alpaca SIP probe should not run with Tradier primary")

    result = v2.prefer_best_live_feed(FakeAnalyzer(), "SPY")
    assert result["provider"] == "tradier", result
    assert result["alpaca_probe_skipped"] is True, result
    assert result["active_feed"] == "TRADIER CONSOLIDATED", result


def test_v2_market_context_prefers_tradier_quotes():
    import analyzer_v2_integration as v2

    class FakeAnalyzer:
        USE_TRADIER = True
        TRADIER_TOKEN = "test-token"
        LIVE_FEED = "iex"

        @staticmethod
        def get_tradier_quotes(symbols, token):
            assert token == "test-token"
            return {
                "SPY": {"last": 102.0, "prevclose": 100.0},
                "QQQ": {"last": 101.0, "prevclose": 100.0},
                "IWM": {"last": 99.0, "prevclose": 100.0},
            }

        @staticmethod
        def snapshot(*args, **kwargs):
            raise AssertionError("Alpaca market context should not run")

    v2._MARKET_CACHE.clear()
    result = v2._market_context(FakeAnalyzer())
    assert result["provider"] == "tradier", result
    assert result["moves"] == {"SPY": 2.0, "QQQ": 1.0, "IWM": -1.0}, result
    assert result["broad_market_avg_pct"] == 0.67, result


def test_analyzer_health_warns_on_durable_sync_error():
    from pathlib import Path

    source = Path("analyzer_v2_ui.py").read_text(encoding="utf-8")
    assert "durable_error" in source
    assert "durable GitHub sync reported an error" in source
    assert '"MATCH ACTIVE"' in source
    assert '"ARMED"' in source
    assert '"PAUSED OFF-HOURS"' in source


def test_scanner_timeframe_fit_separates_intraday_swing_and_longer_term():
    import scanner_timeframe_fit as stf

    intraday = stf.classify_timeframe_fit(
        {
            "momentum_5m": 1.8,
            "momentum_15m": 3.2,
            "volume_pace_display": 2.5,
            "vwap": 10.0,
            "above_vwap": True,
            "distance_from_high_pct": 1.0,
            "spread_pct": 0.8,
            "day_pct": 7.0,
        }
    )
    assert intraday["primary_fit"] == "INTRADAY", intraday
    assert intraday["scores"]["INTRADAY"] > intraday["scores"]["SWING"], intraday

    swing = stf.classify_timeframe_fit(
        {
            "momentum_5m": -0.2,
            "momentum_15m": 0.1,
            "volume_pace_display": 1.1,
            "vwap": 10.0,
            "above_vwap": True,
            "distance_from_high_pct": 5.0,
            "spread_pct": 1.0,
            "day_pct": 9.0,
            "daily_return_5d_pct": 12.0,
            "daily_return_20d_pct": 14.0,
            "stair_structure_score": 76.0,
            "stair_step_count": 3,
            "stair_reaccelerating": True,
            "daily_return_40d_pct": 2.0,
            "daily_above_ma20": True,
            "daily_above_ma40": False,
            "daily_ma_alignment": "MIXED",
            "daily_from_recent_high_pct": -10.0,
        }
    )
    assert swing["primary_fit"] == "SWING", swing
    assert swing["scores"]["SWING"] > swing["scores"]["LONGER-TERM"], swing

    longer = stf.classify_timeframe_fit(
        {
            "momentum_5m": -0.5,
            "momentum_15m": -0.3,
            "volume_pace_display": 0.8,
            "vwap": 10.0,
            "above_vwap": False,
            "distance_from_high_pct": 7.0,
            "spread_pct": 1.0,
            "day_pct": 5.0,
            "daily_return_5d_pct": 1.0,
            "daily_return_20d_pct": 18.0,
            "daily_return_40d_pct": 35.0,
            "daily_above_ma20": True,
            "daily_above_ma40": True,
            "daily_ma_alignment": "BULLISH",
            "daily_from_recent_high_pct": -5.0,
            "stair_structure_score": 58.0,
            "stair_step_count": 1,
        }
    )
    assert longer["primary_fit"] == "LONGER-TERM", longer
    assert longer["scores"]["LONGER-TERM"] >= 80.0, longer


def test_scanner_longer_term_fit_is_capped_when_history_is_sparse():
    import scanner_timeframe_fit as stf

    result = stf.classify_timeframe_fit(
        {
            "day_pct": 8.0,
            "daily_return_20d_pct": 20.0,
        }
    )
    assert result["scores"]["LONGER-TERM"] <= 57.0, result
    assert any(
        "history coverage is limited" in reason
        for reason in result["reasons"]["LONGER-TERM"]
    ), result


def test_scanner_timeframe_fit_never_changes_production_rank_fields():
    import copy
    import scanner_timeframe_fit as stf

    row = {
        "symbol": "TEST",
        "score": 81.0,
        "opportunity_score": 84.0,
        "scanner_action": "ANALYZE NOW",
        "momentum_5m": 1.2,
        "momentum_15m": 2.0,
        "volume_pace_display": 2.0,
        "vwap": 10.0,
        "above_vwap": True,
        "distance_from_high_pct": 2.0,
        "spread_pct": 1.0,
        "day_pct": 8.0,
    }
    before = copy.deepcopy(row)
    stf.attach_timeframe_fit(row)
    assert row["score"] == before["score"], row
    assert row["opportunity_score"] == before["opportunity_score"], row
    assert row["scanner_action"] == before["scanner_action"], row
    assert row["timeframe_fit"]["production_rank_impact"] is False, row


def test_scanner_and_analyzer_scores_are_labeled_as_non_probabilities():
    from pathlib import Path

    scanner = Path("scanner_app.py").read_text(encoding="utf-8")
    analyzer = Path("analyzer_v2_ui.py").read_text(encoding="utf-8")
    assert "SETUP SCORE / 100" in scanner
    assert "not a probability of profit and not an entry command" in scanner
    assert "very high Setup Score" in scanner
    assert "setup-strength score for further upside; not a probability" in analyzer
    assert "current entry-quality score; not a success probability" in analyzer
    assert "fit scores are not probabilities" in analyzer


def test_scanner_ui_exposes_timeframe_filter_without_reranking():
    from pathlib import Path

    source = Path("scanner_app.py").read_text(encoding="utf-8")
    assert '"Trade Horizon Focus"' in source
    assert '"Short term (intraday)"' in source
    assert '"Medium term (swing)"' in source
    assert '"Long term"' in source
    assert '"LONGER-TERM"' in source
    assert "Ranking itself is unchanged" in source
    assert "BEST FIT" in source
    combined = Path("app.py").read_text(encoding="utf-8")
    assert "Grade · Best Fit" in combined


def test_actionable_momentum_alert_requires_existing_strong_scanner_state():
    import momentum_alerts as ma

    base = {
        "symbol": "TEST",
        "setup_grade": "A",
        "scanner_action": "ANALYZE NOW",
        "passed_base_filters": True,
        "alert_ready": True,
    }
    assert ma.is_actionable_momentum_alert(base) is True

    for field, value in (
        ("setup_grade", "C"),
        ("scanner_action", "WAIT PULLBACK"),
        ("passed_base_filters", False),
        ("alert_ready", False),
    ):
        row = dict(base)
        row[field] = value
        assert ma.is_actionable_momentum_alert(row) is False, (field, row)


def test_high_score_pullback_watch_requires_strong_score_and_trusted_data():
    import momentum_alerts as ma

    base = {
        "symbol": "WETO",
        "setup_grade": "A",
        "scanner_action": "WAIT PULLBACK",
        "passed_base_filters": True,
        "action_data_integrity_ok": True,
        "score": 94,
        "timeframe_best_fit": "INTRADAY",
        "volume_pace": 38.4,
    }
    assert ma.PULLBACK_WATCH_MIN_SCORE == 90.0
    assert ma.is_high_score_pullback_watch(base) is True
    assert "wait for pullback confirmation" in ma.pullback_watch_message(base)

    for field, value in (
        ("score", 89.9),
        ("scanner_action", "ANALYZE NOW"),
        ("passed_base_filters", False),
        ("action_data_integrity_ok", False),
        ("setup_grade", "C"),
    ):
        row = dict(base)
        row[field] = value
        assert ma.is_high_score_pullback_watch(row) is False, (field, row)


def test_high_score_pullback_watch_alert_is_state_deduplicated():
    import momentum_alerts as ma

    watch = {
        "candidates": [
            {
                "symbol": "WETO",
                "setup_grade": "A",
                "scanner_action": "WAIT PULLBACK",
                "passed_base_filters": True,
                "action_data_integrity_ok": True,
                "score": 94,
            }
        ]
    }
    ready = {
        "candidates": [
            {
                "symbol": "WETO",
                "setup_grade": "A",
                "scanner_action": "ANALYZE NOW",
                "passed_base_filters": True,
                "action_data_integrity_ok": True,
                "score": 94,
            }
        ]
    }

    first, keys = ma.newly_high_score_pullback(watch, [])
    assert [row["symbol"] for row in first] == ["WETO"], first
    repeated, keys = ma.newly_high_score_pullback(watch, keys)
    assert repeated == [], repeated
    left, keys = ma.newly_high_score_pullback(ready, keys)
    assert left == [], left
    assert keys == set(), keys
    reentered, keys = ma.newly_high_score_pullback(watch, keys)
    assert [row["symbol"] for row in reentered] == ["WETO"], reentered


def test_momentum_alert_only_fires_when_symbol_newly_enters_ready_state():
    import momentum_alerts as ma

    payload = {
        "candidates": [
            {
                "symbol": "AAA",
                "setup_grade": "A",
                "scanner_action": "ANALYZE NOW",
                "passed_base_filters": True,
                "alert_ready": True,
            },
            {
                "symbol": "BBB",
                "setup_grade": "B",
                "scanner_action": "WATCH",
                "passed_base_filters": True,
                "alert_ready": True,
            },
        ]
    }
    new_rows, current = ma.newly_actionable(payload, [])
    assert [row["symbol"] for row in new_rows] == ["AAA"], new_rows
    assert current == {"AAA"}, current

    new_rows, current = ma.newly_actionable(payload, {"AAA"})
    assert new_rows == [], new_rows
    assert current == {"AAA"}, current


def test_combined_app_keeps_one_async_scanner_loop_across_views():
    from pathlib import Path

    app_source = Path("app.py").read_text(encoding="utf-8")
    scanner_source = Path("scanner_app.py").read_text(encoding="utf-8")
    runtime_source = Path("scanner_runtime.py").read_text(encoding="utf-8")
    assert "def _workspace_scanner_monitor():" in app_source
    assert "start_scanner_process(" in app_source
    assert "poll_scanner_process(" in app_source
    assert 'st.session_state["_combined_scanner_monitor_active"] = True' in app_source
    assert "combined_monitor_active" in scanner_source
    assert "This child\n        # view only renders status" in scanner_source
    assert "os.O_CREAT | os.O_EXCL | os.O_WRONLY" in runtime_source
    assert "subprocess.Popen(" in runtime_source


def test_momentum_alert_ui_has_in_app_and_optional_browser_notifications():
    from pathlib import Path

    source = Path("app.py").read_text(encoding="utf-8")
    assert "ACTIONABLE MOMENTUM ALERT" in source
    assert "PULLBACK WATCH" in source
    assert "High-Score Pullback Watch" in source
    assert "This is an early heads-up, not an entry signal." in source
    assert "Review it in Analyzer before deciding whether to trade." in source
    assert "Enable browser alerts" in source
    assert "Notification.requestPermission" in source
    assert "permission === 'denied'" in source
    assert "browser notifications unavailable" in source
    assert "not an automatic buy signal" in source


def test_scanner_runtime_async_start_is_nonblocking_and_lock_safe():
    import sys
    import tempfile
    import time
    from pathlib import Path
    import scanner_runtime as sr

    old_lock = sr.LOCK_FILE
    temp_dir = tempfile.TemporaryDirectory()
    state = None
    try:
        sr.LOCK_FILE = Path(temp_dir.name) / "scanner.lock"
        started_at = time.perf_counter()
        state = sr.start_scanner_process(
            alpaca_key="test",
            alpaca_secret="test",
            command=[
                sys.executable,
                "-c",
                "import time; time.sleep(0.60); print('done')",
            ],
            require_scan_file=False,
            timeout_seconds=2,
        )
        start_latency = time.perf_counter() - started_at
        assert state["started"] is True, state
        assert start_latency < 0.35, start_latency
        assert sr.scanner_process_busy() is True

        duplicate = sr.start_scanner_process(
            alpaca_key="test",
            alpaca_secret="test",
            command=[sys.executable, "-c", "print('duplicate')"],
            require_scan_file=False,
            timeout_seconds=2,
        )
        assert duplicate["started"] is False, duplicate
        assert duplicate["busy"] is True, duplicate

        result = None
        deadline = time.time() + 3
        while time.time() < deadline:
            result = sr.poll_scanner_process(state)
            if result.get("done"):
                break
            time.sleep(0.03)
        assert result and result["done"] is True, result
        assert result["ok"] is True, result
        assert "done" in result["stdout"], result
        assert sr.scanner_process_busy() is False
        state = None
    finally:
        if state and state.get("process") and state["process"].poll() is None:
            state["process"].kill()
            state["process"].wait(timeout=2)
            sr._release_scan_lock(state.get("lock_token"))
            sr._cleanup_logs(state)
        sr.LOCK_FILE = old_lock
        temp_dir.cleanup()


def test_scanner_runtime_timeout_releases_shared_lock():
    import sys
    import tempfile
    import time
    from pathlib import Path
    import scanner_runtime as sr

    old_lock = sr.LOCK_FILE
    temp_dir = tempfile.TemporaryDirectory()
    state = None
    try:
        sr.LOCK_FILE = Path(temp_dir.name) / "scanner.lock"
        state = sr.start_scanner_process(
            alpaca_key="test",
            alpaca_secret="test",
            command=[
                sys.executable,
                "-c",
                "import time; time.sleep(2)",
            ],
            require_scan_file=False,
            timeout_seconds=0.05,
        )
        assert state["started"] is True, state
        time.sleep(0.08)
        result = sr.poll_scanner_process(state)
        assert result["done"] is True, result
        assert result["ok"] is False, result
        assert "timeout" in result["message"].lower(), result
        assert sr.scanner_process_busy() is False
        state = None
    finally:
        if state and state.get("process") and state["process"].poll() is None:
            state["process"].kill()
            state["process"].wait(timeout=2)
            sr._release_scan_lock(state.get("lock_token"))
            sr._cleanup_logs(state)
        sr.LOCK_FILE = old_lock
        temp_dir.cleanup()


def test_scanner_runtime_recovers_stale_lock_after_crash():
    import json
    import tempfile
    import time
    from pathlib import Path
    import scanner_runtime as sr

    old_lock = sr.LOCK_FILE
    temp_dir = tempfile.TemporaryDirectory()
    try:
        sr.LOCK_FILE = Path(temp_dir.name) / "scanner.lock"
        sr.LOCK_FILE.write_text(
            json.dumps(
                {
                    "token": "stale",
                    "created_at": time.time() - sr.LOCK_STALE_SECONDS - 5,
                    "pid": 999999,
                }
            ),
            encoding="utf-8",
        )
        assert sr.scanner_process_busy() is False
        assert sr.LOCK_FILE.exists() is False
    finally:
        sr.LOCK_FILE = old_lock
        temp_dir.cleanup()


def test_two_minute_runtime_health_flags_tight_and_overrun_scans():
    import scanner_runtime as sr

    healthy = sr.cadence_health(45, 120)
    tight = sr.cadence_health(100, 120)
    overrun = sr.cadence_health(125, 120)
    assert healthy["status"] == "healthy", healthy
    assert tight["status"] == "tight", tight
    assert tight["headroom_seconds"] == 20.0, tight
    assert overrun["status"] == "overrun", overrun
    assert overrun["headroom_seconds"] == -5.0, overrun


def test_momentum_alert_can_realert_only_after_leaving_ready_state():
    import momentum_alerts as ma

    ready = {
        "candidates": [
            {
                "symbol": "AAA",
                "setup_grade": "A",
                "scanner_action": "ANALYZE NOW",
                "passed_base_filters": True,
                "alert_ready": True,
            }
        ]
    }
    not_ready = {
        "candidates": [
            {
                "symbol": "AAA",
                "setup_grade": "A",
                "scanner_action": "WAIT PULLBACK",
                "passed_base_filters": True,
                "alert_ready": True,
            }
        ]
    }

    first, keys = ma.newly_actionable(ready, [])
    assert [row["symbol"] for row in first] == ["AAA"], first
    repeated, keys = ma.newly_actionable(ready, keys)
    assert repeated == [], repeated
    left, keys = ma.newly_actionable(not_ready, keys)
    assert left == [], left
    assert keys == set(), keys
    reentered, keys = ma.newly_actionable(ready, keys)
    assert [row["symbol"] for row in reentered] == ["AAA"], reentered


def test_offhours_daily_context_builds_swing_longer_term_candidate_without_live_action():
    import offhours_timeframe_scan as ots

    bars = []
    start = datetime(2026, 6, 1, 16, 0, tzinfo=ET)
    for i in range(60):
        close = 5.0 * (1.0 + 0.008 * i)
        bars.append(
            {
                "t": _iso(start + timedelta(days=i)),
                "o": close * 0.99,
                "h": close * 1.02,
                "l": close * 0.98,
                "c": close,
                "v": 500_000 + i * 8_000,
                "vw": None,
            }
        )

    row = ots._daily_context(
        "TEST",
        {
            "average_volume": 650_000,
            "average_dollar_volume": 4_000_000,
        },
        bars,
        spy_return_20d=3.0,
    )
    assert row is not None, row
    assert row["daily_history_sessions"] >= 42, row
    assert max(
        row["timeframe_swing_score"],
        row["timeframe_longer_term_score"],
    ) >= 60, row
    assert row["timeframe_longer_term_score"] >= 60, row
    assert 70 <= row["daily_discovery_score"] < 100, row
    assert row["trend_candidate_raw_score"] >= row["daily_discovery_score"], row
    assert row["trend_candidate_score_version"] == "trend-candidate-score-v1", row
    assert row["daily_review_action"] in {
        "REVIEW SWING",
        "REVIEW LONGER-TERM",
        "REVIEW SWING / LONGER-TERM",
    }, row
    assert row["production_rank_impact"] is False, row
    assert "scanner_action" not in row, row
    assert "ml_continuation_prob_pct" not in row, row


def test_offhours_history_pool_is_price_band_balanced_and_not_only_daily_movers():
    import offhours_timeframe_scan as ots

    quotes = {}
    idx = 0
    for low, high in ((1.0, 4.5), (6.0, 18.0), (22.0, 55.0)):
        for j in range(45):
            idx += 1
            price = low + (high - low) * (j / 44)
            quotes[f"T{idx:03d}"] = {
                "type": "stock",
                "last": price,
                "prevclose": price / (1.0 + ((j % 5) * 0.002)),
                "average_volume": 1_000_000 - j * 5_000,
                "volume": 900_000 + j * 2_000,
                "change_percentage": (j % 5) * 0.2,
            }

    pool, eligible = ots._preselect_history_pool(quotes, 90)
    assert eligible == 135, eligible
    assert len(pool) == 90, len(pool)
    prices = [row["price"] for row in pool]
    assert any(price < 5 for price in prices), prices
    assert any(5 <= price < 20 for price in prices), prices
    assert any(price >= 20 for price in prices), prices
    # The screen should contain low-change names selected for liquidity/structure,
    # not only the largest completed-session percentage movers.
    assert any(abs(row["change_pct"]) < 0.25 for row in pool), pool[:10]


def test_scanner_ui_surfaces_completed_daily_discovery_when_market_closed():
    from pathlib import Path

    scanner_source = Path("scanner_app.py").read_text(encoding="utf-8")
    app_source = Path("app.py").read_text(encoding="utf-8")
    assert "Off-Hours Swing / Longer-Term Discovery" in scanner_source
    assert "offhours_timeframe_latest.json" in scanner_source
    assert 'current_phase == "closed"' in scanner_source
    assert 'st.session_state.get("scanner_trade_horizon", "ALL")' in scanner_source
    assert "completed-daily Swing / Longer-Term discovery" in app_source
    assert "source_mode" in app_source
    assert "DAILY REVIEW" in app_source


def test_analyzer_outcome_horizon_rejects_late_gap_bars():
    import score_analyzer_outcomes as sao

    target = datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)
    bars = [
        {"t": "2026-08-28T15:03:00Z", "c": 10.3},
        {"t": "2026-08-28T15:04:00Z", "c": 10.4},
    ]
    assert sao.OUTCOME_MAX_BAR_DELAY_SECONDS == 180
    assert sao._price_at_or_after(bars, target) == 10.3

    late_only = [
        {"t": "2026-08-28T15:04:00Z", "c": 10.4},
    ]
    assert sao._price_at_or_after(late_only, target) is None


def test_live_confirmation_rows_do_not_double_count_overlapping_ticker_windows():
    import scanner_ml_ranker as sm

    rows = [
        {"symbol": "AAA", "timestamp": 10_000.0},
        {"symbol": "AAA", "timestamp": 11_800.0},
        {"symbol": "AAA", "timestamp": 13_600.0},
        {"symbol": "BBB", "timestamp": 10_600.0},
        {"symbol": "BBB", "timestamp": 14_300.0},
    ]
    selected = sm.independent_confirmation_rows(rows)
    assert [(row["symbol"], row["timestamp"]) for row in selected] == [
        ("AAA", 10_000.0),
        ("BBB", 10_600.0),
        ("AAA", 13_600.0),
        ("BBB", 14_300.0),
    ], selected


def test_peer_ml_replay_requires_strictly_later_live_confirmation():
    from pathlib import Path
    import peer_ml_predictor as peer

    rows = [
        {
            "symbol": "OLD",
            "trading_date": "2026-08-28",
            "observation_source": "historical_replay",
            "timestamp": 1000.0,
            "label": 1,
        },
        {
            "symbol": "SAME",
            "trading_date": "2026-08-28",
            "observation_source": "live_scan",
            "timestamp": 2000.0,
            "label": 0,
        },
        {
            "symbol": "LATER",
            "trading_date": "2026-08-31",
            "observation_source": "live_scan",
            "timestamp": 3000.0,
            "label": 1,
        },
    ]
    context = peer._source_integrity_context(rows)
    assert context["replay_end_day"] == "2026-08-28", context
    assert [row["symbol"] for row in context["live_confirmation_rows"]] == [
        "LATER"
    ], context
    assert peer.MIN_LIVE_CONFIRMATION_SAMPLES >= 100
    assert peer.MIN_LIVE_CONFIRMATION_DAYS >= 5
    assert peer.MIN_LIVE_CONFIRMATION_CLASS_COUNT >= 15
    assert peer.MIN_LIVE_CONFIRMATION_SYMBOLS >= 15

    source = Path("peer_ml_predictor.py").read_text(encoding="utf-8")
    assert 'validation_status = "replay_validated_waiting_live"' in source
    assert '"replay_survivorship_limit": bool(replay_rows)' in source


def test_scanner_historical_validation_excludes_live_confirmation_pool():
    from pathlib import Path

    source = Path("scanner_ml_ranker.py").read_text(encoding="utf-8")
    assert "validation_rows = replay_rows if replay_rows else rows" in source
    assert "for i, row in enumerate(validation_rows)" in source
    assert '"historical_validation_source"' in source
    assert '"historical_replay" if replay_rows else "live_only"' in source
    # The later live holdout must remain a distinct pool.
    assert "live_confirmation_rows_raw = [" in source
    assert "independent_confirmation_rows(" in source
    assert "row[\"trading_date\"] > replay_end_day" in source


def test_scanner_replay_live_confirmation_gate_is_integrity_sized():
    import scanner_ml_ranker as sm

    assert sm.MIN_LIVE_CONFIRMATION_SAMPLES >= 100
    assert sm.MIN_LIVE_CONFIRMATION_DAYS >= 5
    assert sm.MIN_LIVE_CONFIRMATION_CLASS_COUNT >= 15
    assert sm.MIN_LIVE_CONFIRMATION_SYMBOLS >= 15
    assert sm.LIVE_CONFIRMATION_MIN_GAP_SECONDS >= 60 * 60


def test_validation_workflow_runs_before_merge_on_pull_requests():
    from pathlib import Path

    source = Path(".github/workflows/analyzer-v2-check.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request:" in source
    assert "branches: [main]" in source
    assert "python consistency_regression_check.py" in source


def test_offhours_workflow_runs_after_close_and_commits_separate_snapshot():
    from pathlib import Path

    source = Path(".github/workflows/offhours-timeframe-scan.yml").read_text(
        encoding="utf-8"
    )
    assert "cron: '15 22 * * 1-5'" in source
    assert "python offhours_timeframe_scan.py" in source
    assert "TRADIER_ACCESS_TOKEN" in source
    assert "scan_logs/offhours_timeframe_latest.json" in source
    assert "contents: write" in source
    assert "Run offhours scan smoke" in source
    assert "git pull --rebase origin main" in source
    assert "git push origin HEAD:main" in source


def test_prediction_tracker_uses_first_post_horizon_bar_with_three_minute_cap():
    import prediction_tracker as pt

    target = datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)
    bars = [
        {"t": "2026-08-28T14:59:00Z", "c": 9.9},
        {"t": "2026-08-28T15:03:00Z", "c": 10.3},
        {"t": "2026-08-28T15:04:00Z", "c": 10.4},
    ]
    assert pt.OUTCOME_MAX_BAR_DELAY_SECONDS == 180
    assert pt._first_close_at_or_after(bars, target) == 10.3
    assert pt._first_close_at_or_after(
        [{"t": "2026-08-28T14:59:00Z", "c": 9.9}],
        target,
    ) is None
    assert pt._first_close_at_or_after(
        [{"t": "2026-08-28T15:04:00Z", "c": 10.4}],
        target,
    ) is None


def test_same_ticker_ml_uses_clock_horizons_and_effective_samples():
    import ml_predictor as mp

    current = datetime(2026, 8, 28, 10, 0, tzinfo=ET)
    bars = [
        {"t": _iso(current + timedelta(minutes=29)), "c": 10.1},
        {"t": _iso(current + timedelta(minutes=30)), "c": 10.2},
        {"t": _iso(current + timedelta(minutes=31)), "c": 10.3},
        {"t": _iso(current + timedelta(minutes=60)), "c": 10.5},
    ]
    assert mp._close_at_clock_horizon(bars, current, ET, 30) == 10.2
    assert mp._close_at_clock_horizon(bars, current, ET, 60) == 10.5

    rows = [
        {"timestamp": 1000.0, "trading_date": "2026-08-28"},
        {"timestamp": 2000.0, "trading_date": "2026-08-28"},
        {"timestamp": 4600.0, "trading_date": "2026-08-28"},
        {"timestamp": 1000.0, "trading_date": "2026-08-29"},
    ]
    selected = mp._decorrelate_effective_rows(rows)
    assert [(r["trading_date"], r["timestamp"]) for r in selected] == [
        ("2026-08-28", 1000.0),
        ("2026-08-29", 1000.0),
        ("2026-08-28", 4600.0),
    ], selected
    assert mp.ML_EFFECTIVE_SAMPLE_GAP_SECONDS >= 60 * 60


def test_same_ticker_ml_requires_consolidated_live_and_history_for_validation():
    import ml_predictor as mp

    assert mp._consolidated_source("TRADIER CONSOLIDATED HISTORICAL")
    assert mp._consolidated_source("alpaca_sip_5min")
    assert not mp._consolidated_source("alpaca_iex")
    assert not mp._consolidated_source("mixed_iex_sip")

    assert mp._consolidated_live_metrics({
        "market_provider": "tradier",
        "live_feed": "TRADIER CONSOLIDATED",
    })
    assert not mp._consolidated_live_metrics({
        "market_provider": "alpaca",
        "live_feed": "IEX",
    })


def test_scanner_ml_excludes_non_consolidated_observations():
    import scanner_ml_ranker as sm

    payload = {"source": "live_scan"}
    assert sm._consolidated_observation_source(
        {
            "observation_source": "live_scan",
            "market_provider": "tradier",
            "live_feed": "consolidated",
        },
        payload,
    )
    assert not sm._consolidated_observation_source(
        {
            "observation_source": "live_scan",
            "market_provider": "alpaca",
            "live_feed": "iex",
        },
        payload,
    )
    assert sm._consolidated_observation_source(
        {
            "observation_source": "historical_replay",
            "liquidity_source": "historical_tradier_replay",
            "live_intraday_source": "tradier_historical_5min_open",
        },
        {"source": "historical_replay"},
    )


def test_scanner_actions_fail_closed_on_data_integrity():
    import stock_scanner as ss

    now_et = datetime(2026, 8, 31, 10, 0, tzinfo=ET)
    fresh = {
        "live_quote_source": "tradier_consolidated",
        "latest_trade_time": _iso(now_et - timedelta(seconds=20)),
        "latest_quote_time": _iso(now_et - timedelta(seconds=10)),
        "price": 10.0,
        "vwap": 9.9,
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "spread_pct": 0.4,
    }
    ok, reasons = ss._scanner_data_integrity(fresh, now_et)
    assert ok, reasons

    iex = dict(fresh)
    iex["live_quote_source"] = "alpaca_iex"
    ok, reasons = ss._scanner_data_integrity(iex, now_et)
    assert not ok
    assert any("not consolidated" in reason for reason in reasons)

    stale = dict(fresh)
    stale["latest_trade_time"] = _iso(now_et - timedelta(minutes=5))
    ok, reasons = ss._scanner_data_integrity(stale, now_et)
    assert not ok
    assert any("stale" in reason for reason in reasons)


def test_analyzer_entries_fail_closed_on_data_integrity():
    import analyzer_v2_integration as v2

    good = {
        "market_provider": "tradier",
        "live_feed": "TRADIER CONSOLIDATED",
        "trade_age_seconds": 20,
        "quote_age_seconds": 10,
        "price": 10.0,
        "vwap": 9.9,
        "momentum_5m": 1.0,
        "momentum_15m": 2.0,
        "spread_pct": 0.4,
    }
    assert v2._analyzer_live_data_integrity(good)["ok"] is True

    weak = dict(good)
    weak["live_feed"] = "IEX"
    weak["market_provider"] = "alpaca"
    weak["trade_age_seconds"] = 300
    result = v2._analyzer_live_data_integrity(weak)
    assert result["ok"] is False
    assert result["consolidated"] is False
    assert any("stale" in reason for reason in result["reasons"])


def test_historical_analogs_cannot_change_live_plan_geometry_or_scores():
    import copy
    import stock_analyzer as sa

    base = {
        "price": 10.0,
        "vwap": 9.8,
        "vwap_extension_pct": 2.04,
        "day_pct": 20.0,
        "momentum_5m": 1.2,
        "momentum_15m": 2.0,
        "volume_pace": 2.5,
        "spread_pct": 0.4,
        "score": 82.0,
        "atr_14": 0.8,
        "atr_14_pct": 8.0,
        "supports": [
            {"price": 9.2, "quality_score": 70, "quality": "STRONG", "side": "support"}
        ],
        "resistances": [
            {"price": 11.0, "quality_score": 75, "quality": "STRONG", "side": "resistance"}
        ],
        "impulse_pullback": {
            "detected": True,
            "impulse_low": 8.0,
            "impulse_high": 12.0,
            "impulse_move_pct": 50.0,
            "current_retracement_pct": 50.0,
            "bounce_recovery_pct": 10.0,
            "bounce_confirmed": True,
        },
        "bounce_sequence": {"detected": False, "completed_bounces": 0},
        "stair_step": {"detected": False},
        "run_exhaustion": {"score": 35.0},
        "liquidity": {"label": "HIGH", "avg_dollar_volume": 15_000_000},
        "news": [],
    }

    bullish_history = copy.deepcopy(base)
    bullish_history["historical_analogs"] = {
        "status": "ok",
        "samples": [{"d1": 100.0}, {"d1": 80.0}],
    }
    bullish_history["historical_setup"] = {
        "status": "ok",
        "sample_count": 100,
        "next_day_up_pct": 99.0,
        "median_mfe_1d": 150.0,
        "median_mfe_3d": 250.0,
        "intraday": {
            "median_impulse_retracement_pct": 25.0,
            "second_bounce_rate_pct": 99.0,
            "post_second_bounce_drop5_rate_pct": 1.0,
        },
    }

    bearish_history = copy.deepcopy(base)
    bearish_history["historical_analogs"] = {
        "status": "ok",
        "samples": [{"d1": -80.0}, {"d1": -90.0}],
    }
    bearish_history["historical_setup"] = {
        "status": "ok",
        "sample_count": 100,
        "next_day_up_pct": 1.0,
        "median_mfe_1d": 1.0,
        "median_mfe_3d": 1.0,
        "intraday": {
            "median_impulse_retracement_pct": 62.0,
            "second_bounce_rate_pct": 1.0,
            "post_second_bounce_drop5_rate_pct": 99.0,
        },
    }

    a = sa.build_trade_plan(bullish_history, datetime.now(timezone.utc))
    b = sa.build_trade_plan(bearish_history, datetime.now(timezone.utc))

    for key in ("preferred_plan", "status", "action", "confidence"):
        assert a.get(key) == b.get(key), (key, a.get(key), b.get(key))

    for family in ("pullback", "breakout", "selected"):
        pa = a.get(family) or {}
        pb = b.get(family) or {}
        for key in (
            "entry_low", "entry_high", "entry_mid", "stop",
            "target1", "target2", "stretch_target", "risk_reward",
        ):
            assert pa.get(key) == pb.get(key), (family, key, pa.get(key), pb.get(key))

    assert a.get("historical_research_only") is True
    assert b.get("historical_research_only") is True

    v2_source = __import__("pathlib").Path("analyzer_v2_integration.py").read_text(
        encoding="utf-8"
    )
    assert "history_points = 0.0" in v2_source
    assert "Historical bounce occurrence rates are reference context only" in v2_source
    assert "Historical behavior is displayed separately as research-only" in v2_source
    assert "Historical failure rates remain research-only" in v2_source
    assert "must not help an entry clear the safety gate" in v2_source


def test_advisory_model_percentages_are_explicit_and_neutral():
    import ml_ui

    advisory = {
        "status": "ok",
        "validated": False,
        "probability_pct": 72.0,
    }
    validated = {
        "status": "ok",
        "validated": True,
        "probability_pct": 72.0,
    }
    assert ml_ui._pct_value(advisory) == "ADVISORY · 72%"
    assert ml_ui._probability_class(advisory, high=60) == ""
    assert ml_ui._pct_value(validated) == "72%"
    assert ml_ui._probability_class(validated, high=60) == "good"


if __name__ == "__main__":
    tests = [
        test_analyzer_daily_history_prefers_tradier,
        test_analyzer_session_filter_uses_current_extended_session,
        test_analyzer_closed_preview_uses_latest_regular_session,
        test_analyzer_does_not_fake_extended_hours_volume_pace,
        test_intraday_calibration_excludes_offhours_and_weekends,
        test_timeframe_calibration_uses_one_latest_regular_row_per_ticker_day,
        test_prediction_tracker_mirrors_daily_timeframe_sampling,
        test_old_calibration_schema_is_rejected,
        test_outcome_tracker_runs_after_extended_hours,
        test_scanner_table_volume_pace_formatter_matches_column,
        test_combined_scanner_uses_display_volume_pace_source,
        test_scanner_timeframe_fit_separates_intraday_swing_and_longer_term,
        test_scanner_longer_term_fit_is_capped_when_history_is_sparse,
        test_scanner_timeframe_fit_never_changes_production_rank_fields,
        test_scanner_and_analyzer_scores_are_labeled_as_non_probabilities,
        test_scanner_ui_exposes_timeframe_filter_without_reranking,
        test_offhours_daily_context_builds_swing_longer_term_candidate_without_live_action,
        test_offhours_history_pool_is_price_band_balanced_and_not_only_daily_movers,
        test_scanner_ui_surfaces_completed_daily_discovery_when_market_closed,
        test_offhours_workflow_runs_after_close_and_commits_separate_snapshot,
        test_prediction_tracker_skips_closed_market_records,
        test_scanner_outcome_report_does_not_call_gross_returns_trade_wins,
        test_late_scanner_report_has_explicit_no_horizon_status,
        test_manual_scanner_refreshes_combined_candidates_after_success,
        test_v2_skips_alpaca_sip_probe_when_tradier_primary,
        test_v2_market_context_prefers_tradier_quotes,
        test_analyzer_health_warns_on_durable_sync_error,
        test_analyzer_tradier_does_not_block_on_alpaca_snapshot,
        test_analyzer_reports_actual_historical_provider,
        test_analyzer_prefers_tradier,
        test_analyzer_falls_back_cleanly,
        test_scanner_ml_version_gate,
        test_analyzer_calibration_version_gate,
        test_scanner_outcome_metadata,
        test_scanner_outcome_horizon_rejects_late_gap_bars,
        test_analyzer_outcome_horizon_rejects_late_gap_bars,
        test_live_confirmation_rows_do_not_double_count_overlapping_ticker_windows,
        test_scanner_outcomes_expose_deduplicated_actionable_events,
        test_scanner_historical_returns_are_causal_and_timestamp_matched,
        test_scanner_enrichment_pool_is_not_display_watchlist_truncated,
        test_scanner_snapshot_preserves_action_data_integrity_for_watch_alerts,
        test_scanner_latest_snapshot_write_is_atomic,
        test_scanner_and_analyzer_use_midpoint_spread_formula,
        test_offhours_outcomes_include_two_day_horizon,
        test_offhours_score_is_labeled_trend_candidate_score,
        test_uncapped_trend_candidate_score_is_preserved_in_outcome_cohort,
        test_combined_candidate_list_uses_shared_trade_horizon_filter,
        test_mixed_timeframe_is_labeled_multiple_timeframes,
        test_glass_theme_styles_selectboxes_and_trade_horizon,
        test_streamlit_version_is_pinned_for_ui_stability,
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
        test_analyzer_white_tables_are_collapsible,
        test_analyzer_page_leads_with_actionable_decision_hierarchy,
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
        test_analyzer_ml_walk_forward_never_splits_one_trading_day,
        test_peer_ml_replay_requires_strictly_later_live_confirmation,
        test_scanner_historical_validation_excludes_live_confirmation_pool,
        test_scanner_replay_live_confirmation_gate_is_integrity_sized,
        test_validation_workflow_runs_before_merge_on_pull_requests,
        test_prediction_tracker_uses_first_post_horizon_bar_with_three_minute_cap,
        test_same_ticker_ml_uses_clock_horizons_and_effective_samples,
        test_same_ticker_ml_requires_consolidated_live_and_history_for_validation,
        test_scanner_ml_excludes_non_consolidated_observations,
        test_scanner_actions_fail_closed_on_data_integrity,
        test_analyzer_entries_fail_closed_on_data_integrity,
        test_historical_analogs_cannot_change_live_plan_geometry_or_scores,
        test_advisory_model_percentages_are_explicit_and_neutral,
        test_analyzer_ml_validation_requires_probability_skill,
        test_impulse_detector_measures_fraction_of_run,
        test_entry_readiness_penalizes_unconfirmed_shallow_retrace,
        test_run_exhaustion_flags_rejected_mature_run,
        test_full_spectrum_exposes_all_scenarios,
        test_multi_bounce_detector_tracks_decay_and_lower_highs,
        test_multi_bounce_ignores_micro_wiggles_and_waits_for_distinct_second_swing,
        test_distinct_bounce_semantics_are_version_isolated_for_peer_ml,
        test_multi_bounce_full_spectrum_accepts_sequence_state,
        test_stair_step_detector_finds_higher_plateau_sequence,
        test_scanner_behavior_completed_bar_parity,
        test_scanner_behavior_detects_reclaim_acceleration_and_breakout,
        test_scanner_behavior_detects_failed_breakout,
        test_scanner_behavior_fields_survive_scan_logging,
        test_low_rr_repeat_bounce_cannot_replace_primary_plan,
        test_analyzer_bounce_progress_and_plan_change_are_explicit,
        test_analyzer_exposes_same_evidence_bars_for_visual_snapshots,
        test_analyzer_visual_specs_show_real_pattern_markers,
        test_analyzer_visuals_use_dark_high_contrast_theme,
        test_analyzer_visual_snapshots_are_collapsible_and_contextual,
        test_analyzer_long_context_text_is_collapsible,
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
        test_swing_feature_research_freezes_thresholds_before_confirmation,
        test_swing_feature_research_requires_holdout_confirmation,
        test_swing_feature_research_preserves_market_regime_labels,
        test_live_swing_research_flags_match_frozen_rules,
        test_live_swing_research_flags_never_change_scores,
        test_live_swing_research_calibration_dedupes_ticker_day,
        test_swing_research_live_context_is_not_historical_parity,
        test_swing_research_calibration_excludes_wrong_context,
        test_scanner_visibly_marks_stale_snapshot,
        test_scanner_ui_accepts_tradier_without_alpaca_credentials,
        test_live_scanner_matches_scheduled_tradier_discovery,
        test_swing_research_ui_disclaims_historical_probability,
        test_legacy_analyzer_entrypoint_cannot_drift,
        test_monday_readiness_blocks_stale_scan_handoffs,
        test_live_scanner_uses_two_minute_cadence,
        test_actionable_momentum_alert_requires_existing_strong_scanner_state,
        test_high_score_pullback_watch_requires_strong_score_and_trusted_data,
        test_high_score_pullback_watch_alert_is_state_deduplicated,
        test_momentum_alert_only_fires_when_symbol_newly_enters_ready_state,
        test_combined_app_keeps_one_async_scanner_loop_across_views,
        test_momentum_alert_ui_has_in_app_and_optional_browser_notifications,
        test_scanner_runtime_async_start_is_nonblocking_and_lock_safe,
        test_scanner_runtime_timeout_releases_shared_lock,
        test_scanner_runtime_recovers_stale_lock_after_crash,
        test_two_minute_runtime_health_flags_tight_and_overrun_scans,
        test_momentum_alert_can_realert_only_after_leaving_ready_state,
        test_analyzer_live_test_status_exposes_tracking_health,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL CONSISTENCY REGRESSION CHECKS PASSED")
