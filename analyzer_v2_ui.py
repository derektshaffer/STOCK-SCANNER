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

    tracking = v2.get("tracking") or {}
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

            blockers = v2.get("entry_blockers") or []
            st.markdown("#### Entry timing")
            if blockers:
                for blocker in blockers:
                    st.write(f"• {blocker}")
            else:
                st.write("• No major entry blockers detected.")

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
            st.markdown("#### Shares / turnover")
            shares = turnover.get("shares_outstanding")
            turn = turnover.get("shares_outstanding_turnover")
            st.write(f"Shares outstanding: **{_fmt_int(shares)}**")
            st.write(
                "Session volume / shares outstanding: "
                f"**{_fmt_pct((turn or 0) * 100) if turn is not None else '—'}**"
            )
            st.caption(
                "This is a shares-outstanding turnover proxy. Reliable live float "
                "shares are not available from the app's current data sources."
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

        st.markdown("#### Prediction tracking")
        st.write(
            f"Recorded: **{int(tracking.get('total_predictions') or 0)}** · "
            f"60m resolved: **{int(tracking.get('resolved_60m') or 0)}** · "
            f"60m higher rate: **{_fmt_pct(tracking.get('higher_60m_rate'))}** · "
            f"T1-before-stop resolved: **{int(tracking.get('resolved_target_stop') or 0)}**"
        )
        current_bucket = (
            "80-100" if potential >= 80 else
            "65-79" if potential >= 65 else
            "50-64" if potential >= 50 else
            "0-49"
        )
        calibration = (tracking.get("potential_calibration") or {}).get(current_bucket) or {}
        if int(calibration.get("n") or 0) >= 5:
            st.write(
                f"**Current Potential bucket ({current_bucket}) empirical result:** "
                f"{_fmt_pct(calibration.get('higher_60m_rate'))} higher after 60m "
                f"across n={int(calibration.get('n') or 0)} tracked observations."
            )
        else:
            st.write(
                f"**Calibration:** collecting observations for the {current_bucket} "
                "Potential bucket; at least 5 resolved examples are needed before showing a rate."
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
            "older outcomes with delayed SIP data, and groups resolved predictions by "
            "score bucket so we can test whether higher scores really outperform lower scores."
        )
