from datetime import datetime, timedelta, timezone

from historical_patterns import analyze_historical_patterns
from analyzer_history_cache import load_deep_5m_history, filter_history_rows


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _expanded_history_fetch(sa, symbol, timeframe, start, end, limit=10000):
    """Fetch deeper historical data without silently hitting Alpaca row caps."""
    if timeframe == "1Day":
        expanded_start = min(start, end - timedelta(days=900))
        return sa.try_sip_delayed_bars(symbol, timeframe, expanded_start, end, 1000)

    if timeframe != "5Min":
        return sa.try_sip_delayed_bars(symbol, timeframe, start, end, limit)

    expanded_start = min(start, end - timedelta(days=540))
    rows, source = load_deep_5m_history(
        symbol,
        end=end,
        days=540,
        step_days=45,
        fetch_bars=lambda sym, tf, chunk_start, chunk_end, chunk_limit: sa.try_sip_delayed_bars(
            sym, tf, chunk_start, chunk_end, chunk_limit
        ),
    )
    return filter_history_rows(rows, expanded_start, end), source


def install_historical_analysis(sa):
    """Return an analyze() function enhanced with same-ticker setup history.

    The installer is idempotent, so it is safe on Streamlit reruns.
    """
    if hasattr(sa, "_historical_enhanced_analyze"):
        return sa._historical_enhanced_analyze

    base_analyze = getattr(sa, "_base_analyze_for_history", None) or sa.analyze
    sa._base_analyze_for_history = base_analyze
    sa.ANALYZER_ENGINE_VERSION = "trade-plan-v7-repeat-bounce-stair"

    def enhanced_analyze(symbol):
        metrics = base_analyze(symbol)
        old_score = float(metrics.get("score") or 50)
        metrics["technical_score_before_history"] = round(old_score, 1)
        now = datetime.now(timezone.utc)

        gap_pct = None
        day_open = _num(metrics.get("session_open"))
        prev_close = _num(metrics.get("prev_close"))
        if day_open and prev_close:
            gap_pct = sa.pct(day_open, prev_close)
        metrics["gap_pct"] = round(gap_pct, 2) if gap_pct is not None else None

        try:
            setup = analyze_historical_patterns(
                symbol=symbol,
                now=now,
                current_day_pct=metrics.get("day_pct"),
                current_gap_pct=gap_pct,
                current_volume_pace=metrics.get("volume_pace"),
                fetch_bars=lambda sym, timeframe, start, end, limit=10000: _expanded_history_fetch(
                    sa, sym, timeframe, start, end, limit
                ),
                et=sa.ET,
            )
        except Exception as exc:
            setup = {
                "status": "unavailable",
                "sample_count": 0,
                "matches": [],
                "error": str(exc)[:160],
            }

        metrics["historical_setup"] = setup
        hist = metrics.get("historical_analogs")
        if isinstance(hist, dict):
            hist["setup_patterns"] = setup

        # Integrity-first policy: completed historical analogs are research
        # context only until their live predictive value is independently
        # validated. They must not change today's production score, grade,
        # entry geometry, plan family, confidence, or action.
        metrics["historical_score_adjustment"] = 0.0
        metrics["historical_production_influence"] = False
        metrics["historical_policy"] = "research_only_until_validated"

        plan = metrics.get("trade_plan") or {}
        if plan:
            plan["historical_setup"] = setup
            plan["historical_production_influence"] = False
            plan["historical_policy"] = "research_only_until_validated"

            research_notes = []
            hist_retrace = _num(
                (setup.get("intraday") or {}).get(
                    "median_impulse_retracement_pct"
                )
            )
            if hist_retrace is not None:
                research_notes.append(
                    f"Research only: same-ticker impulse history had a median "
                    f"retracement near {hist_retrace:.0f}% before later bounce attempts."
                )
            if setup.get("bias_label") in {"BULLISH", "BEARISH"}:
                research_notes.append(
                    "Research only: completed historical setup matches currently "
                    f"lean {str(setup.get('bias_label')).lower()}."
                )
            failure = _num(setup.get("breakout_failure_pct"))
            follow = _num(setup.get("breakout_follow_through_pct"))
            if (
                setup.get("status") == "ok"
                and int(setup.get("breakout_test_count") or 0) >= 3
                and failure is not None
                and failure >= 60
                and (follow is None or follow <= 45)
            ):
                research_notes.append(
                    "Research only: prior matched sessions had an elevated "
                    "breakout-failure rate."
                )
            plan["historical_research_notes"] = research_notes[:4]
            metrics["trade_plan"] = plan

        return metrics

    sa._historical_enhanced_analyze = enhanced_analyze
    sa.analyze = enhanced_analyze
    return enhanced_analyze
