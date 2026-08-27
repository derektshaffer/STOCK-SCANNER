from pathlib import Path
import os
import runpy

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


def _preload_secrets():
    """Load Streamlit secrets before stock_analyzer reads its environment."""
    try:
        secrets = dict(st.secrets)
    except Exception:
        return
    for key in (
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "ALPACA_LIVE_FEED",
        "ALPACA_HISTORICAL_FEED",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ANALYZER_REFRESH_SECONDS",
        "SEC_USER_AGENT",
        "ANALYZER_GITHUB_TOKEN",
        "ANALYZER_GITHUB_REPO",
        "ANALYZER_GITHUB_BRANCH",
        "ANALYZER_REMOTE_SYNC_SECONDS",
    ):
        value = secrets.get(key)
        if value is not None and str(value).strip():
            os.environ[key] = str(value).strip()


def _combined_workspace():
    """True when analyzer_bootstrap is being rendered inside app.py."""
    return st.session_state.get("app_view") in ("Momentum Scanner", "Stock Analyzer")


def _install_no_fade_css(combined=False):
    """Prevent fragment-refresh flashing without preserving stale whole pages.

    The previous global [data-stale] override kept detached Analyzer widgets
    visible after switching back to the Scanner. They looked clickable but
    Streamlit had already removed their widget handlers. In the combined app,
    only the live Analyzer fragment gets the no-fade treatment.
    """
    if combined:
        stale_selector = (
            ".st-key-analyzer_live_fragment[data-stale=\"true\"], "
            ".st-key-analyzer_live_fragment [data-stale=\"true\"]"
        )
    else:
        stale_selector = (
            '[data-stale="true"], div[data-stale="true"], '
            '.element-container[data-stale="true"]'
        )

    st.markdown(
        f"""
        <style>
        {stale_selector} {{
            opacity: 1 !important;
            filter: none !important;
            transition: none !important;
            animation: none !important;
        }}
        [data-testid="stAppViewBlockContainer"],
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        .stApp,
        .stApp > div,
        .element-container {{
            transition: none !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _cleanup_combined_browser_helpers():
    """Retire Scanner-only browser helpers without mutating Streamlit nodes."""
    components.html(
        """
        <script>
        (() => {
          const p = window.parent;
          const d = p.document;

          const scroll = p.__ssaScrollKeeper;
          if (scroll) {
            try { scroll.observer && scroll.observer.disconnect(); } catch (_) {}
            try { scroll.scroller && scroll.onScroll && scroll.scroller.removeEventListener('scroll', scroll.onScroll); } catch (_) {}
            try { scroll.onWindowScroll && p.removeEventListener('scroll', scroll.onWindowScroll); } catch (_) {}
          }
          p.__ssaScrollKeeper = null;
          p.__ssaRerunPending = false;

          const expander = p.__scannerExpandController;
          if (expander) {
            try { d.removeEventListener('click', expander.click); } catch (_) {}
            try { d.removeEventListener('keydown', expander.keydown); } catch (_) {}
          }
          p.__scannerExpandController = null;

          const ux = p.__scannerUXPatch;
          if (ux) {
            try { d.removeEventListener('click', ux.captureClick, true); } catch (_) {}
            try { d.removeEventListener('keydown', ux.keydown); } catch (_) {}
          }
          p.__scannerUXPatch = null;

          // Expanded scanner cards are browser-injected and are not owned by
          // Streamlit, so removing those is safe. Do NOT set inline display or
          // visibility styles on Streamlit's stale nodes: React can recycle
          // them for the Analyzer, which caused missing cards and blank areas.
          d.querySelectorAll('.scanner-inline-detail').forEach((node) => node.remove());
          if (p.__scannerExpandedSymbols && p.__scannerExpandedSymbols.clear) {
            p.__scannerExpandedSymbols.clear();
          }

          const tips = p.__stockTechnicalTooltips;
          if (tips && tips.observer) {
            try { tips.observer.disconnect(); } catch (_) {}
          }
          const tipBox = d.getElementById('stock-tech-tooltip');
          if (tipBox) tipBox.style.display = 'none';
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _install_scroll_keeper():
    """Preserve viewport across full-app reruns in the standalone analyzer."""
    components.html(
        """
        <script>
        (() => {
          const p = window.parent;
          const d = p.document;
          const KEY = "ssa-scroll-position-v2";

          function findScroller() {
            const selectors = [
              '[data-testid="stAppViewContainer"]',
              '[data-testid="stMain"]',
              'section.main'
            ];
            for (const selector of selectors) {
              const el = d.querySelector(selector);
              if (el && el.scrollHeight > el.clientHeight + 20) return el;
            }
            return d.scrollingElement || d.documentElement;
          }

          function getY() {
            const scroller = findScroller();
            if (scroller && typeof scroller.scrollTop === "number") {
              return Math.max(0, scroller.scrollTop);
            }
            return Math.max(0, p.scrollY || d.documentElement.scrollTop || 0);
          }

          function savePosition() {
            try {
              p.sessionStorage.setItem(KEY, JSON.stringify({ y: getY(), t: Date.now() }));
            } catch (_) {}
          }

          function restorePosition(y) {
            const scroller = findScroller();
            try {
              if (scroller && scroller !== d.scrollingElement && scroller !== d.documentElement) {
                scroller.scrollTop = y;
              } else {
                p.scrollTo(0, y);
              }
            } catch (_) {}
          }

          const old = p.__ssaScrollKeeper;
          if (old) {
            try { old.observer && old.observer.disconnect(); } catch (_) {}
            try { old.scroller && old.onScroll && old.scroller.removeEventListener("scroll", old.onScroll); } catch (_) {}
            try { old.onWindowScroll && p.removeEventListener("scroll", old.onWindowScroll); } catch (_) {}
          }

          let saved = null;
          try {
            const raw = p.sessionStorage.getItem(KEY);
            saved = raw ? JSON.parse(raw) : null;
          } catch (_) {}

          if (saved && Number.isFinite(saved.y) && Date.now() - saved.t < 120000) {
            const y = saved.y;
            p.requestAnimationFrame(() => restorePosition(y));
            p.setTimeout(() => restorePosition(y), 60);
            p.setTimeout(() => restorePosition(y), 180);
            p.setTimeout(() => restorePosition(y), 450);
          }

          const scroller = findScroller();
          const onScroll = () => {
            if (p.__ssaRerunPending) return;
            savePosition();
          };
          if (scroller) scroller.addEventListener("scroll", onScroll, { passive: true });
          p.addEventListener("scroll", onScroll, { passive: true });

          const observer = new MutationObserver((mutations) => {
            for (const m of mutations) {
              if (
                m.type === "attributes" &&
                m.attributeName === "data-stale" &&
                m.target &&
                m.target.getAttribute("data-stale") === "true"
              ) {
                if (!p.__ssaRerunPending) {
                  savePosition();
                  p.__ssaRerunPending = true;
                }
                break;
              }
            }
          });
          if (d.body) {
            observer.observe(d.body, {
              subtree: true,
              attributes: true,
              attributeFilter: ["data-stale"]
            });
          }

          p.__ssaScrollKeeper = {
            observer,
            scroller,
            onScroll,
            onWindowScroll: onScroll
          };

          p.setTimeout(() => {
            p.__ssaRerunPending = false;
            savePosition();
          }, 700);
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _activate_saved_stock(symbol):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return
    st.session_state["ticker"] = symbol
    st.session_state["ticker_search_request"] = symbol
    st.session_state.pop("ticker_picker", None)
    st.session_state.pop("result", None)


def _render_saved_stocks():
    """Compact session-persistent saved-stock toolbar."""
    if "saved_stocks" not in st.session_state:
        st.session_state["saved_stocks"] = []

    saved = [
        str(x).upper().strip()
        for x in st.session_state.get("saved_stocks", [])
        if str(x).strip()
    ]
    saved = list(dict.fromkeys(saved))[:24]
    st.session_state["saved_stocks"] = saved

    current = str(
        st.session_state.get("ticker_search_request")
        or st.session_state.get("ticker")
        or "SDOT"
    ).upper().strip()

    # One compact toolbar row instead of a header card + separate action row.
    title_col, action_a, action_b, spacer = st.columns(
        [1.15, 1.05, 1.05, 4.75],
        vertical_alignment="center",
    )
    with title_col:
        st.markdown(
            '<div class="saved-stock-inline-title">★ Saved Stocks</div>',
            unsafe_allow_html=True,
        )

    with action_a:
        can_save = bool(current) and current not in saved
        if st.button(
            f"☆ Save {current}" if current else "☆ Save current",
            key="save_current_stock",
            disabled=not can_save,
            use_container_width=True,
        ):
            st.session_state["saved_stocks"] = (saved + [current])[:24]
            st.rerun()

    with action_b:
        can_remove = bool(current) and current in saved
        if st.button(
            f"Remove {current}",
            key="remove_current_stock",
            disabled=not can_remove,
            use_container_width=True,
        ):
            st.session_state["saved_stocks"] = [x for x in saved if x != current]
            st.rerun()

    saved = st.session_state.get("saved_stocks", [])
    if not saved:
        return

    # Saved ticker chips only add a second compact row when there are actually
    # saved names to show.
    for start in range(0, len(saved), 8):
        chunk = saved[start : start + 8]
        cols = st.columns(8)
        for i, col in enumerate(cols):
            if i >= len(chunk):
                continue
            symbol = chunk[i]
            with col:
                if st.button(
                    f"● {symbol}" if symbol == current else symbol,
                    key=f"saved_stock_{start+i}_{symbol}",
                    type="primary" if symbol == current else "secondary",
                    use_container_width=True,
                ):
                    _activate_saved_stock(symbol)
                    st.rerun()

def _prepare_combined_result(sa):
    """Calculate the selected ticker without rendering Analyzer UI."""
    ticker = str(
        st.session_state.get("ticker_search_request")
        or st.session_state.get("ticker")
        or "SDOT"
    ).upper().strip()
    if not ticker:
        return "No ticker was selected."

    existing = st.session_state.get("result")
    existing_symbol = str((existing or {}).get("symbol") or "").upper().strip() if isinstance(existing, dict) else ""
    state_symbol = str(st.session_state.get("ticker") or "").upper().strip()

    if isinstance(existing, dict) and existing and state_symbol == ticker and (not existing_symbol or existing_symbol == ticker):
        return None

    try:
        result = sa.analyze(ticker)
    except Exception as exc:
        return str(exc)

    st.session_state["result"] = result
    st.session_state["ticker"] = ticker
    st.session_state["ticker_search_request"] = ticker
    st.session_state.pop("ticker_picker", None)
    return None


def prepare_analyzer_result(symbol):
    """Pre-calculate a scanner-launched analysis before switching views.

    This function is called from the Scanner button callback. Because Streamlit
    runs callbacks before the rest of the app rerun, the current Scanner stays
    on screen while the market/history/ML analysis completes. Only after this
    returns does app.py switch ``app_view`` to the Analyzer.
    """
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return "No ticker was selected."

    _preload_secrets()
    st.session_state["ticker"] = symbol
    st.session_state["ticker_search_request"] = symbol
    st.session_state.pop("ticker_picker", None)
    st.session_state.pop("result", None)

    import stock_analyzer as sa
    from historical_integration import install_historical_analysis
    from ml_integration import install_ml_analysis
    from analyzer_v2_integration import install_v2_analysis

    install_historical_analysis(sa)
    install_ml_analysis(sa)
    install_v2_analysis(sa)
    return _prepare_combined_result(sa)


def run():
    _preload_secrets()
    combined = _combined_workspace()
    # Fragment reruns should never dim the Analyzer. Scope this CSS to stale
    # Streamlit elements so numbers can update without the page flashing.
    _install_no_fade_css(combined=combined)

    import stock_analyzer as sa
    from historical_integration import install_historical_analysis
    from ml_integration import install_ml_analysis
    from analyzer_v2_integration import install_v2_analysis

    install_historical_analysis(sa)
    install_ml_analysis(sa)
    install_v2_analysis(sa)

    launch_error = None
    if combined:
        # Scanner-launched analyses are normally already calculated by the
        # button callback. This is a fast no-op in that case and remains a
        # fallback for direct/manual switches to the Analyzer.
        launch_error = _prepare_combined_result(sa)
        _cleanup_combined_browser_helpers()
        with st.container(key="saved_stocks_top"):
            _render_saved_stocks()

        if launch_error:
            st.error(f"Could not analyze the selected ticker: {launch_error}")
            return

    from historical_ui import render_historical_setup
    from ml_ui import render_ml_prediction

    target = Path(__file__).with_name("analyzer_ui_core.py")
    if not target.exists():
        raise FileNotFoundError("analyzer_ui_core.py is missing from the repository root.")

    try:
        refresh_seconds = max(
            5, int(os.environ.get("ANALYZER_REFRESH_SECONDS", "15") or 15)
        )
    except Exception:
        refresh_seconds = 15

    @st.fragment(run_every=f"{refresh_seconds}s")
    def _render_live_analyzer():
        # Only this fragment reruns on the timer. app.py's navigation and the
        # rest of the workspace stay mounted, eliminating full-page refreshes.
        with st.container(key="analyzer_live_fragment"):
            original_expander = st.expander
            analysis_slot = {"placeholder": None}

            def _expander_with_analysis_slot(label, *args, **kwargs):
                if (
                    analysis_slot["placeholder"] is None
                    and str(label).startswith("Trade plan details")
                ):
                    analysis_slot["placeholder"] = st.empty()
                return original_expander(label, *args, **kwargs)

            st.expander = _expander_with_analysis_slot
            try:
                ns = runpy.run_path(str(target), run_name="__main__")
            finally:
                st.expander = original_expander

            result = ns.get("r") or {}
            card = ns.get("card")
            pp = ns.get("pp")

            if card and pp:
                slot = analysis_slot.get("placeholder")

                def _render_analysis_sections():
                    with st.container(key="ml_prediction_section"):
                        render_ml_prediction(st, pd, result, card)
                    with st.container(key="historical_match_section"):
                        render_historical_setup(st, pd, result, card, pp)

                if slot is not None:
                    with slot.container():
                        _render_analysis_sections()
                else:
                    _render_analysis_sections()

    _render_live_analyzer()

    if not combined:
        _install_scroll_keeper()
