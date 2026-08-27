import html


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

    st.markdown(
        '<div class="section">Upside potential & entry timing '
        '<span style="font-size:12px;color:#91a7c2">Decision v2</span></div>',
        unsafe_allow_html=True,
    )

    potential = float(v2.get("potential_score") or 0)
    readiness = float(v2.get("entry_readiness") or 0)
    evidence = float(v2.get("evidence_strength") or 0)

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

    with st.expander("Why these three scores?"):
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

        tracking = v2.get("tracking") or {}
        st.markdown("#### Prediction tracking")
        st.write(
            f"Recorded: **{int(tracking.get('total_predictions') or 0)}** · "
            f"60m resolved: **{int(tracking.get('resolved_60m') or 0)}** · "
            f"60m higher rate: **{_fmt_pct(tracking.get('higher_60m_rate'))}** · "
            f"T1-before-stop resolved: **{int(tracking.get('resolved_target_stop') or 0)}**"
        )
        st.caption(
            "The app now records one prediction per ticker per 5-minute bucket and "
            "resolves older outcomes with delayed SIP data. Storage is runtime-local "
            "for this first version, so a Streamlit redeploy/restart can reset the history."
        )
