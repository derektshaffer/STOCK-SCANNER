from datetime import datetime, timedelta, timezone

from historical_patterns import analyze_historical_patterns


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
    cursor = expanded_start
    step = timedelta(days=45)
    merged = {}
    sources = []

    while cursor < end:
        chunk_end = min(end, cursor + step)
        try:
            chunk, source = sa.try_sip_delayed_bars(
                symbol, timeframe, cursor, chunk_end, 10000
            )
        except Exception:
            chunk, source = [], "unavailable"

        if source and source not in sources:
            sources.append(source)
        for bar in chunk or []:
            ts = str(bar.get("t") or "")
            if ts:
                merged[ts] = bar
        cursor = chunk_end

    rows = [merged[k] for k in sorted(merged)]
    return rows, " + ".join(sources) if sources else "unavailable"


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
        try:
            snap = sa.snapshot(symbol.upper().strip(), sa.LIVE_FEED)
            day = snap.get("dailyBar") or {}
            day_open = _num(day.get("o"))
            prev_close = _num(metrics.get("prev_close"))
            if day_open and prev_close:
                gap_pct = sa.pct(day_open, prev_close)
        except Exception:
            pass
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

        # Historical setup matching affects the actual setup score.
        new_score = old_score
        score_reasons = list(metrics.get("score_reasons") or [])
        if setup.get("status") == "ok" and int(setup.get("sample_count") or 0) >= 5:
            bias = _num(setup.get("bias_score"))
            if bias is not None:
                new_score += _clamp(bias * 0.35, -7, 7)
                if bias >= 6:
                    score_reasons.append("bullish same-ticker setup history")
                elif bias <= -6:
                    score_reasons.append("bearish same-ticker setup history")

            failure = _num(setup.get("breakout_failure_pct"))
            follow = _num(setup.get("breakout_follow_through_pct"))
            if failure is not None and failure >= 65 and (follow is None or follow < 45):
                new_score -= 3
                score_reasons.append("historical breakout failure risk")

        new_score = round(_clamp(new_score, 0, 100), 1)
        metrics["score"] = new_score
        metrics["historical_score_adjustment"] = round(new_score - old_score, 1)
        metrics["score_reasons"] = score_reasons

        day_pct = _num(metrics.get("day_pct")) or 0
        vwap_ext = _num(metrics.get("vwap_extension_pct")) or 0
        chase = max(0, day_pct - 25) * 0.16 + max(0, vwap_ext - 5) * 0.5
        metrics["grade"] = "A" if new_score >= 78 else "B" if new_score >= 65 else "C" if new_score >= 52 else "REJECT"
        metrics["entry_quality"] = "FAVORABLE" if new_score >= 72 and chase < 10 else "WAIT / CONFIRM" if new_score >= 55 else "POOR / HIGH RISK"

        # Rebuild the trade plan now that same-ticker pullback history is
        # available. build_trade_plan can use the stock's historical median
        # impulse retracement to shift the preferred pullback zone rather than
        # assuming every ticker should retrace exactly the same amount.
        try:
            metrics["trade_plan"] = sa.build_trade_plan(metrics, now)
        except Exception:
            pass

        plan = metrics.get("trade_plan") or {}
        if plan:
            plan["historical_setup"] = setup
            confidence = float(plan.get("confidence") or old_score)
            confidence += new_score - old_score

            failure = _num(setup.get("breakout_failure_pct"))
            follow = _num(setup.get("breakout_follow_through_pct"))
            elevated_breakout_risk = (
                setup.get("status") == "ok"
                and int(setup.get("breakout_test_count") or 0) >= 3
                and failure is not None
                and failure >= 60
                and (follow is None or follow <= 45)
            )
            if elevated_breakout_risk:
                confidence -= 5

            confidence = int(round(_clamp(confidence, 0, 95)))
            plan["confidence"] = confidence
            plan["confidence_label"] = "HIGH" if confidence >= 75 else "MODERATE" if confidence >= 58 else "LOW"

            reasons = list(plan.get("reasons") or [])
            hist_retrace = _num((setup.get("intraday") or {}).get("median_impulse_retracement_pct"))
            if hist_retrace is not None:
                reasons.insert(
                    0,
                    f"Same-ticker impulse history favors roughly a {hist_retrace:.0f}% retracement before the next bounce attempt."
                )
            if setup.get("bias_label") == "BULLISH":
                reasons.insert(0, "Same-ticker historical setup matches lean bullish.")
            elif setup.get("bias_label") == "BEARISH":
                reasons.insert(0, "Same-ticker historical setup matches lean bearish, so confirmation matters more.")
            if elevated_breakout_risk:
                reasons.append("Historical breakout-failure rate is elevated; prefer a hold/retest over a quick poke above resistance.")
                if plan.get("status") == "ENTRY AVAILABLE" and plan.get("preferred_plan") == "breakout":
                    plan["status"] = "WAIT"
                    plan["action"] = "WAIT FOR BREAKOUT TO HOLD — history shows frequent failures"
            plan["reasons"] = reasons

            plan["method_note"] = (
                "Rule-based long momentum decision support using VWAP, support/resistance, ATR, "
                "momentum, volume pace, spread/liquidity, same-ticker spike analogs plus setup-"
                "matched gap/run-vs-fade behavior, breakout failure/follow-through, VWAP reclaim "
                "tendencies, impulse retracement, dedicated Bounce #2/#3+ scalp geometry, historical "
                "late-bounce falloff, multi-session stair-step / plateau behavior, time-of-day behavior "
                "and catalyst context. Targets are scenarios, not guarantees."
            )
            metrics["trade_plan"] = plan

        return metrics

    sa._historical_enhanced_analyze = enhanced_analyze
    sa.analyze = enhanced_analyze
    return enhanced_analyze
