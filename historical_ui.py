def render_historical_setup(st, pd, result, card, pp):
    setup = result.get("historical_setup") or (result.get("historical_analogs") or {}).get("setup_patterns") or {}

    st.markdown('<div class="section">Historical setup match <span style="font-size:12px;color:#91a7c2">research-only</span></div>', unsafe_allow_html=True)
    st.caption(
        "Historical analogs are reference research only. They do not change the "
        "live Setup Score, entry zone, targets, stop, preferred plan, action, "
        "Entry Readiness, Upside Potential, or plan confidence."
    )
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
            "Overall directional lean from the stock's own historically similar setups. It summarizes whether comparable past days tended to behave bullishly, bearishly, or mixed.",
        )
        sample_quality = setup.get("sample_quality") or "LOW"
        similar_note = f'{setup.get("setup_label") or "setup matches"} · {sample_quality} sample'
        card(
            cols[1], "SIMILAR DAYS", str(setup.get("sample_count", 0)), similar_note, "",
            "Number of the stock's own historical trading days that most closely resemble today's move, gap, relative volume, and intraday setup."
        )

        gr, gf = setup.get("gap_run_pct"), setup.get("gap_fade_pct")
        card(
            cols[2],
            "GAP RUN / FADE",
            f"{gr:.0f}% / {gf:.0f}%" if gr is not None and gf is not None else "—",
            f'n={setup.get("gap_sample_count", 0)} gap analogs',
            "",
            "Of comparable historical gap days, the first number is the share that continued running and the second is the share that faded."
        )

        bf, bfail = setup.get("breakout_follow_through_pct"), setup.get("breakout_failure_pct")
        card(
            cols[3],
            "BREAKOUT HOLD / FAIL",
            f"{bf:.0f}% / {bfail:.0f}%" if bf is not None and bfail is not None else "—",
            f'n={setup.get("breakout_test_count", 0)} tested',
            "",
            "Among comparable historical breakouts, HOLD is the share that stayed above the breakout level; FAIL is the share that fell back below it."
        )

        vr = intr.get("vwap_reclaim_follow_through_pct")
        card(
            cols[4],
            "VWAP RECLAIM",
            f"{vr:.0f}% follow" if vr is not None else "—",
            f'n={intr.get("sample_count", 0)} matched intraday days',
            "",
            "How often a move back above VWAP on comparable historical days produced follow-through instead of quickly losing VWAP again."
        )

        pb = intr.get("median_first_pullback_pct")
        high_period = str(intr.get("session_high_most_common") or "—")
        card(
            cols[5],
            "EARLY PULLBACK",
            pp(pb),
            f"High most often: {high_period}",
            "",
            "Median size of the first meaningful pullback after the early push on comparable historical days. A negative value means price pulled back from the prior reference level.",
            "POWER HOUR" if high_period.upper() == "POWER HOUR" else None,
            "Power Hour is the final hour of regular U.S. stock trading, usually 3:00–4:00 PM Eastern. Volume and volatility often increase as traders and institutions reposition before the 4:00 PM close."
            if high_period.upper() == "POWER HOUR" else None,
        )

        second_rate = intr.get("second_bounce_rate_pct")
        third_rate = intr.get("third_bounce_rate_pct")
        b1_med = intr.get("median_bounce1_pct")
        b2_med = intr.get("median_bounce2_pct")
        b2_lower = intr.get("second_bounce_lower_high_rate_pct")
        if any(v is not None for v in (second_rate, third_rate, b1_med, b2_med, b2_lower)):
            bc = st.columns(5)
            card(
                bc[0],
                "2ND BOUNCE RATE",
                f"{second_rate:.0f}%" if second_rate is not None else "—",
                "matched intraday days",
                "good" if second_rate is not None and second_rate >= 55 else "warn",
                "How often historically similar days produced a second completed pullback-to-rebound cycle after the first bounce.",
            )
            card(
                bc[1],
                "3RD BOUNCE RATE",
                f"{third_rate:.0f}%" if third_rate is not None else "—",
                "matched intraday days",
                "good" if third_rate is not None and third_rate >= 45 else "warn",
                "How often historically similar days produced a third completed rebound after two earlier bounce cycles.",
            )
            card(
                bc[2],
                "MEDIAN BOUNCE #1",
                f"{b1_med:.1f}%" if b1_med is not None else "—",
                "first rebound size",
                "",
                "Median percentage gain from the first pullback low to the first confirmed bounce peak on comparable historical days.",
            )
            card(
                bc[3],
                "MEDIAN BOUNCE #2",
                f"{b2_med:.1f}%" if b2_med is not None else "—",
                "second rebound size",
                "",
                "Median percentage gain from the second dip to the second confirmed bounce peak on comparable historical days.",
            )
            card(
                bc[4],
                "2ND BOUNCE LOWER HIGH",
                f"{b2_lower:.0f}%" if b2_lower is not None else "—",
                "of days with a second bounce",
                "bad" if b2_lower is not None and b2_lower >= 60 else "warn",
                "Among days that produced a second bounce, the percentage where that second bounce peaked below the previous bounce high. Higher values suggest later bounces often weaken.",
            )

        post2_drop5 = intr.get("post_second_bounce_drop5_rate_pct")
        post3_drop5 = intr.get("post_third_bounce_drop5_rate_pct")
        post3_median = intr.get("median_post_third_bounce_max_drop_pct")
        stair_hist = setup.get("stair_step_history") or {}
        stair_n = int(stair_hist.get("event_count") or 0)
        stair_hit5 = stair_hist.get("next3d_hit5_rate_pct")
        stair_fail5 = stair_hist.get("next3d_failure5_rate_pct")

        if any(v is not None for v in (post2_drop5, post3_drop5, post3_median)) or stair_n:
            fc = st.columns(6)
            card(
                fc[0],
                "DROP ≥5% AFTER #2",
                f"{post2_drop5:.0f}%" if post2_drop5 is not None else "—",
                "after second bounce peak",
                "bad" if post2_drop5 is not None and post2_drop5 >= 55 else "warn",
                "How often comparable sessions fell at least 5% after the second completed bounce peak.",
            )
            card(
                fc[1],
                "DROP ≥5% AFTER #3",
                f"{post3_drop5:.0f}%" if post3_drop5 is not None else "—",
                "after third bounce peak",
                "bad" if post3_drop5 is not None and post3_drop5 >= 55 else "warn",
                "How often comparable sessions fell at least 5% after the third completed bounce peak.",
            )
            card(
                fc[2],
                "MEDIAN DROP AFTER #3",
                pp(post3_median),
                "peak to later session low",
                "bad" if post3_median is not None and post3_median <= -8 else "warn",
                "Median worst decline from the third-bounce peak to a later low during the same session.",
            )
            card(
                fc[3],
                "STAIR-STEP EVENTS",
                str(stair_n),
                "historical same-ticker sequences",
                "",
                "Number of historical multi-session step / higher-plateau structures found in the event study.",
            )
            card(
                fc[4],
                "STAIR +5% / 3D",
                f"{stair_hit5:.0f}%" if stair_hit5 is not None else "—",
                "within next 3 sessions",
                "good" if stair_hit5 is not None and stair_hit5 >= 55 else "warn",
                "How often a historical stair-step or higher-plateau setup expanded at least another 5% within the next three sessions.",
            )
            card(
                fc[5],
                "STAIR FAIL -5% / 3D",
                f"{stair_fail5:.0f}%" if stair_fail5 is not None else "—",
                "within next 3 sessions",
                "bad" if stair_fail5 is not None and stair_fail5 >= 45 else "warn",
                "How often a historical stair-step setup instead fell at least 5% within the next three sessions.",
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
            with st.expander("Historical setup match table", expanded=False):
                st.dataframe(matches[show], width="stretch", hide_index=True)

        st.caption(
            "Included in the setup score and trade-plan confidence. Similarity uses today's move size, "
            "opening gap and relative volume; recent 5-minute matches add VWAP-reclaim, early-pullback, "
            "multi-bounce falloff and time-of-day tendencies. Daily history separately studies multi-session stair-step outcomes."
        )
    elif setup.get("status") == "unavailable":
        detail = setup.get("error")
        msg = "Historical setup matching is temporarily unavailable; the rest of the analyzer is still active."
        if detail:
            msg += f" ({detail})"
        st.caption(msg)
    else:
        st.info("Not enough comparable same-ticker history for the setup-pattern layer yet.")
