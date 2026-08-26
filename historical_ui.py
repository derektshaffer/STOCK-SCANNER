import streamlit as _st


# The combined workspace should reveal useful analyzer output immediately on
# page load. This CSS is installed when the Analyzer's historical UI module is
# imported, before analyzer_ui_core.py renders its main dashboard.
_st.markdown(
    """
    <style>
    /* Compact the combined Scanner / Analyzer selector on Analyzer pages. */
    .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
        gap: 6px !important;
        padding: 4px !important;
        margin: 0 0 8px !important;
        border-radius: 13px !important;
    }
    .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label {
        min-height: 64px !important;
        grid-template-columns: 38px minmax(0,1fr) !important;
        column-gap: 10px !important;
        row-gap: 0 !important;
        padding: 9px 13px !important;
        border-radius: 10px !important;
    }
    .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::before {
        width: 32px !important;
        height: 32px !important;
        font-size: 17px !important;
        border-width: 1px !important;
    }
    .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(2)::before {
        font-size: 19px !important;
    }
    .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label p {
        font-size: 16px !important;
        line-height: 1.05 !important;
    }
    .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::after {
        font-size: 10.5px !important;
        line-height: 1.1 !important;
    }

    /* Saved Stocks is a utility, not the main content. Keep it very compact. */
    .st-key-saved_stocks_top {
        margin-bottom: 2px !important;
    }
    .st-key-saved_stocks_top .saved-stock-shell {
        min-height: 48px !important;
        padding: 9px 13px !important;
        margin: 0 0 6px !important;
        border-radius: 10px !important;
    }
    .st-key-saved_stocks_top .saved-stock-title {
        font-size: 15px !important;
        line-height: 1.05 !important;
    }
    .st-key-saved_stocks_top .saved-stock-sub {
        display: none !important;
    }
    .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) {
        gap: 8px !important;
        margin-bottom: 3px !important;
    }
    .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(1),
    .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(2) {
        flex: 0 0 20% !important;
        width: 20% !important;
        max-width: 20% !important;
    }
    .st-key-saved_stocks_top .st-key-save_current_stock button,
    .st-key-saved_stocks_top .st-key-remove_current_stock button {
        min-height: 38px !important;
        height: 38px !important;
        border-radius: 8px !important;
        padding-top: 4px !important;
        padding-bottom: 4px !important;
    }
    .st-key-saved_stocks_top .st-key-save_current_stock button p,
    .st-key-saved_stocks_top .st-key-save_current_stock button span,
    .st-key-saved_stocks_top .st-key-remove_current_stock button p,
    .st-key-saved_stocks_top .st-key-remove_current_stock button span {
        font-size: 12px !important;
    }
    .st-key-saved_stocks_top [data-testid="stCaptionContainer"] {
        display: none !important;
    }

    /* Compress the Analyzer's own heading and search area. */
    .block-container {
        padding-top: .65rem !important;
    }
    .hero {
        padding: 10px 15px !important;
        margin-bottom: 7px !important;
        border-radius: 11px !important;
    }
    .hero .title,
    .title {
        font-size: 23px !important;
        line-height: 1.08 !important;
        letter-spacing: -.35px !important;
    }
    .hero .sub,
    .sub {
        font-size: 11.5px !important;
        line-height: 1.2 !important;
        margin-top: 2px !important;
    }
    .search-label {
        font-size: 15px !important;
        margin: 0 0 4px 2px !important;
    }

    /* Tighten Streamlit controls/captions in the analyzer input row. */
    [data-testid="stSelectbox"] > div > div,
    [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        min-height: 42px !important;
    }
    div[data-testid="stButton"] button[kind="primary"] {
        min-height: 42px !important;
    }
    [data-testid="stCaptionContainer"] p {
        line-height: 1.25 !important;
    }

    /* Pull the first real analysis cards upward. */
    [data-testid="stExpander"] {
        margin-top: 4px !important;
        margin-bottom: 6px !important;
    }

    @media (max-width: 900px) {
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label {
            min-height: 56px !important;
        }
        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(1),
        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(2) {
            flex: 0 0 30% !important;
            width: 30% !important;
            max-width: 30% !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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
