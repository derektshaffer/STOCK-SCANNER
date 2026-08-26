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
    ):
        value = secrets.get(key)
        if value is not None and str(value).strip():
            os.environ[key] = str(value).strip()


def _combined_workspace():
    """True when analyzer_bootstrap is being rendered inside app.py."""
    return st.session_state.get("app_view") in ("Momentum Scanner", "Stock Analyzer")


def _install_no_fade_css():
    """Keep a standalone analyzer fully opaque while a rerun is active."""
    st.markdown(
        """
        <style>
        [data-stale="true"],
        div[data-stale="true"],
        .element-container[data-stale="true"] {
            opacity: 1 !important;
            filter: none !important;
            transition: none !important;
            animation: none !important;
        }
        [data-testid="stAppViewBlockContainer"],
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        .stApp,
        .stApp > div,
        .element-container {
            transition: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _cleanup_combined_browser_helpers():
    """Remove Scanner-only browser state before the Analyzer does heavy work.

    The expanded scanner cards are injected directly into the browser DOM, so
    Streamlit does not own them and cannot reliably remove them during a rerun.
    Clean them up immediately when entering the Analyzer. Also stop the old
    scroll/tooltip observers so they do not watch the Analyzer build.
    """
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
    """Session-persistent one-click stock list pinned to the top of Analyzer."""
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

    st.markdown(
        """
        <style>
        /* The keyed container is a top-level Streamlit block. Give it the
           earliest flex order so Saved Stocks stays at the top of the page. */
        .st-key-saved_stocks_top { order: -1000 !important; }
        .saved-stock-shell{
            border:1px solid #263e5c;background:#0d192a;border-radius:14px;
            padding:12px 14px 9px;margin:4px 0 10px;
        }
        .saved-stock-title{font-size:15px;font-weight:900;color:#f2f7ff}
        .saved-stock-sub{font-size:12px;color:#91a7c2;margin-top:3px}
        </style>
        <div class="saved-stock-shell">
          <div class="saved-stock-title">★ Saved Stocks</div>
          <div class="saved-stock-sub">Save tickers you want to revisit, then click one to analyze it immediately.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    action_a, action_b, spacer = st.columns([1.25, 1.35, 5.4])
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
        st.caption("No saved stocks yet. Analyze a ticker, then click **Save** above.")
        return

    for start in range(0, len(saved), 6):
        chunk = saved[start : start + 6]
        cols = st.columns(6)
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
                    help=f"Analyze {symbol}",
                ):
                    _activate_saved_stock(symbol)
                    st.rerun()


def run():
    _preload_secrets()
    combined = _combined_workspace()
    if combined:
        # Remove browser-injected Scanner content before any heavy Analyzer
        # imports/data calls begin, so old Scanner cards cannot linger onscreen.
        _cleanup_combined_browser_helpers()

        # Render Saved Stocks before the Analyzer's heavier imports and UI.
        # The keyed container keeps this block pinned at the top of the page.
        with st.container(key="saved_stocks_top"):
            _render_saved_stocks()
    else:
        _install_no_fade_css()

    import stock_analyzer as sa
    from historical_integration import install_historical_analysis
    from historical_ui import render_historical_setup
    from ml_integration import install_ml_analysis
    from ml_ui import render_ml_prediction

    install_historical_analysis(sa)
    install_ml_analysis(sa)

    target = Path(__file__).with_name("analyzer_ui_core.py")
    if not target.exists():
        raise FileNotFoundError("analyzer_ui_core.py is missing from the repository root.")

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
        if slot is not None:
            with slot.container():
                render_ml_prediction(st, pd, result, card)
                render_historical_setup(st, pd, result, card, pp)
        else:
            render_ml_prediction(st, pd, result, card)
            render_historical_setup(st, pd, result, card, pp)

    if not combined:
        _install_scroll_keeper()
