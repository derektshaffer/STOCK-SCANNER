from datetime import datetime, timedelta, timezone

from ml_predictor import predict_ml
from peer_ml_predictor import predict_peer_ml


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _validated_edge_only(ml):
    """Replace the headline ML edge with a validated-model-only consensus.

    Individual advisory/unvalidated probabilities stay visible in the UI, but
    they are excluded from the headline edge and therefore cannot influence the
    trade-plan confidence adjustment. This keeps the prominent score tied only
    to models that beat their walk-forward validation gate.
    """
    if not isinstance(ml, dict) or ml.get("status") != "ok":
        return ml

    models = ml.get("models") or {}
    weights = {
        "target_before_stop": 0.24,
        "higher_60": 0.14,
        "higher_30": 0.08,
        "breakout_hold": 0.07,
        "reversal_30": 0.11,
        "repeat_bounce_30": 0.11,
        "new_high_60": 0.07,
        "post_bounce_failure_60": 0.10,
        "stair_reacceleration_60": 0.08,
    }
    used = []
    weighted = []

    for name, weight in weights.items():
        model = models.get(name) or {}
        if model.get("status") != "ok" or not model.get("validated"):
            continue
        if name == "breakout_hold" and not ml.get("breakout_relevant"):
            continue
        if name in {"repeat_bounce_30", "new_high_60"} and not ml.get("bounce_relevant"):
            continue
        if name == "post_bounce_failure_60" and not ml.get("mature_bounce_relevant"):
            continue
        if name == "stair_reacceleration_60" and not ml.get("stair_relevant"):
            continue
        probability = _num(model.get("probability_pct"))
        if probability is None:
            continue
        if name in {"reversal_30", "post_bounce_failure_60"}:
            probability = 100.0 - probability
        used.append(name)
        weighted.append((probability, weight))

    if weighted:
        total_weight = sum(weight for _, weight in weighted)
        edge = sum(probability * weight for probability, weight in weighted) / total_weight
        if edge >= 65:
            lean = "BULLISH / SUPPORTS ENTRY"
        elif edge <= 45:
            lean = "BEARISH / CAUTION"
        else:
            lean = "MIXED"
    else:
        edge = None
        lean = "NO VALIDATED EDGE"

    count = len(used)
    coverage = (
        "NONE" if count == 0 else
        "LIMITED" if count == 1 else
        "MODERATE" if count == 2 else
        "STRONG"
    )

    ml["ml_edge_score"] = round(edge, 1) if edge is not None else None
    ml["ml_lean"] = lean
    ml["validated_edge_models"] = used
    ml["validated_edge_model_count"] = count
    ml["ml_edge_coverage"] = coverage
    ml["edge_method"] = "validated_models_only"
    return ml


def _expanded_history_fetch(sa, symbol, timeframe, start, end, limit=10000):
    """Fetch a deeper 5-minute history for ML without one oversized request.

    ML v1 originally asked for ~95 calendar days. Some tickers only produced a
    few dozen usable labeled observations after warm-up/future-window filters.
    For 5-minute ML data we expand to ~365 calendar days and request it in
    40-day chunks, then de-duplicate by timestamp. Other timeframes keep the
    analyzer's normal historical fetch behavior.
    """
    if timeframe != "5Min":
        return sa.try_sip_delayed_bars(symbol, timeframe, start, end, limit)

    expanded_start = min(start, end - timedelta(days=365))
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

        ml = _validated_edge_only(ml)

        # Keep the same-ticker model primary. The peer layer is trained
        # separately on behaviorally similar setups from OTHER symbols, now
        # including impulse/retracement/bounce structure when those historical
        # replay features are available.
        same_ticker_edge = _num(ml.get("ml_edge_score"))
        same_ticker_gate = bool(ml.get("gate_passed"))
        try:
            peer = predict_peer_ml(
                symbol=symbol,
                now=now,
                metrics=metrics,
                et=sa.ET,
            )
        except Exception as exc:
            peer = {
                "status": "unavailable",
                "validated": False,
                "version": "analyzer-peer-v1",
                "error": str(exc)[:180],
            }

        ml["peer_model"] = peer
        ml["same_ticker_edge_score"] = (
            round(same_ticker_edge, 1)
            if same_ticker_edge is not None
            else None
        )
        ml["peer_edge_score"] = _num(peer.get("peer_edge_score"))
        ml["peer_probability_pct"] = _num(peer.get("probability_pct"))
        ml["peer_validated"] = bool(peer.get("validated"))
        reversal_model=(ml.get("models") or {}).get("reversal_30") or {}
        ml["reversal_30_probability_pct"] = _num(reversal_model.get("probability_pct"))
        ml["reversal_30_validated"] = bool(reversal_model.get("validated"))
        repeat_bounce=(ml.get("models") or {}).get("repeat_bounce_30") or {}
        new_high=(ml.get("models") or {}).get("new_high_60") or {}
        ml["repeat_bounce_30_probability_pct"] = _num(repeat_bounce.get("probability_pct"))
        ml["repeat_bounce_30_validated"] = bool(repeat_bounce.get("validated"))
        ml["new_high_60_probability_pct"] = _num(new_high.get("probability_pct"))
        ml["new_high_60_validated"] = bool(new_high.get("validated"))
        post_failure=(ml.get("models") or {}).get("post_bounce_failure_60") or {}
        stair_reaccel=(ml.get("models") or {}).get("stair_reacceleration_60") or {}
        ml["post_bounce_failure_60_probability_pct"] = _num(post_failure.get("probability_pct"))
        ml["post_bounce_failure_60_validated"] = bool(post_failure.get("validated"))
        ml["stair_reacceleration_60_probability_pct"] = _num(stair_reaccel.get("probability_pct"))
        ml["stair_reacceleration_60_validated"] = bool(stair_reaccel.get("validated"))
        ml["peer_blend_weight_pct"] = 0
        ml["hybrid_ml_edge_score"] = (
            round(same_ticker_edge, 1)
            if same_ticker_edge is not None
            else None
        )

        # A validated peer model can contribute at most 30% and only when the
        # existing same-ticker gate has already passed. If the stock-specific
        # model has not proved itself, peer behavior remains visible/advisory
        # and cannot move plan confidence.
        peer_edge = _num(peer.get("peer_edge_score"))
        if (
            same_ticker_gate
            and same_ticker_edge is not None
            and bool(peer.get("validated"))
            and peer_edge is not None
        ):
            hybrid_edge = 0.70 * same_ticker_edge + 0.30 * peer_edge
            ml["hybrid_ml_edge_score"] = round(hybrid_edge, 1)
            ml["ml_edge_score"] = round(hybrid_edge, 1)
            ml["peer_blend_weight_pct"] = 30
            ml["edge_method"] = "same_ticker_70_peer_30"
            ml["ml_lean"] = (
                "BULLISH / SUPPORTS ENTRY"
                if hybrid_edge >= 65
                else "BEARISH / CAUTION"
                if hybrid_edge <= 45
                else "MIXED"
            )
        elif peer_edge is not None:
            ml["edge_method"] = str(ml.get("edge_method") or "validated_models_only") + "_peer_advisory"

        ml["version"] = "ml-v2.0-confirmed-multisession-peer"
        metrics["ml_prediction"] = ml

        # Validation gate: only a model that beats naive baselines on unseen,
        # chronological walk-forward samples may influence confidence. The edge
        # used here is now composed ONLY of validated models. ML v1 still never
        # overrides the rule-based entry/stop/target decision.
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
                    reasons.insert(
                        0,
                        "Validated hybrid ML probabilities support the current setup."
                        if ml.get("peer_blend_weight_pct")
                        else "Validated same-ticker ML probabilities support the current setup."
                    )
                elif edge <= 45:
                    reasons.insert(
                        0,
                        "Validated hybrid ML probabilities argue for extra caution."
                        if ml.get("peer_blend_weight_pct")
                        else "Validated same-ticker ML probabilities argue for extra caution."
                    )
                else:
                    reasons.insert(
                        0,
                        "Validated hybrid ML probabilities are mixed."
                        if ml.get("peer_blend_weight_pct")
                        else "Validated same-ticker ML probabilities are mixed."
                    )
                plan["reasons"] = reasons
                plan["ml_confidence_adjustment"] = round(adjustment, 1)
                metrics["trade_plan"] = plan

        return metrics

    sa._ml_enhanced_analyze = enhanced_analyze
    sa.analyze = enhanced_analyze
    return enhanced_analyze
