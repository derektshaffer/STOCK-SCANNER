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


def _install_no_fade_css():
    """Keep Streamlit's existing UI fully opaque while an app rerun is active.

    Streamlit intentionally marks old elements as data-stale=true during a
    rerun and lowers their opacity. That is useful for ordinary forms, but it
    makes a polling market dashboard visibly flash every refresh. The old
    values remain on screen until their replacements arrive; this CSS simply
    prevents the stale-state dimming/transition.
    """
    st.markdown(
        """
        <style>
        /* Prevent full-page dim/fade during Streamlit reruns. */
        [data-stale="true"],
        div[data-stale="true"],
        .element-container[data-stale="true"] {
            opacity: 1 !important;
            filter: none !important;
            transition: none !important;
            animation: none !important;
        }

        /* Keep the main dashboard container steady while results refresh. */
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


def _install_scroll_keeper():
    """Preserve the user's viewport across Streamlit full-app reruns.

    The dashboard still performs a full rerun for fresh analysis, but this
    browser-side helper captures the scroll position as soon as Streamlit marks
    the old DOM stale. It then ignores the automatic jump-to-top while the
    rerun is in progress and restores the saved position when the new DOM is
    ready. No market-data or refresh behavior is changed.
    """
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
              p.sessionStorage.setItem(
                KEY,
                JSON.stringify({ y: getY(), t: Date.now() })
              );
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

          // Remove the previous helper before installing the replacement from
          // this rerun. Parent-window references survive component replacement.
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

          // A new component means the new Streamlit DOM is arriving. Restore
          // several times because tables/cards may finish sizing a little later.
          if (saved && Number.isFinite(saved.y) && Date.now() - saved.t < 120000) {
            const y = saved.y;
            p.requestAnimationFrame(() => restorePosition(y));
            p.setTimeout(() => restorePosition(y), 60);
            p.setTimeout(() => restorePosition(y), 180);
            p.setTimeout(() => restorePosition(y), 450);
          }

          const scroller = findScroller();
          const onScroll = () => {
            // Streamlit's own full rerun can force scrollTop to zero. Do not let
            // that programmatic jump overwrite the position captured at stale.
            if (p.__ssaRerunPending) return;
            savePosition();
          };
          if (scroller) scroller.addEventListener("scroll", onScroll, { passive: true });
          p.addEventListener("scroll", onScroll, { passive: true });

          // data-stale=true is set at the beginning of a Streamlit rerun, while
          // the user's current scroll position is still intact. Capture it once.
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

          // Once the replacement DOM has settled, ordinary user scrolling may
          // update the saved position again.
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


def run():
    _preload_secrets()
    _install_no_fade_css()

    import stock_analyzer as sa
    from historical_integration import install_historical_analysis
    from historical_ui import render_historical_setup
    from ml_integration import install_ml_analysis
    from ml_ui import render_ml_prediction

    # Layer order matters: the rule-based analyzer runs first, historical setup
    # matching enhances it second, and ML v1 reads that completed trade plan.
    install_historical_analysis(sa)
    install_ml_analysis(sa)

    target = Path(__file__).with_name("analyzer_ui_core.py")
    if not target.exists():
        raise FileNotFoundError("analyzer_ui_core.py is missing from the repository root.")

    # Reserve a slot immediately before the existing Trade plan details
    # expander. ML v1 and Historical Setup Match are populated there after the
    # core UI finishes and its result/card helpers become available.
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
            # Fallback if the core UI changes and the expander label no longer
            # matches. Better to show the analysis layers at the end than hide them.
            render_ml_prediction(st, pd, result, card)
            render_historical_setup(st, pd, result, card, pp)

    _install_scroll_keeper()
