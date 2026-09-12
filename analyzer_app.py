# Deployment refresh: analyzer-plotly-render-v3
from pathlib import Path
import runpy

import streamlit as st
import streamlit.components.v1 as components

from glass_theme import inject_glass_theme

# Compatibility entrypoint for deployments configured to launch analyzer_app.py.
target = Path(__file__).with_name("app.py")
if not target.exists():
    raise FileNotFoundError(
        "app.py was not found in the repository root. "
        "The combined Momentum Scanner + Stock Analyzer requires app.py."
    )

runpy.run_path(str(target), run_name="__main__")
view = st.session_state.get("app_view", "Momentum Scanner")


# Compact presentation layer for the combined workspace. These rules only
# affect appearance; navigation, loading and session-state behavior are unchanged.
if view == "Momentum Scanner":
    st.markdown(
        """
        <style>
        /* app.py emits this legacy title box. The selector itself is the header. */
        .combined-nav-wrap { display: none !important; }

        .st-key-app_view,
        .st-key-app_view > div,
        .st-key-app_view [data-testid="stRadio"],
        .st-key-app_view [data-testid="stRadio"] > div,
        [data-testid="stElementContainer"]:has(.st-key-app_view) {
            width: 100% !important;
            max-width: none !important;
            min-width: 0 !important;
        }

        /* Important: only style the two option labels INSIDE the radio group.
           Styling every label caused Streamlit's hidden "Stock Workspace" label
           to appear as a third giant card. */
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
            display: grid !important;
            grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
            gap: 8px !important;
            width: 100% !important;
            max-width: none !important;
            box-sizing: border-box !important;
            padding: 6px !important;
            margin: 2px 0 18px !important;
            border: 1px solid #30445d !important;
            border-radius: 17px !important;
            background: linear-gradient(135deg, rgba(12,23,39,.96), rgba(8,17,31,.96)) !important;
            box-shadow: 0 6px 18px rgba(0,0,0,.14) !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label {
            position: relative !important;
            display: grid !important;
            grid-template-columns: 56px minmax(0,1fr) !important;
            grid-template-rows: auto auto !important;
            column-gap: 14px !important;
            row-gap: 2px !important;
            align-items: center !important;
            min-height: 98px !important;
            width: 100% !important;
            box-sizing: border-box !important;
            padding: 16px 20px !important;
            border: 1px solid transparent !important;
            border-radius: 13px !important;
            background: transparent !important;
            box-shadow: none !important;
            cursor: pointer !important;
            overflow: hidden !important;
            transition: border-color .15s ease, background .15s ease, box-shadow .15s ease !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:hover {
            border-color: #3c5b77 !important;
            background: rgba(17,31,48,.82) !important;
        }

        /* Keep the real radio accessible but visually replace the tiny dot. */
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {
            position: absolute !important;
            opacity: 0 !important;
            pointer-events: none !important;
            width: 1px !important;
            height: 1px !important;
            overflow: hidden !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::before {
            grid-column: 1 !important;
            grid-row: 1 / 3 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            width: 48px !important;
            height: 48px !important;
            border-radius: 999px !important;
            border: 1.5px solid #304760 !important;
            color: #f4f8ff !important;
            background: rgba(11,22,37,.5) !important;
            font-size: 25px !important;
            line-height: 1 !important;
            font-weight: 750 !important;
            box-sizing: border-box !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(1)::before { content: "↗"; }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(2)::before { content: "⌕"; font-size: 29px !important; }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label [data-testid="stMarkdownContainer"] {
            grid-column: 2 !important;
            grid-row: 1 !important;
            align-self: end !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label p {
            margin: 0 !important;
            color: #f5f9ff !important;
            font-size: 20px !important;
            line-height: 1.08 !important;
            font-weight: 900 !important;
            letter-spacing: -.015em !important;
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::after {
            grid-column: 2 !important;
            grid-row: 2 !important;
            align-self: start !important;
            color: #aebed1 !important;
            font-size: 13px !important;
            line-height: 1.2 !important;
            font-weight: 500 !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(1)::after {
            content: "Discover high-momentum stocks";
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(2)::after {
            content: "Deep dive into any stock";
        }

        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {
            border-color: #4ecb6b !important;
            background: linear-gradient(135deg, #123b25 0%, #0d2f1d 100%) !important;
            box-shadow: 0 0 0 1px rgba(78,203,107,.12), 0 5px 18px rgba(30,125,61,.18) !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked)::before {
            border-color: #3c9f56 !important;
            background: rgba(12,53,31,.68) !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) p {
            color: #ffffff !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked)::after {
            color: #cfdbd6 !important;
        }

        /* Saved Stocks: compact but still clearly separated. */
        .st-key-saved_stocks_top .saved-stock-shell {
            min-height: 86px !important;
            box-sizing: border-box !important;
            padding: 17px 20px 15px !important;
            margin: 0 0 12px !important;
            border: 1px solid #29425f !important;
            border-radius: 14px !important;
            background: linear-gradient(135deg, #0d1a2d, #0b1728) !important;
        }
        .st-key-saved_stocks_top .saved-stock-title {
            font-size: 20px !important;
            line-height: 1.15 !important;
            font-weight: 900 !important;
            color: #f4f8ff !important;
            letter-spacing: -.01em !important;
        }
        .st-key-saved_stocks_top .saved-stock-sub {
            margin-top: 8px !important;
            font-size: 13px !important;
            line-height: 1.3 !important;
            color: #a9bbd1 !important;
        }

        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) {
            gap: 12px !important;
            align-items: stretch !important;
            margin-bottom: 8px !important;
        }
        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(1),
        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(2) {
            flex: 0 0 30% !important;
            width: 30% !important;
            max-width: 30% !important;
        }
        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(3) {
            flex: 1 1 auto !important;
            width: auto !important;
        }

        .st-key-saved_stocks_top .st-key-save_current_stock button,
        .st-key-saved_stocks_top .st-key-remove_current_stock button {
            min-height: 54px !important;
            border-radius: 11px !important;
            font-size: 15px !important;
            font-weight: 850 !important;
            box-shadow: none !important;
        }
        .st-key-saved_stocks_top .st-key-save_current_stock button:not(:disabled) {
            background: linear-gradient(135deg, #215a33, #154525) !important;
            border: 1.5px solid #55c96d !important;
            color: #ffffff !important;
        }
        .st-key-saved_stocks_top .st-key-save_current_stock button:not(:disabled):hover {
            background: linear-gradient(135deg, #286c3c, #19512c) !important;
            border-color: #70dc85 !important;
        }
        .st-key-saved_stocks_top .st-key-remove_current_stock button:not(:disabled) {
            background: #0b1625 !important;
            border: 1.5px solid #3e9654 !important;
            color: #5ec672 !important;
        }
        .st-key-saved_stocks_top .st-key-remove_current_stock button:not(:disabled):hover {
            background: #10241a !important;
            border-color: #58c66f !important;
            color: #78df8d !important;
        }
        .st-key-saved_stocks_top .st-key-save_current_stock button p,
        .st-key-saved_stocks_top .st-key-save_current_stock button span,
        .st-key-saved_stocks_top .st-key-remove_current_stock button p,
        .st-key-saved_stocks_top .st-key-remove_current_stock button span {
            color: inherit !important;
            font-size: 15px !important;
            font-weight: 850 !important;
        }

        .st-key-saved_stocks_top [data-testid="stCaptionContainer"] p {
            font-size: 13px !important;
            line-height: 1.4 !important;
            color: #9fafc2 !important;
        }

        /* Prevent white/washed-out Streamlit secondary buttons. */
        div[data-testid="stButton"] button[kind="secondary"] {
            background: #101b2d !important;
            border: 1px solid #36506d !important;
            color: #eef5ff !important;
            box-shadow: none !important;
        }
        div[data-testid="stButton"] button[kind="secondary"] p,
        div[data-testid="stButton"] button[kind="secondary"] span {
            color: #eef5ff !important;
            font-weight: 800 !important;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover:not(:disabled) {
            background: #153524 !important;
            border-color: #49b66a !important;
            color: #ffffff !important;
        }

        div[data-testid="stButton"] button:disabled {
            background: #0d1624 !important;
            border-color: #26384d !important;
            color: #8396ad !important;
            opacity: .82 !important;
        }
        div[data-testid="stButton"] button:disabled p,
        div[data-testid="stButton"] button:disabled span {
            color: #8396ad !important;
        }

        @media (max-width: 900px) {
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
                grid-template-columns: 1fr !important;
                gap: 6px !important;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label {
                min-height: 82px !important;
                grid-template-columns: 48px minmax(0,1fr) !important;
                column-gap: 12px !important;
                padding: 13px 16px !important;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::before {
                width: 40px !important;
                height: 40px !important;
                font-size: 22px !important;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label p {
                font-size: 18px !important;
            }
            .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::after {
                font-size: 12px !important;
            }
            .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(1),
            .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(2) {
                flex: 1 1 50% !important;
                width: 50% !important;
                max-width: none !important;
            }
        }

        /* ABOVE-THE-FOLD OVERRIDES
           The Analyzer's useful data should be visible immediately on desktop. */
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
            padding: 3px !important;
            gap: 5px !important;
            margin: 0 0 8px !important;
            border-radius: 12px !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label {
            min-height: 54px !important;
            grid-template-columns: 34px minmax(0,1fr) !important;
            grid-template-rows: 1fr !important;
            column-gap: 9px !important;
            padding: 8px 12px !important;
            border-radius: 9px !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::before {
            grid-row: 1 !important;
            width: 30px !important;
            height: 30px !important;
            font-size: 17px !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label:nth-child(2)::before {
            font-size: 19px !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label [data-testid="stMarkdownContainer"] {
            grid-row: 1 !important;
            align-self: center !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label p {
            font-size: 16px !important;
            line-height: 1 !important;
        }
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] > label::after {
            display: none !important;
            content: none !important;
        }

        /* Saved Stocks becomes a compact utility strip instead of a hero section. */
        .st-key-saved_stocks_top .saved-stock-shell {
            min-height: 0 !important;
            padding: 8px 12px !important;
            margin: 0 0 6px !important;
            border-radius: 10px !important;
        }
        .st-key-saved_stocks_top .saved-stock-title {
            font-size: 15px !important;
            line-height: 1 !important;
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
            flex: 0 0 22% !important;
            width: 22% !important;
            max-width: 22% !important;
        }
        .st-key-saved_stocks_top .st-key-save_current_stock button,
        .st-key-saved_stocks_top .st-key-remove_current_stock button {
            min-height: 36px !important;
            height: 36px !important;
            border-radius: 8px !important;
            padding: 4px 10px !important;
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

        /* Combined Analyzer: strip hero-size spacing and tighten search controls. */
        .hero {
            padding: 7px 10px !important;
            margin-bottom: 5px !important;
            border-radius: 9px !important;
            min-height: 0 !important;
        }
        .hero .title {
            font-size: 18px !important;
            line-height: 1.05 !important;
            letter-spacing: -.15px !important;
        }
        .hero .sub {
            display: none !important;
        }
        .search-label {
            font-size: 13px !important;
            margin: 0 0 3px 1px !important;
        }
        [data-testid="stSelectbox"] {
            margin-bottom: 0 !important;
        }
        [data-testid="stSelectbox"] > div > div {
            min-height: 36px !important;
        }
        .block-container {
            padding-top: .25rem !important;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


# Analyzer presentation is owned by analyzer_overview.css.

if view == "Momentum Scanner":
    st.markdown(
        """
        <style>
        /* Keep scanner controls directly below the workspace selector. */
        .st-key-app_view {
            order: -1000 !important;
        }
        .st-key-scanner_controls_top {
            order: -900 !important;
            margin: 0 0 4px !important;
        }
        .st-key-scanner_auto_status_top {
            order: -890 !important;
            margin: 0 0 6px !important;
        }
        .st-key-scanner_controls_top [data-testid="stHorizontalBlock"] {
            align-items: center !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _finish_transition_cleanup():
    st.html(
        """
        <script>
        (() => {
          const p = window;
          const d = p.document;

          const previous = p.__stockWorkspaceTransition;
          if (previous && previous.capture) {
            try { d.removeEventListener('click', previous.capture, true); } catch (_) {}
          }
          p.__stockWorkspaceTransition = null;

          const scroll = p.__ssaScrollKeeper;
          if (!d.querySelector('.st-key-analyzer_live_fragment')) scroll?.dispose?.();

          const working = p.__stockWorkingButtonTransition;
          if (working) {
            try { working.capture && d.removeEventListener('click', working.capture, true); } catch (_) {}
            try { working.click && d.removeEventListener('click', working.click); } catch (_) {}
          }
          p.__stockWorkingButtonTransition = null;

          const mask = d.getElementById('stock-workspace-transition-mask');
          if (mask) mask.remove();
          const staleStyle = d.getElementById('stock-switch-hide-stale');
          if (staleStyle) staleStyle.remove();
          const preserveStyle = d.getElementById('stock-analyze-preserve-scanner');
          if (preserveStyle) preserveStyle.remove();
          try { d.body.style.overflow = ''; } catch (_) {}
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


# analyzer_app.py is the Streamlit Cloud compatibility entrypoint. Its legacy
# presentation CSS is emitted after app.py, so re-apply the shared glass theme
# here to guarantee the new workspace theme wins in the final cascade.
inject_glass_theme()
if view == "Stock Analyzer":
    from analyzer_overview import inject_overview_theme
    inject_overview_theme(st)

if view == "Momentum Scanner":
    _finish_transition_cleanup()
else:
    _finish_transition_cleanup()
