import html
from datetime import datetime


def _fmt_pct(value, digits=1):
    try:
        return f"{float(value):.{digits}f}%"
    except Exception:
        return "—"


def _fmt_int(value):
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return "—"


def _fmt_money_compact(value):
    try:
        value = float(value)
    except Exception:
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000:
        return f"{sign}$" + f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}$" + f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{sign}$" + f"{value / 1_000:.1f}K"
    return f"{sign}$" + f"{value:,.0f}"

def _component_line(components, ordered_labels):
    if not isinstance(components, dict):
        return None
    parts = []
    for key, label in ordered_labels:
        value = components.get(key)
        try:
            value = float(value)
        except Exception:
            continue
        if key != "base" and abs(value) < 0.05:
            continue
        text = f"{value:.1f}" if key == "base" else f"{value:+.1f}"
        parts.append(f"{label} {text}")
    return " · ".join(parts) if parts else None


def _signal_time(value):
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.strftime("%-I:%M %p ET")
    except Exception:
        return "—"


def _signal_progression_html(lifecycle):
    if not isinstance(lifecycle, dict) or lifecycle.get("status") != "ok":
        return None

    signal_price = lifecycle.get("signal_price")
    current_price = lifecycle.get("current_price")
    change = lifecycle.get("change_since_signal_pct")
    thesis = str(lifecycle.get("thesis_status") or "ACTIVE")
    current_state = str(lifecycle.get("current_state") or "Current setup unavailable")
    when = _signal_time(lifecycle.get("signal_time"))

    signal_text = f"${float(signal_price):.2f}" if signal_price is not None else "—"
    current_text = f"${float(current_price):.2f}" if current_price is not None else "—"
    change_text = f"{float(change):+.1f}%" if change is not None else "—"
    thesis_cls = (
        "#63e58b" if "SUCCEEDED" in thesis
        else "#ff8585" if thesis in {"FAILED", "AT RISK"}
        else "#ffd166"
    )
    return (
        '<div style="margin:3px 0 5px;padding:7px 10px;border:1px solid #28425f;'
        'border-radius:9px;background:#0c1828;font-size:12px;line-height:1.35;">'
        '<b style="color:#dce9f8;">Signal progression</b>'
        f' &nbsp; Original entry <b>{html.escape(signal_text)}</b> at {html.escape(when)}'
        f' &nbsp;→&nbsp; Now <b>{html.escape(current_text)}</b> '
        f'(<b>{html.escape(change_text)}</b>)'
        f' &nbsp;·&nbsp; Thesis: <b style="color:{thesis_cls};">{html.escape(thesis)}</b>'
        f' &nbsp;·&nbsp; Current: <b>{html.escape(current_state)}</b>'
        '</div>'
    )


def _score_card(st, col, title, score, label, note):
    cls = "good" if score >= 72 else "warn" if score >= 52 else "bad"
    with col:
        st.markdown(
            f'<div class="card"><div class="k">{html.escape(title)}</div>'
            f'<div class="v {cls}">{score:.0f} / 100</div>'
            f'<div class="n">{html.escape(label)} · {html.escape(note)}</div></div>',
            unsafe_allow_html=True,
        )


def _text_card(st, col, title, value, note, cls="warn"):
    with col:
        st.markdown(
            f'<div class="card"><div class="k">{html.escape(title)}</div>'
            f'<div class="v {cls}">{html.escape(str(value))}</div>'
            f'<div class="n">{html.escape(str(note))}</div></div>',
            unsafe_allow_html=True,
        )


def render_v2_decision(st, metrics):
    v2 = metrics.get("decision_v2") or {}
    if not v2:
        return

    potential = float(v2.get("potential_score") or 0)
    readiness = float(v2.get("entry_readiness") or 0)
    evidence = float(v2.get("evidence_strength") or 0)

    heading_cols = st.columns([8, 1.35], vertical_alignment="center")
    with heading_cols[0]:
        st.markdown(
            '<div class="section">Upside potential & entry timing '
            '<span style="font-size:12px;color:#91a7c2">Decision v2</span></div>',
            unsafe_allow_html=True,
        )
    details_slot = heading_cols[1]

    cols = st.columns(3)
    _score_card(
        st, cols[0], "UPSIDE POTENTIAL", potential,
        str(v2.get("potential_label") or "—"),
        "chance/quality of further upside, not entry timing",
    )
    _score_card(
        st, cols[1], "ENTRY READINESS", readiness,
        str(v2.get("entry_label") or "—"),
        "quality of entering around the current price",
    )
    _score_card(
        st, cols[2], "EVIDENCE STRENGTH", evidence,
        str(v2.get("evidence_label") or "—"),
        "how much reliable data supports the read",
    )

    timeframe = v2.get("timeframe_analysis") or {}
    if timeframe:
        st.markdown(
            '<div class="section">Timeframe fit '
            '<span style="font-size:12px;color:#91a7c2">intraday · swing · longer-term</span></div>',
            unsafe_allow_html=True,
        )
        tf_scores = timeframe.get("scores") or {}
        tf_labels = timeframe.get("labels") or {}
        best_fit = str(timeframe.get("best_fit") or "MIXED")
        tf_cols = st.columns(4)
        _text_card(
            st,
            tf_cols[0],
            "BEST FIT",
            best_fit,
            "strongest current evidence match",
            "good" if best_fit != "MIXED" else "warn",
        )
        _score_card(
            st,
            tf_cols[1],
            "INTRADAY FIT",
            float(tf_scores.get("intraday") or 0),
            str(tf_labels.get("intraday") or "—"),
            "today / live momentum",
        )
        _score_card(
            st,
            tf_cols[2],
            "SWING FIT",
            float(tf_scores.get("swing") or 0),
            str(tf_labels.get("swing") or "—"),
            "multi-day continuation",
        )
        _score_card(
            st,
            tf_cols[3],
            "LONGER-TERM FIT",
            float(tf_scores.get("long_term") or 0),
            str(tf_labels.get("long_term") or "—"),
            "fundamentals + multi-month trend",
        )
        research = timeframe.get("swing_research_flags") or {}
        matches = research.get("matches") or []
        if matches:
            labels = " · ".join(
                str(item.get("label") or item.get("id") or "Research match")
                for item in matches
            )
            st.info(
                "**Swing research match — exploratory tracking only:** "
                + labels
                + ". The original study used end-of-day historical observations; "
                "this live Analyzer match is intraday, so the historical rate below "
                "is reference context, not a live success probability or direct validation."
            )
            with st.expander("Research match details"):
                for item in matches:
                    hist = item.get("historical_confirmation") or {}
                    label = str(item.get("label") or item.get("id") or "Setup")
                    variant = str(item.get("variant") or "").strip()
                    title = label + (f" · {variant}" if variant else "")
                    st.markdown(f"**{title}**")
                    st.write(str(item.get("rule") or ""))
                    rate = hist.get("confirmation_success_pct")
                    comp = hist.get("comparison_success_pct")
                    lift = hist.get("confirmation_lift_pp")
                    n = hist.get("confirmation_n")
                    if rate is not None and comp is not None:
                        st.caption(
                            f"Historical EOD reference only: {float(rate):.1f}% "
                            f"reached +5% before -4% vs {float(comp):.1f}% for the "
                            f"comparison set · lift {float(lift):+.1f} pp · n={int(n or 0)}. "
                            "Do not read this as the probability for the current intraday match."
                        )
                st.caption(str(research.get("note") or ""))

        st.caption(str(timeframe.get("note") or ""))

    tracking = v2.get("tracking") or {}
    stream = v2.get("live_stream_status") or {}
    stream_provider = str(stream.get("provider") or "").lower()
    stream_status = str(stream.get("status") or "").upper()
    data_label = (
        "TRADIER CONSOLIDATED"
        if stream_provider == "tradier"
        else str(stream.get("feed") or "ALPACA").upper()
    )
    durable_on = bool(tracking.get("durable_enabled"))
    last_record = tracking.get("last_record") or {}
    last_sync = last_record.get("durable_sync") or {}
    durable_error = (
        tracking.get("durable_error")
        or (
            last_sync.get("error")
            if last_sync.get("reason") == "error"
            else None
        )
    )
    record_ok = bool(
        last_record.get("recorded")
        or last_record.get("reason") == "already_recorded"
    )
    research_state = (
        (v2.get("timeframe_analysis") or {}).get("swing_research_flags") or {}
    )
    research_session = str(
        research_state.get("live_sampling_context") or ""
    ).lower()
    research_match_eligible = bool(
        research_state.get("matched")
        and research_session == "regular_intraday"
        and research_state.get("historical_universe_proxy_pass") is True
    )
    swing_forward_status = (
        "MATCH ACTIVE"
        if research_match_eligible
        else "ARMED"
        if research_session == "regular_intraday"
        else "PAUSED OFF-HOURS"
    )

    if tracking.get("error"):
        st.warning(
            "Live test status: prediction tracking reported an error — "
            + str(tracking.get("error"))[:180]
        )
    elif not durable_on:
        st.warning(
            "Live test status: market analysis is running, but durable prediction "
            "tracking is OFF. Live samples could be lost on an app restart."
        )
    elif durable_error:
        st.warning(
            "Live test status: local prediction capture is running, but the "
            "durable GitHub sync reported an error — "
            + str(durable_error)[:180]
        )
    else:
        st.caption(
            "Live test status · Data **"
            + data_label
            + (" / " + stream_status if stream_status else "")
            + "** · Durable tracking **ON** · Prediction capture **"
            + ("ACTIVE" if record_ok else "READY")
            + "** · Swing forward tracking **"
            + swing_forward_status
            + "**"
        )

    lifecycle_html = _signal_progression_html(tracking.get("signal_lifecycle"))
    if lifecycle_html:
        st.markdown(lifecycle_html, unsafe_allow_html=True)

    with details_slot.popover("Why these scores"):
        pc, ec = st.columns(2)
        with pc:
            st.markdown("#### Potential drivers")
            reasons = v2.get("potential_reasons") or []
            if reasons:
                for reason in reasons:
                    st.write(f"• {reason}")
            else:
                st.write("• No strong upside drivers identified yet.")
            potential_makeup = _component_line(
                v2.get("potential_components"),
                [
                    ("base", "Base"),
                    ("technical_momentum", "Technical"),
                    ("historical_analogs", "History"),
                    ("validated_ml", "ML"),
                    ("catalyst", "Catalyst"),
                    ("market_sector", "Market"),
                    ("dilution", "Dilution"),
                ],
            )
            if potential_makeup:
                st.caption("Score makeup · " + potential_makeup)

            blockers = v2.get("entry_blockers") or []
            st.markdown("#### Entry timing")
            if blockers:
                for blocker in blockers:
                    st.write(f"• {blocker}")
            else:
                st.write("• No major entry blockers detected.")
            entry_makeup = _component_line(
                v2.get("entry_components"),
                [
                    ("base", "Base"),
                    ("trigger_proximity", "Trigger"),
                    ("reward_risk", "R/R"),
                    ("vwap", "VWAP"),
                    ("momentum", "Momentum"),
                    ("execution_quality", "Execution"),
                    ("extension", "Extension"),
                    ("pullback_structure", "Pullback"),
                    ("repeat_bounce_setup", "Repeat bounce"),
                    ("stair_step_structure", "Stair-step"),
                    ("plan_status_cap", "Plan cap"),
                    ("evidence_safety_cap", "Evidence cap"),
                ],
            )
            if entry_makeup:
                st.caption("Score makeup · " + entry_makeup)

        with ec:
            st.markdown("#### Evidence quality")
            for reason in v2.get("evidence_reasons") or []:
                st.write(f"• {reason}")

            market = v2.get("market_context") or {}
            moves = market.get("moves") or {}
            st.markdown("#### Market context")
            st.write(
                f"**{market.get('label') or 'UNKNOWN'}** · "
                f"SPY {_fmt_pct(moves.get('SPY'))} · "
                f"QQQ {_fmt_pct(moves.get('QQQ'))} · "
                f"IWM {_fmt_pct(moves.get('IWM'))}"
            )
            if market.get("sector_etf"):
                st.write(
                    f"Sector proxy **{market.get('sector_etf')}**: "
                    f"{_fmt_pct(market.get('sector_move_pct'))}"
                )

            stream = v2.get("live_stream_status") or {}
            provider = str(stream.get("provider") or "").lower()
            if provider == "tradier":
                stream_status = str(stream.get("status") or "").upper()
                st.write(
                    f"**Live market stream: TRADIER CONSOLIDATED** · {stream_status}"
                )
            else:
                sip = v2.get("sip_status") or {}
                if sip.get("available") and str(sip.get("active_feed") or "").upper() == "SIP":
                    st.write("**Live market feed: SIP ACTIVE** · consolidated real-time feed")
                else:
                    st.write("**Live market feed: IEX fallback**")
                    if sip.get("error"):
                        st.caption("SIP entitlement check: " + str(sip.get("error"))[:160])

        catalyst = v2.get("catalyst_strength") or {}
        sec = v2.get("fundamental_context") or {}
        turnover = v2.get("turnover_context") or {}
        tcols = st.columns(3)

        with tcols[0]:
            st.markdown("#### Catalyst")
            st.write(f"**{catalyst.get('label') or 'NONE'}**")
            st.caption(
                f"{int(catalyst.get('fresh_articles') or 0)} fresh article(s) · "
                f"{int(catalyst.get('article_count') or 0)} analyzed"
            )

        with tcols[1]:
            st.markdown("#### Float / turnover")
            shares = turnover.get("shares_outstanding")
            shares_turn = turnover.get("shares_outstanding_turnover")
            float_shares = turnover.get("float_shares")
            float_turn = turnover.get("float_turnover")

            if float_shares is not None:
                st.write(
                    f"Public float: **{_fmt_int(float_shares)} shares** "
                    f"({turnover.get('float_source') or 'provider'})"
                )
                st.write(
                    "Session volume / public float: "
                    f"**{_fmt_pct((float_turn or 0) * 100) if float_turn is not None else '—'}**"
                )
                if turnover.get("float_date"):
                    st.caption(
                        f"Float date: {turnover.get('float_date')}. "
                        "Collected for calibration; not yet used to change score weights."
                    )
            else:
                st.write(f"Shares outstanding: **{_fmt_int(shares)}**")
                st.write(
                    "Session volume / shares outstanding: "
                    f"**{_fmt_pct((shares_turn or 0) * 100) if shares_turn is not None else '—'}**"
                )
                st.caption(
                    "True public float is not currently configured. Add an "
                    "INTRINIO_API_KEY to Streamlit Secrets to enable the "
                    "Intrinio public-float feed; until then this remains a "
                    "shares-outstanding turnover proxy."
                )

        with tcols[2]:
            st.markdown("#### Dilution / financing")
            st.write(f"**{sec.get('dilution_risk') or 'UNKNOWN'}**")
            forms = sec.get("recent_offering_forms") or []
            if forms:
                st.caption(
                    ", ".join(
                        f"{row.get('form')} ({row.get('age_days')}d)"
                        for row in forms[:4]
                    )
                )
            kws = sec.get("dilution_keywords") or []
            if kws:
                st.caption("Detected filing terms: " + ", ".join(kws[:5]))

        timeframe = v2.get("timeframe_analysis") or {}
        if timeframe:
            st.markdown("#### Timeframe evidence")
            tfc1, tfc2, tfc3 = st.columns(3)
            with tfc1:
                st.markdown("**Intraday**")
                for reason in timeframe.get("intraday_reasons") or []:
                    st.write(f"• {reason}")
            with tfc2:
                st.markdown("**Swing**")
                for reason in timeframe.get("swing_reasons") or []:
                    st.write(f"• {reason}")
            with tfc3:
                st.markdown("**Longer-term**")
                for reason in timeframe.get("long_term_reasons") or []:
                    st.write(f"• {reason}")

            trend = timeframe.get("daily_trend") or {}
            fundamentals = sec.get("fundamentals") or {}
            st.caption(
                "Price trend · "
                f"20d {_fmt_pct(trend.get('return_20d_pct'))} · "
                f"60d {_fmt_pct(trend.get('return_60d_pct'))} · "
                f"120d {_fmt_pct(trend.get('return_120d_pct'))}"
            )
            st.caption(
                "Reported fundamentals · "
                f"Revenue {_fmt_money_compact(fundamentals.get('revenue_latest'))} · "
                f"YoY {_fmt_pct(fundamentals.get('revenue_yoy_pct'))} · "
                f"Net income {_fmt_money_compact(fundamentals.get('net_income_latest'))} · "
                f"Cash {_fmt_money_compact(fundamentals.get('cash_and_equivalents'))} · "
                f"Long-term debt {_fmt_money_compact(fundamentals.get('long_term_debt'))}"
            )

        st.markdown("#### Prediction tracking")
        effective_resolved = int(
            tracking.get("effective_resolved_60m")
            or tracking.get("durable_resolved_60m")
            or tracking.get("resolved_60m")
            or 0
        )
        progress = tracking.get("calibration_progress") or {}
        next_threshold = progress.get("next_threshold")
        if next_threshold:
            progress_text = (
                f"{effective_resolved}/{int(next_threshold)} toward "
                f"{progress.get('stage') or 'COLLECTING'}"
            )
        else:
            progress_text = f"{effective_resolved} resolved · {progress.get('stage') or 'STRONGER SAMPLE'}"
        st.write(
            f"Recorded this runtime: **{int(tracking.get('total_predictions') or 0)}** · "
            f"Durable 60m outcomes: **{effective_resolved}** · "
            f"Calibration progress: **{progress_text}**"
        )
        st.caption(
            "Calibration uses at most one observation per ticker per hour so "
            "overlapping 5-minute refreshes do not inflate the sample. "
            "30 resolved ticker-hours = early read · 100+ = useful · "
            "300+ = much stronger evidence for changing score weights."
        )
        current_bucket = (
            "80-100" if potential >= 80 else
            "65-79" if potential >= 65 else
            "50-64" if potential >= 50 else
            "0-49"
        )
        calibration = (tracking.get("potential_calibration") or {}).get(current_bucket) or {}
        if int(calibration.get("n") or 0) >= 5:
            r15 = calibration.get("return_15m") or {}
            r30 = calibration.get("return_30m") or {}
            r60 = calibration.get("return_60m") or {}
            st.write(
                f"**Current Potential bucket ({current_bucket}) empirical result:** "
                f"15m higher {_fmt_pct(r15.get('higher_rate'))} · "
                f"30m higher {_fmt_pct(r30.get('higher_rate'))} · "
                f"60m higher {_fmt_pct(calibration.get('higher_60m_rate'))} "
                f"(60m n={int(calibration.get('n') or 0)})."
            )
            if int(calibration.get("target_stop_n") or 0) >= 5:
                st.write(
                    f"Target 1 before stop: "
                    f"**{_fmt_pct(calibration.get('target_before_stop_rate'))}** "
                    f"across n={int(calibration.get('target_stop_n') or 0)} decisive outcomes."
                )
        else:
            st.write(
                f"**Calibration:** collecting observations for the {current_bucket} "
                "Potential bucket; at least 5 resolved examples are needed before showing a rate."
            )
        entry_signal_cal = tracking.get("entry_signal_calibration") or {}
        if int(entry_signal_cal.get("signals") or 0) > 0:
            st.write(
                f"**Actual ENTRY AVAILABLE signals:** "
                f"{int(entry_signal_cal.get('signals') or 0)} independent signal(s) · "
                f"T1-before-stop {_fmt_pct(entry_signal_cal.get('target_before_stop_rate'))} "
                f"across n={int(entry_signal_cal.get('resolved_target_stop') or 0)} decisive outcomes · "
                f"60m higher {_fmt_pct(entry_signal_cal.get('higher_60m_rate'))} "
                f"across n={int(entry_signal_cal.get('resolved_60m') or 0)}."
            )

        research_cal = tracking.get("swing_research_flag_calibration") or {}
        current_research = (
            (v2.get("timeframe_analysis") or {}).get("swing_research_flags") or {}
        )
        current_matches = current_research.get("matches") or []
        if current_matches:
            st.markdown("#### Live Swing research tracking")
            for item in current_matches:
                flag_id = str(item.get("id") or "")
                label = str(item.get("label") or flag_id or "Research match")
                live = research_cal.get(flag_id) or {}
                signals = int(live.get("signals") or 0)
                resolved = int(live.get("resolved") or 0)
                stage = str(live.get("stage") or "COLLECTING")
                if resolved:
                    st.write(
                        f"**{label}:** {signals} independent live match(es) · "
                        f"{resolved} resolved · +5% before -4% "
                        f"{_fmt_pct(live.get('target_before_stop_rate_pct'))} · "
                        f"{stage}."
                    )
                else:
                    st.write(
                        f"**{label}:** {signals} independent live match(es) recorded · "
                        f"waiting for 5-trading-day outcomes · {stage}."
                    )
            st.caption(
                "Forward calibration counts only regular-session matches that pass the "
                "historical study's basic price/day-move/dollar-volume universe proxy, "
                "using the first match per ticker per signal day. It remains intraday "
                "exploratory evidence rather than direct EOD historical parity."
            )

        tf_progress = tracking.get("timeframe_learning_progress") or {}
        tf_best = tracking.get("timeframe_best_fit_calibration") or {}
        if tf_progress or tf_best:
            st.markdown("#### Timeframe learning")
            _ip = tf_progress.get("intraday") or {}
            _sp = tf_progress.get("swing") or {}
            _lp = tf_progress.get("long_term") or {}
            st.write(
                "**Resolved outcome samples:** "
                f"Intraday 60m **{int(_ip.get('resolved') or 0)}** · "
                f"Swing 5-day **{int(_sp.get('resolved') or 0)}** · "
                f"Longer-term 20-day **{int(_lp.get('resolved') or 0)}**"
            )
            current_fit = str((v2.get("timeframe_analysis") or {}).get("best_fit") or "")
            fit_stats = tf_best.get(current_fit) or {}
            if int(fit_stats.get("resolved") or 0) >= 5:
                st.write(
                    f"**{current_fit} historical fit result:** "
                    f"higher {_fmt_pct(fit_stats.get('higher_rate'))} · "
                    f"avg return {_fmt_pct(fit_stats.get('avg_return_pct'))} · "
                    f"n={int(fit_stats.get('resolved') or 0)} over {fit_stats.get('horizon') or 'matched horizon'}."
                )
            else:
                st.caption(
                    "The app is now recording Intraday, Swing, and Longer-term "
                    "fit scores against their actual future outcomes. These "
                    "results are tracking-only until enough independent samples "
                    "exist to validate changing the score weights."
                )

        repeat_cal = tracking.get("repeat_bounce_calibration") or {}
        if int(repeat_cal.get("entry_signals") or 0) > 0:
            st.write(
                f"**Bounce #2/#3+ live calibration:** "
                f"{int(repeat_cal.get('entry_signals') or 0)} confirmed later-bounce signal(s) · "
                f"T1-before-stop {_fmt_pct(repeat_cal.get('target_before_stop_rate'))} "
                f"across n={int(repeat_cal.get('resolved_target_stop') or 0)} decisive outcomes · "
                f"avg 30m MFE {_fmt_pct(repeat_cal.get('avg_mfe_30m_pct'))} · "
                f"avg 30m MAE {_fmt_pct(repeat_cal.get('avg_mae_30m_pct'))}."
            )

        failure_cal = tracking.get("mature_bounce_failure_calibration") or {}
        if int(failure_cal.get("resolved_60m_excursions") or 0) > 0:
            st.write(
                f"**Mature-bounce falloff calibration:** "
                f"≥5% drop {_fmt_pct(failure_cal.get('drop_5pct_rate'))} · "
                f"≥10% drop {_fmt_pct(failure_cal.get('drop_10pct_rate'))} "
                f"across n={int(failure_cal.get('resolved_60m_excursions') or 0)} resolved 60m observations."
            )

        if tracking.get("durable_enabled"):
            st.write("**Durable tracking: ON** · Analyzer predictions are syncing to GitHub.")
            if tracking.get("durable_error"):
                st.caption("Last durable-sync issue: " + str(tracking.get("durable_error"))[:160])
        else:
            st.write("**Durable tracking: OFF** · local tracking still works, but restarts can reset it.")
            st.caption(
                "To enable durable Analyzer calibration, add a fine-grained GitHub token "
                "to Streamlit Secrets as ANALYZER_GITHUB_TOKEN with Contents read/write "
                "permission for this repository."
            )
        st.caption(
            "The app records one prediction per ticker per 5-minute bucket, resolves "
            "older outcomes with Tradier consolidated history (Alpaca fallback), and groups resolved predictions by "
            "score bucket so we can test whether higher scores really outperform lower scores."
        )
