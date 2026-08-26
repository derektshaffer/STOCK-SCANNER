def render_historical_setup(st, pd, result, card, pp):
    setup = result.get("historical_setup") or (result.get("historical_analogs") or {}).get("setup_patterns") or {}

    st.markdown('<div class="section">Historical setup match</div>', unsafe_allow_html=True)
    if setup.get("status") == "ok":
        intr = setup.get("intraday") or {}
        cols = st.columns(6)
        bias = setup.get("bias_score")

        card(
            cols[0],
            "SETUP BIAS",
            setup.get("bias_label") or "MIXED",
            f"Score {bias:+.1f}" if bias is not None else "same-ticker history",
            "good" if setup.get("bias_label") == "BULLISH" else "bad" if setup.get("bias_label") == "BEARISH" else "warn",
        )
        card(cols[1], "SIMILAR DAYS", str(setup.get("sample_count", 0)), setup.get("setup_label") or "setup matches")

        gr, gf = setup.get("gap_run_pct"), setup.get("gap_fade_pct")
        card(
            cols[2],
            "GAP RUN / FADE",
            f"{gr:.0f}% / {gf:.0f}%" if gr is not None and gf is not None else "—",
            f'n={setup.get("gap_sample_count", 0)} gap analogs',
        )

        bf, bfail = setup.get("breakout_follow_through_pct"), setup.get("breakout_failure_pct")
        card(
            cols[3],
            "BREAKOUT HOLD / FAIL",
            f"{bf:.0f}% / {bfail:.0f}%" if bf is not None and bfail is not None else "—",
            f'n={setup.get("breakout_test_count", 0)} tested',
        )

        vr = intr.get("vwap_reclaim_follow_through_pct")
        card(
            cols[4],
            "VWAP RECLAIM",
            f"{vr:.0f}% follow" if vr is not None else "—",
            f'n={intr.get("sample_count", 0)} matched intraday days',
        )

        pb = intr.get("median_first_pullback_pct")
        card(
            cols[5],
            "EARLY PULLBACK",
            pp(pb),
            f'High most often: {intr.get("session_high_most_common") or "—"}',
        )

        for note in (setup.get("notes") or [])[:5]:
            st.caption("• " + str(note))

        matches = pd.DataFrame(setup.get("matches") or [])
        if not matches.empty:
            show = [
                c for c in [
                    "date",
                    "pattern",
                    "gap_pct",
                    "day_pct",
                    "relative_volume",
                    "same_day_pullback_pct",
                    "next_day_pct",
                    "next_day_mfe_pct",
                    "breakout_follow",
                    "breakout_failed",
                ] if c in matches.columns
            ]
            st.dataframe(matches[show], width="stretch", hide_index=True)

        st.caption(
            "Included in the setup score and trade-plan confidence. Similarity uses today's move size, "
            "opening gap and relative volume; recent 5-minute matches add VWAP-reclaim, early-pullback "
            "and time-of-day tendencies."
        )
    elif setup.get("status") == "unavailable":
        detail = setup.get("error")
        msg = "Historical setup matching is temporarily unavailable; the rest of the analyzer is still active."
        if detail:
            msg += f" ({detail})"
        st.caption(msg)
    else:
        st.info("Not enough comparable same-ticker history for the setup-pattern layer yet.")
