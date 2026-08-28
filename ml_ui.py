def _pct_value(model):
    if not model or model.get("status") != "ok":
        return "—"
    p = model.get("probability_pct")
    return f"{p:.0f}%" if p is not None else "—"


def _validation_note(model):
    if not model:
        return "not available"
    status = model.get("status")
    if status != "ok":
        if status == "insufficient_samples":
            n = int(model.get("samples") or 0)
            positives = int(model.get("positives") or 0)
            negatives = int(model.get("negatives") or max(0, n - positives))
            outcomes = model.get("outcome_summary") or {}
            unresolved = int(outcomes.get("unresolved") or 0)
            ambiguous = int(outcomes.get("ambiguous") or 0)
            if model.get("label") == "target_before_stop":
                extra = ""
                if unresolved or ambiguous:
                    extra = f" · {unresolved} unresolved / {ambiguous} ambiguous excluded"
                return (
                    "insufficient outcome balance"
                    f" · {positives} target-first / {negatives} stop-first"
                    + extra
                )
            if n >= 180:
                return (
                    "insufficient outcome balance"
                    + (f" · {positives} positive / {negatives} negative" if n else "")
                )
        n = model.get("samples") or model.get("validation_samples")
        return f"{status.replace('_', ' ')}" + (f" · n={n}" if n else "")
    acc = model.get("walk_forward_accuracy_pct")
    base = model.get("baseline_accuracy_pct")
    n = model.get("validation_samples")
    flag = "validated" if model.get("validated") else "advisory"
    if acc is None:
        return flag
    return f"{flag} · WF {acc:.0f}% vs {base:.0f}% baseline · n={n}"


def render_ml_prediction(st, pd, result, card):
    ml = result.get("ml_prediction") or {}

    version = str(ml.get("version") or "ml-v1").replace("ml-", "ML ")
    heading_cols = st.columns([8, 1.25], vertical_alignment="center")
    with heading_cols[0]:
        st.markdown(
            f'<div class="section">Machine-learning probability model '
            f'<span style="font-size:12px;color:#91a7c2">{version}</span></div>',
            unsafe_allow_html=True,
        )
    ml_details_slot = heading_cols[1]

    if ml.get("status") != "ok":
        if ml.get("status") == "insufficient_history":
            st.info("ML v1.1 does not have enough 5-minute same-ticker history to train a reliable model yet.")
        else:
            detail = ml.get("error")
            st.caption("ML v1.1 is temporarily unavailable." + (f" {detail}" if detail else ""))
        return

    models = ml.get("models") or {}
    target = models.get("target_before_stop") or {}
    m30 = models.get("higher_30") or {}
    m60 = models.get("higher_60") or {}
    breakout = models.get("breakout_hold") or {}
    reversal = models.get("reversal_30") or {}

    cols = st.columns(7)
    edge = ml.get("ml_edge_score")
    edge_count = int(ml.get("validated_edge_model_count") or 0)
    coverage = ml.get("ml_edge_coverage") or "NONE"
    gate = ml.get("validation_gate") or "ADVISORY ONLY"
    peer = ml.get("peer_model") or {}
    peer_blend = int(ml.get("peer_blend_weight_pct") or 0)
    edge_class = "good" if edge is not None and edge >= 65 else "bad" if edge is not None and edge <= 45 else "warn"

    if edge is None:
        edge_note = "no validated same-ticker models yet · advisory predictions excluded"
    else:
        model_word = "model" if edge_count == 1 else "models"
        edge_note = f'{ml.get("ml_lean") or "MIXED"} · {edge_count} validated {model_word} · {coverage.lower()} coverage'
        if peer_blend:
            edge_note += f" · peer layer {peer_blend}%"
        if gate != "PASSED":
            edge_note += " · advisory only"

    card(
        cols[0],
        "HYBRID ML EDGE" if peer_blend else "VALIDATED ML EDGE",
        f"{edge:.0f} / 100" if edge is not None else "—",
        edge_note,
        edge_class,
        "The model's combined stock-specific directional score. Above 50 leans bullish; below 50 leans bearish. Only validated models count toward this score.",
    )
    target_note = _validation_note(target)
    if target.get("horizon") == "same_session" and target.get("status") == "ok":
        target_note += " · same-session first touch"
    card(
        cols[1],
        "TARGET 1 BEFORE STOP",
        _pct_value(target),
        target_note,
        "good" if (target.get("probability_pct") or 0) >= 65 else "warn",
        "Estimated probability that the analyzer's first upside target is reached before its stop during the same trading session.",
    )
    card(
        cols[2],
        "30M HIGHER",
        _pct_value(m30),
        _validation_note(m30),
        "good" if (m30.get("probability_pct") or 0) >= 60 else "warn",
        "Estimated probability that the stock price will be higher than it is now 30 minutes from the current snapshot.",
    )
    card(
        cols[3],
        "60M HIGHER",
        _pct_value(m60),
        _validation_note(m60),
        "good" if (m60.get("probability_pct") or 0) >= 60 else "warn",
        "Estimated probability that the stock price will be higher than it is now 60 minutes from the current snapshot.",
    )
    card(
        cols[4],
        "BREAKOUT HOLD",
        _pct_value(breakout) if ml.get("breakout_relevant") else "N/A",
        _validation_note(breakout) if ml.get("breakout_relevant") else "not near breakout trigger",
        "good" if (breakout.get("probability_pct") or 0) >= 60 else "warn",
        "When price is near a breakout level, this estimates whether the breakout will hold rather than quickly fail back below resistance.",
    )
    card(
        cols[5],
        "30M REVERSAL RISK",
        _pct_value(reversal),
        _validation_note(reversal),
        "bad" if (reversal.get("probability_pct") or 0) >= 60 else "good" if reversal.get("probability_pct") is not None and (reversal.get("probability_pct") or 0) <= 40 else "warn",
        "Estimated chance that a meaningful downside reversal occurs before another continuation push during the next 30 minutes.",
    )
    card(
        cols[6],
        "VALIDATION",
        f'{ml.get("validated_models", 0)} / 5',
        "models passed walk-forward gate",
        "good" if ml.get("gate_passed") else "warn",
        "How many same-ticker ML models passed the walk-forward validation gate. Unvalidated models remain advisory and do not get full decision weight.",
    )

    peer_cols = st.columns([1.35, 1.0, 3.65])
    peer_probability = peer.get("probability_pct")
    peer_edge = peer.get("peer_edge_score")
    peer_validated = bool(peer.get("validated"))
    peer_status = str(peer.get("status") or "unavailable").replace("_", " ")
    peer_note = (
        f"{'validated' if peer_validated else peer_status} · "
        f"n={int(peer.get('samples') or 0):,} similar setups · "
        f"{int(peer.get('peer_symbols') or 0)} other tickers"
    )
    card(
        peer_cols[0],
        "SIMILAR-TICKER +3% / 60M",
        f"{float(peer_probability):.1f}%" if peer_probability is not None else "—",
        peer_note,
        "good" if peer_validated and (peer_edge or 50) >= 58 else "warn",
        "Among historically similar setups from other tickers, the percentage that gained at least 3% over the following 60 minutes.",
    )
    card(
        peer_cols[1],
        "PEER EDGE",
        f"{float(peer_edge):.0f} / 100" if peer_edge is not None else "—",
        (
            f"{peer_blend}% of headline when blended"
            if peer_blend
            else "advisory until both gates pass"
        ),
        "good" if peer_validated and (peer_edge or 50) >= 58 else "bad" if peer_validated and (peer_edge or 50) <= 42 else "warn",
        "A normalized 0-100 score for how favorable the similar-ticker historical cohort is. It stays advisory until the peer and same-ticker validation gates both pass.",
    )
    with peer_cols[2]:
        top_peers = peer.get("top_peer_symbols") or []
        peer_names = ", ".join(
            str(item.get("symbol"))
            for item in top_peers[:6]
            if item.get("symbol")
        )
        if peer_names:
            st.caption(
                "Peer cohort emphasizes behaviorally similar momentum setups from other symbols. "
                f"Most represented peers: {peer_names}."
            )
        else:
            st.caption(
                "Peer ML is collecting a sufficiently large, diverse cohort of similar momentum setups."
            )

    st.caption(
        "Same-ticker ML stays primary. A peer model can contribute at most 30% of the headline edge, "
        "and only after the stock's own same-ticker validation gate and the peer model's separate "
        "whole-trading-day validation gate have both passed."
    )

    with ml_details_slot.popover("ML details"):
        st.write(
            "**What it predicts:** Whether Target 1 is reached before the stop during the "
            "rest of the same trading session, whether price is higher in 30 and 60 minutes, "
            "and breakout hold probability when price is near the breakout trigger."
        )
        target_source = target.get("target_source") or "Target 1"
        outcomes = target.get("outcome_summary") or {}
        st.caption(
            f"Target 1 source: {target_source}. Same-session T1 training uses only decisive "
            f"first-touch outcomes: {int(outcomes.get('target_wins') or 0)} target-first, "
            f"{int(outcomes.get('stop_first') or 0)} stop-first; "
            f"{int(outcomes.get('unresolved') or 0)} unresolved and "
            f"{int(outcomes.get('ambiguous') or 0)} ambiguous observations are excluded."
        )
        st.write(
            "**How it is trained:** Same-ticker 5-minute bars are converted into historical "
            "snapshots using only information that existed at each snapshot: day move, gap, "
            "VWAP extension, 5/15/30-minute momentum, volume pace, distance from the high, "
            "ATR, time of day, range position and intraday range."
        )
        st.write(
            "**Leakage protection:** Validation is expanding-window/walk-forward: older samples "
            "train the model and later samples are kept unseen for testing. A model must beat a "
            "naive baseline and meet a Brier-score threshold before it is marked validated."
        )
        st.write(
            "**Headline edge rule:** Same-ticker models remain primary. Only validated same-ticker "
            "models contribute to the stock-specific edge. The separate similar-ticker peer model "
            "can contribute up to 30% only when it passes whole-trading-day validation AND the "
            "same-ticker validation gate has already passed. Peer-only evidence stays advisory."
        )
        st.write(
            "**Peer model:** It searches historical momentum observations from other tickers for "
            "setups resembling the current stock in price band, liquidity, day move, 5/15-minute "
            "momentum, volume pace, VWAP extension, distance from the high, intraday range, time of day, "
            "impulse size, retracement depth, bounce recovery and pullback-volume behavior. "
            "Its target is a +3% or greater move over the next 60 minutes."
        )

        rows = []
        labels = {
            "target_before_stop": "Target 1 before stop",
            "higher_30": "30m higher",
            "higher_60": "60m higher",
            "breakout_hold": "Breakout hold",
            "reversal_30": "30m reversal risk",
        }
        for key, label in labels.items():
            m = models.get(key) or {}
            rows.append(
                {
                    "Prediction": label,
                    "Probability": m.get("probability_pct"),
                    "Training samples": m.get("samples"),
                    "Walk-forward samples": m.get("validation_samples"),
                    "WF accuracy %": m.get("walk_forward_accuracy_pct"),
                    "Naive baseline %": m.get("baseline_accuracy_pct"),
                    "Accuracy edge %": m.get("accuracy_edge_pct"),
                    "Brier": m.get("brier"),
                    "Validated": bool(m.get("validated")),
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        validated_models = [
            (key, model)
            for key, model in models.items()
            if model.get("status") == "ok" and model.get("validated")
        ]
        if validated_models:
            features = []
            for key, model in validated_models:
                for item in model.get("top_features") or []:
                    features.append(
                        {
                            "Model": labels.get(key, key),
                            "Feature": item.get("feature"),
                            "Relative gain share %": item.get("share_pct"),
                        }
                    )
            if features:
                st.markdown("#### What the validated models are using most")
                st.dataframe(pd.DataFrame(features), width="stretch", hide_index=True)

        st.caption(
            f'XGBoost · same-ticker data · {ml.get("training_samples", 0)} historical snapshots · '
            f'target geometry +{ml.get("target_pct", 0):.1f}% / {ml.get("stop_pct", 0):.1f}% · '
            f'source: {ml.get("source")}. '
            f'Peer cohort: {int(peer.get("samples") or 0):,} similar observations across '
            f'{int(peer.get("peer_symbols") or 0)} other tickers. '
            "ML v1.4 is a probability/decision-support layer, not a guaranteed forecast, "
            "and it cannot override the rule-based trade action."
        )
