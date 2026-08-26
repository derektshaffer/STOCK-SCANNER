from datetime import datetime, timedelta, timezone

from ml_predictor import predict_ml


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _expanded_history_fetch(sa, symbol, timeframe, start, end, limit=10000):
    """Fetch a deeper 5-minute history for ML without one oversized request.

    ML v1 originally asked for ~95 calendar days. Some tickers only produced a
    few dozen usable labeled observations after warm-up/future-window filters.
    For 5-minute ML data we expand to ~240 calendar days and request it in
    40-day chunks, then de-duplicate by timestamp. Other timeframes keep the
    analyzer's normal historical fetch behavior.
    """
    if timeframe != "5Min":
        return sa.try_sip_delayed_bars(symbol, timeframe, start, end, limit)

    expanded_start = min(start, end - timedelta(days=240))
    cursor = expanded_start
    step = timedelta(days=40)
    merged = {}
    sources = []

    while cursor < end:
        chunk_end = min(end, cursor + step)
        try:
            chunk, source = sa.try_sip_delayed_bars(
                symbol,
                timeframe,
                cursor,
                chunk_end,
                10000,
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


def install_ml_analysis(sa):
    """Wrap stock_analyzer.analyze with the experimental ML v1 layer."""
    if hasattr(sa, "_ml_enhanced_analyze"):
        return sa._ml_enhanced_analyze

    base_analyze = sa.analyze

    def enhanced_analyze(symbol):
        metrics = base_analyze(symbol)
        now = datetime.now(timezone.utc)

        def ml_fetch_bars(sym, timeframe, start, end, limit=10000):
            return _expanded_history_fetch(
                sa,
                sym,
                timeframe,
                start,
                end,
                limit,
            )

        try:
            ml = predict_ml(
                symbol=symbol,
                now=now,
                metrics=metrics,
                fetch_bars=ml_fetch_bars,
                et=sa.ET,
            )
        except Exception as exc:
            ml = {
                "status": "unavailable",
                "models": {},
                "validation_gate": "ADVISORY ONLY",
                "gate_passed": False,
                "error": str(exc)[:180],
            }

        metrics["ml_prediction"] = ml

        # Validation gate: only a model that beats naive baselines on unseen,
        # chronological walk-forward samples may influence confidence. It never
        # overrides the rule-based entry/stop/target decision in ML v1.
        plan = metrics.get("trade_plan") or {}
        if plan and ml.get("status") == "ok" and ml.get("gate_passed"):
            edge = _num(ml.get("ml_edge_score"))
            if edge is not None:
                adjustment = _clamp((edge - 50.0) * 0.16, -6.0, 6.0)
                confidence = float(plan.get("confidence") or metrics.get("score") or 50)
                confidence = int(round(_clamp(confidence + adjustment, 0, 95)))
                plan["confidence"] = confidence
                plan["confidence_label"] = (
                    "HIGH" if confidence >= 75 else
                    "MODERATE" if confidence >= 58 else
                    "LOW"
                )
                reasons = list(plan.get("reasons") or [])
                if edge >= 65:
                    reasons.insert(0, "Validated ML v1 probabilities support the current setup.")
                elif edge <= 45:
                    reasons.insert(0, "Validated ML v1 probabilities argue for extra caution.")
                else:
                    reasons.insert(0, "Validated ML v1 probabilities are mixed.")
                plan["reasons"] = reasons
                plan["ml_confidence_adjustment"] = round(adjustment, 1)
                metrics["trade_plan"] = plan

        return metrics

    sa._ml_enhanced_analyze = enhanced_analyze
    sa.analyze = enhanced_analyze
    return enhanced_analyze
