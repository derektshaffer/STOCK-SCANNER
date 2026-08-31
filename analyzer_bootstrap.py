from pathlib import Path
import os
import runpy
import threading
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from analyzer_runtime_context import set_analyzer_namespace


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
        "ANALYZER_STREAM_UI_SECONDS",
        "TRADIER_ACCESS_TOKEN",
        "TRADIER_TOKEN",
        "INTRINIO_API_KEY",
    ):
        value = secrets.get(key)
        if value is not None and str(value).strip():
            os.environ[key] = str(value).strip()


def _start_async_prediction_sync():
    existing=st.session_state.get("_prediction_sync_thread")
    if existing is not None and getattr(existing,"is_alive",lambda:False)():
        return
    try:
        from prediction_tracker import sync_predictions_remote
    except Exception:
        return

    def _run_sync():
        try:
            sync_predictions_remote(force=True)
        except Exception:
            pass

    thread=threading.Thread(
        target=_run_sync,
        daemon=True,
        name="analyzer-prediction-sync",
    )
    st.session_state["_prediction_sync_thread"]=thread
    thread.start()


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
            ".st-key-analyzer_live_fragment [data-stale=\"true\"], "
            ".element-container[data-stale=\"true\"]:has(.st-key-analyzer_live_fragment), "
            ".st-key-analyzer_fast_live_tape[data-stale=\"true\"], "
            ".st-key-analyzer_fast_live_tape [data-stale=\"true\"], "
            ".element-container[data-stale=\"true\"]:has(.st-key-analyzer_fast_live_tape), "
            ".st-key-saved_stocks_top[data-stale=\"true\"], "
            ".st-key-saved_stocks_top [data-stale=\"true\"], "
            ".element-container[data-stale=\"true\"]:has(.st-key-saved_stocks_top)"
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

        /* Shared Analyzer control colors.
           The combined workspace launches through analyzer_bootstrap.py, so
           these styles must live here rather than only in analyzer_app.py. */
        .st-key-saved_stocks_top button[data-testid="stBaseButton-secondary"],
        .st-key-saved_stocks_top button[kind="secondary"],
        .st-key-analyzer_live_fragment button[data-testid="stBaseButton-secondary"],
        .st-key-analyzer_live_fragment button[kind="secondary"],
        .st-key-analyzer_live_fragment [data-testid="stPopover"] button,
        .st-key-analyzer_controls [data-testid="stPopover"] button {{
            background: #11243a !important;
            border: 1px solid #365878 !important;
            color: #edf5ff !important;
            box-shadow: none !important;
            opacity: 1 !important;
        }}

        .st-key-saved_stocks_top button[data-testid="stBaseButton-secondary"] *,
        .st-key-saved_stocks_top button[kind="secondary"] *,
        .st-key-analyzer_live_fragment button[data-testid="stBaseButton-secondary"] *,
        .st-key-analyzer_live_fragment button[kind="secondary"] *,
        .st-key-analyzer_live_fragment [data-testid="stPopover"] button *,
        .st-key-analyzer_controls [data-testid="stPopover"] button * {{
            color: #edf5ff !important;
            fill: #edf5ff !important;
            opacity: 1 !important;
        }}

        .st-key-saved_stocks_top button[data-testid="stBaseButton-secondary"]:hover:not(:disabled),
        .st-key-saved_stocks_top button[kind="secondary"]:hover:not(:disabled),
        .st-key-analyzer_live_fragment button[data-testid="stBaseButton-secondary"]:hover:not(:disabled),
        .st-key-analyzer_live_fragment button[kind="secondary"]:hover:not(:disabled),
        .st-key-analyzer_live_fragment [data-testid="stPopover"] button:hover:not(:disabled),
        .st-key-analyzer_controls [data-testid="stPopover"] button:hover:not(:disabled) {{
            background: #18314d !important;
            border-color: #5b86ad !important;
            color: #ffffff !important;
        }}

        .st-key-saved_stocks_top button:disabled,
        .st-key-analyzer_live_fragment button[data-testid="stBaseButton-secondary"]:disabled,
        .st-key-analyzer_live_fragment button[kind="secondary"]:disabled,
        .st-key-analyzer_live_fragment [data-testid="stPopover"] button:disabled,
        .st-key-analyzer_controls [data-testid="stPopover"] button:disabled {{
            background: #0d1a2b !important;
            border-color: #263d57 !important;
            color: #8095ab !important;
            opacity: 1 !important;
        }}

        .st-key-saved_stocks_top button:disabled *,
        .st-key-analyzer_live_fragment button[data-testid="stBaseButton-secondary"]:disabled *,
        .st-key-analyzer_live_fragment button[kind="secondary"]:disabled *,
        .st-key-analyzer_live_fragment [data-testid="stPopover"] button:disabled *,
        .st-key-analyzer_controls [data-testid="stPopover"] button:disabled * {{
            color: #8095ab !important;
            fill: #8095ab !important;
            opacity: 1 !important;
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
    st.session_state["saved_stock_loading"] = symbol
    st.session_state.pop("ticker_picker", None)
    st.session_state.pop("result", None)


def _render_saved_stocks(key_prefix="saved"):
    """Compact session-persistent saved-stock toolbar.

    Keep the title, Save/Remove actions, and the first saved tickers on one
    horizontal row. Additional saved names only create extra rows when needed.
    """
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

    first_saved = saved[:5]
    weights = [1.15, 1.15, 1.15] + [0.95] * len(first_saved)
    if not first_saved:
        weights += [3.0]

    cols = st.columns(weights, vertical_alignment="center")
    title_col, action_a, action_b = cols[:3]
    saved_cols = cols[3:3 + len(first_saved)]

    with title_col:
        st.markdown(
            '<div class="saved-stock-inline-title">★ Saved Stocks</div>',
            unsafe_allow_html=True,
        )

    with action_a:
        can_save = bool(current) and current not in saved
        if st.button(
            f"☆ Save {current}" if current else "☆ Save current",
            key=f"{key_prefix}_save_current_stock",
            disabled=not can_save,
            use_container_width=True,
        ):
            st.session_state["saved_stocks"] = (saved + [current])[:24]
            st.rerun()

    with action_b:
        can_remove = bool(current) and current in saved
        if st.button(
            f"Remove {current}",
            key=f"{key_prefix}_remove_current_stock",
            disabled=not can_remove,
            use_container_width=True,
        ):
            st.session_state["saved_stocks"] = [x for x in saved if x != current]
            st.rerun()

    loading_symbol = str(
        st.session_state.get("saved_stock_loading") or ""
    ).upper().strip()

    for i, (col, symbol) in enumerate(zip(saved_cols, first_saved)):
        is_loading = loading_symbol == symbol
        with col:
            if st.button(
                "Analyzing..." if is_loading else (
                    f"● {symbol}" if symbol == current else symbol
                ),
                key=f"{key_prefix}_saved_stock_{i}_{symbol}",
                type="primary" if symbol == current else "secondary",
                disabled=bool(is_loading),
                use_container_width=True,
            ):
                _activate_saved_stock(symbol)
                st.rerun()

    remaining = saved[5:]
    for start in range(0, len(remaining), 8):
        chunk = remaining[start : start + 8]
        row_cols = st.columns(8)
        for i, col in enumerate(row_cols):
            if i >= len(chunk):
                continue
            symbol = chunk[i]
            is_loading = loading_symbol == symbol
            with col:
                if st.button(
                    "Analyzing..." if is_loading else (
                        f"● {symbol}" if symbol == current else symbol
                    ),
                    key=f"{key_prefix}_saved_stock_more_{start+i}_{symbol}",
                    type="primary" if symbol == current else "secondary",
                    disabled=bool(is_loading),
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
        st.session_state.pop("saved_stock_loading", None)
        return str(exc)

    st.session_state["result"] = result
    st.session_state["ticker"] = ticker
    st.session_state["ticker_search_request"] = ticker
    st.session_state.pop("ticker_picker", None)
    st.session_state.pop("saved_stock_loading", None)
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
    thesis_namespace=st.session_state.setdefault(
        "_analyzer_thesis_namespace",
        uuid.uuid4().hex,
    )
    # Standalone Analyzer executes in this process; ContextVar keeps its
    # namespace isolated from other Streamlit sessions sharing the process.
    # Combined mode also passes the same namespace into its subprocess.
    set_analyzer_namespace(thesis_namespace)
    combined = _combined_workspace()
    # Fragment reruns should never dim the Analyzer. Scope this CSS to stale
    # Streamlit elements so numbers can update without the page flashing.
    _install_no_fade_css(combined=combined)

    if combined:
        _cleanup_combined_browser_helpers()

        # Never run a fresh Analyzer calculation inside the full-app render.
        # A deep analysis can take long enough to block Streamlit's session,
        # which freezes BOTH workspace tabs and prevents the 2-minute scanner
        # monitor from polling. Start it in the existing cancelable subprocess
        # runtime instead, then poll it from a tiny fragment.
        requested_ticker = str(
            st.session_state.get("_analyzer_background_request_symbol")
            or st.session_state.get("ticker_search_request")
            or st.session_state.get("ticker")
            or "SDOT"
        ).upper().strip()
        existing_result = st.session_state.get("result")

        # Saved-stock/search launches can bypass app.py's scanner button path.
        # Give them the same per-session warm-result behavior.
        cached_entry=(
            (st.session_state.get("_analyzer_result_cache") or {})
            .get(requested_ticker)
            or {}
        )
        cached_result=cached_entry.get("result")
        cached_at=float(cached_entry.get("cached_at") or 0.0)
        if (
            isinstance(cached_result,dict)
            and cached_result
            and cached_at
            and __import__("time").time()-cached_at <= 900
            and (
                not isinstance(existing_result,dict)
                or str(existing_result.get("symbol") or "").upper().strip()
                != requested_ticker
            )
        ):
            existing_result=cached_result
            st.session_state["result"]=cached_result

        existing_symbol = str(
            (existing_result or {}).get("symbol") or ""
        ).upper().strip() if isinstance(existing_result, dict) else ""

        from analyzer_launch_runtime import (
            cancel_analyzer_process,
            poll_analyzer_process,
            start_analyzer_process,
        )

        launch_key = "_analyzer_bootstrap_launch_state"
        launch_state = st.session_state.get(launch_key)
        launch_process = (launch_state or {}).get("process")
        launch_symbol = str(
            (launch_state or {}).get("symbol") or ""
        ).upper().strip()
        launch_active_same_symbol = bool(
            launch_process is not None
            and launch_process.poll() is None
            and launch_symbol == requested_ticker
        )
        forced_refresh = bool(
            str(
                st.session_state.get("_analyzer_background_request_symbol") or ""
            ).upper().strip()
            == requested_ticker
        )
        result_ready = bool(
            requested_ticker
            and isinstance(existing_result, dict)
            and existing_result
            and (not existing_symbol or existing_symbol == requested_ticker)
            and not forced_refresh
            and not launch_active_same_symbol
        )

        if not result_ready:
            launch_state = st.session_state.get(launch_key)
            launch_symbol = str(
                (launch_state or {}).get("symbol") or ""
            ).upper().strip()
            launch_process = (launch_state or {}).get("process")
            launch_active = bool(
                launch_process is not None
                and launch_process.poll() is None
            )

            # A saved-ticker click can change the requested symbol while a
            # previous launch is still running. Cancel the obsolete work rather
            # than letting it overwrite the newly requested analysis later.
            if launch_state and launch_symbol != requested_ticker:
                cancel_analyzer_process(launch_state)
                st.session_state[launch_key] = None
                launch_state = None
                launch_active = False

            if not launch_state:
                launch_state = start_analyzer_process(
                    requested_ticker,
                    alpaca_key=os.environ.get("ALPACA_API_KEY", ""),
                    alpaca_secret=os.environ.get("ALPACA_SECRET_KEY", ""),
                    alpaca_live_feed=os.environ.get("ALPACA_LIVE_FEED", "iex"),
                    tradier_token=(
                        os.environ.get("TRADIER_ACCESS_TOKEN", "")
                        or os.environ.get("TRADIER_TOKEN", "")
                    ),
                    thesis_namespace=thesis_namespace,
                    timeout_seconds=180,
                )
                if not launch_state.get("started"):
                    st.error(
                        "Could not start Analyzer: "
                        + str(launch_state.get("message") or "unknown error")
                    )
                    return
                st.session_state[launch_key] = launch_state
                st.session_state.pop("_analyzer_background_request_symbol", None)
                launch_active = True

            def _cancel_combined_loader():
                state = st.session_state.get(launch_key)
                if state:
                    cancelled = cancel_analyzer_process(state)
                    st.session_state["_analyzer_cancel_notice"] = (
                        cancelled.get("message") or "Analysis cancelled."
                    )
                st.session_state[launch_key] = None
                st.session_state["_analyzer_launch_state"] = None
                st.session_state["_analyzer_loading"] = False
                st.session_state["_manual_analyze_requested"] = False
                st.session_state.pop("_analyzer_background_request_symbol", None)
                st.session_state["app_view"] = "Momentum Scanner"

            @st.fragment(run_every="1s")
            def _render_combined_analysis_loader():
                state = st.session_state.get(launch_key)
                if not state:
                    return

                st.markdown(
                    f'<div class="hero"><div class="title">Single Stock Analyzer</div>'
                    f'<div class="sub">Loading deep analysis for '
                    f'{requested_ticker}…</div></div>',
                    unsafe_allow_html=True,
                )

                outcome = poll_analyzer_process(state)
                if outcome.get("done"):
                    st.session_state[launch_key] = None
                    if not outcome.get("ok"):
                        st.session_state["_analyzer_loading"] = False
                        st.session_state["_manual_analyze_requested"] = False
                        st.session_state.pop("_analyzer_background_request_symbol", None)
                        st.error(
                            "Analyzer failed: "
                            + str(outcome.get("message") or "unknown error")
                        )
                        return

                    symbol = str(
                        outcome.get("symbol") or requested_ticker
                    ).upper().strip()
                    st.session_state["result"] = outcome.get("result")
                    cache=st.session_state.setdefault("_analyzer_result_cache",{})
                    cache[symbol]={
                        "result": outcome.get("result"),
                        "cached_at": __import__("time").time(),
                    }
                    _start_async_prediction_sync()
                    st.session_state["ticker"] = symbol
                    st.session_state["ticker_search_request"] = symbol
                    st.session_state["_analyzer_loading"] = False
                    st.session_state["_manual_analyze_requested"] = False
                    st.session_state.pop("_analyzer_background_request_symbol", None)
                    st.session_state.pop("ticker_picker", None)
                    st.session_state.pop("saved_stock_loading", None)
                    st.rerun(scope="app")
                    return

                elapsed = float(outcome.get("runtime_seconds") or 0.0)
                status_col, cancel_col = st.columns(
                    [4.5, 1.2],
                    vertical_alignment="center",
                )
                with status_col:
                    st.info(
                        f"Analyzing {requested_ticker} in the background… "
                        f"{elapsed:.0f}s elapsed. You are already in Analyzer; "
                        "the full analysis will appear here as soon as it finishes."
                    )
                with cancel_col:
                    st.button(
                        f"Cancel {requested_ticker}",
                        key=f"cancel_combined_loader_{requested_ticker}",
                        use_container_width=True,
                        on_click=_cancel_combined_loader,
                    )

            _render_combined_analysis_loader()

            # If this is a refresh of the stock already on screen, keep the
            # complete Analyzer page rendered from the last good result while
            # the background worker calculates the replacement. This avoids
            # the half-Analyzer / stale-Scanner transition screen and makes
            # the Analyze button feel immediate. Only wait on a loader-only
            # page when there is no correct existing result for this ticker.
            can_render_existing = bool(
                isinstance(existing_result, dict)
                and existing_result
                and existing_symbol == requested_ticker
            )
            if not can_render_existing:
                return

    import stock_analyzer as sa
    from historical_integration import install_historical_analysis
    from ml_integration import install_ml_analysis
    from analyzer_v2_integration import install_v2_analysis

    install_historical_analysis(sa)
    install_ml_analysis(sa)
    install_v2_analysis(sa)

    from historical_ui import render_historical_setup
    from ml_ui import render_ml_prediction
    from live_tape_ui import render_live_tape
    from live_market_stream import get_live_overlay

    target = Path(__file__).with_name("analyzer_ui_core.py")
    if not target.exists():
        raise FileNotFoundError("analyzer_ui_core.py is missing from the repository root.")

    try:
        refresh_seconds = max(
            30, int(os.environ.get("ANALYZER_REFRESH_SECONDS", "60") or 60)
        )
    except Exception:
        refresh_seconds = 15

    try:
        stream_ui_seconds = max(
            1, int(os.environ.get("ANALYZER_STREAM_UI_SECONDS", "2") or 2)
        )
    except Exception:
        stream_ui_seconds = 2

    @st.fragment(run_every=f"{stream_ui_seconds}s")
    def _render_fast_live_tape():
        result = st.session_state.get("result") or {}
        if not isinstance(result, dict) or not result.get("symbol"):
            return
        overlay = get_live_overlay(result)
        with st.container(key="analyzer_fast_live_tape"):
            render_live_tape(st, overlay)

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
                ns = runpy.run_path(
                    str(target),
                    run_name="__main__",
                    init_globals={
                        "_render_combined_saved_stocks": _render_saved_stocks,
                    },
                )
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

    _render_fast_live_tape()
    _render_live_analyzer()

    if not combined:
        _install_scroll_keeper()
