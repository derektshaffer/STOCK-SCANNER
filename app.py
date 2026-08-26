import json
import os
import runpy
from pathlib import Path

import streamlit as st


st.set_page_config(
    page_title="Stock Momentum + Analyzer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# Shared shell styling. The scanner/analyzer keep their own existing styles.
st.markdown(
    """
    <style>
    .combined-nav-wrap {
        border: 1px solid rgba(120,150,190,.28);
        background: rgba(17,27,46,.92);
        border-radius: 14px;
        padding: 10px 14px 7px;
        margin-bottom: 12px;
    }
    .combined-nav-title {
        font-size: 12px;
        font-weight: 850;
        letter-spacing: .08em;
        text-transform: uppercase;
        color: #91a7c2;
        margin-bottom: 4px;
    }
    .combined-quick {
        border: 1px solid rgba(120,150,190,.24);
        background: rgba(17,27,46,.62);
        border-radius: 12px;
        padding: 12px 14px 10px;
        margin: 2px 0 14px;
    }
    .combined-quick-title {
        font-size: 16px;
        font-weight: 900;
        margin-bottom: 2px;
    }
    .combined-quick-sub {
        color: #91a7c2;
        font-size: 13px;
        margin-bottom: 9px;
    }
    .combined-ticker-row {
        min-height: 46px;
        display: flex;
        align-items: center;
        border-bottom: 1px solid rgba(120,150,190,.14);
    }
    .combined-ticker-symbol {
        font-size: 18px;
        font-weight: 950;
        letter-spacing: .01em;
    }
    .combined-ticker-meta {
        color: #91a7c2;
        font-size: 12px;
        margin-top: 1px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


VIEWS = ("Momentum Scanner", "Stock Analyzer")
if st.session_state.get("app_view") not in VIEWS:
    st.session_state["app_view"] = "Momentum Scanner"

st.markdown(
    '<div class="combined-nav-wrap"><div class="combined-nav-title">Stock Workspace</div></div>',
    unsafe_allow_html=True,
)
view = st.radio(
    "Stock Workspace",
    VIEWS,
    key="app_view",
    horizontal=True,
    label_visibility="collapsed",
)


def _latest_scan_candidates():
    path = Path("scan_logs/latest_scan.json")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    rows = payload.get("candidates") or []
    out = []
    seen = set()
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(
            {
                "symbol": symbol,
                "grade": str(row.get("setup_grade") or "—"),
                "score": row.get("score"),
                "day_pct": row.get("day_pct"),
            }
        )
    return out


def _open_analyzer(symbol):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return

    # Preload the analyzer with the scanner selection and force a fresh result.
    st.session_state["ticker"] = symbol
    st.session_state["ticker_search_request"] = symbol
    st.session_state.pop("ticker_picker", None)
    st.session_state.pop("result", None)
    st.session_state["app_view"] = "Stock Analyzer"


def _fmt_num(value, pattern, fallback="—"):
    try:
        return pattern.format(float(value))
    except (TypeError, ValueError):
        return fallback


if view == "Momentum Scanner":
    candidates = _latest_scan_candidates()
    if candidates:
        st.markdown(
            '<div class="combined-quick">'
            '<div class="combined-quick-title">🔎 One-click Stock Analyzer</div>'
            '<div class="combined-quick-sub">Click Analyze beside any ticker from the latest momentum scan.</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Put an Analyze button directly beside every ticker from the current scan.
        # This replaces the old dropdown so any stock is one click away.
        for idx, row in enumerate(candidates):
            symbol = row["symbol"]
            score_text = _fmt_num(row.get("score"), "{:.0f}")
            day_text = _fmt_num(row.get("day_pct"), "{:+.1f}%")
            left, right = st.columns([5.2, 1.3], vertical_alignment="center")
            with left:
                st.markdown(
                    f'<div class="combined-ticker-row"><div>'
                    f'<div class="combined-ticker-symbol">{symbol}</div>'
                    f'<div class="combined-ticker-meta">Grade {row["grade"]} · Score {score_text} · {day_text} today</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
            with right:
                st.button(
                    f"Analyze {symbol}",
                    key=f"combined_analyze_{idx}_{symbol}",
                    type="primary" if idx < 4 else "secondary",
                    use_container_width=True,
                    on_click=_open_analyzer,
                    args=(symbol,),
                )
    else:
        st.caption("Run a momentum scan to populate the one-click Analyze buttons.")


# Both legacy child apps call st.set_page_config themselves. In the combined
# shell we already configured the page above, so temporarily make those child
# calls a no-op. This preserves analyzer_app.py and scanner_app.py as usable
# standalone entrypoints while avoiding duplicate page-config calls here.
_original_set_page_config = st.set_page_config
st.set_page_config = lambda *args, **kwargs: None
try:
    if view == "Momentum Scanner":
        runpy.run_path(str(Path(__file__).with_name("scanner_app.py")), run_name="__main__")
    else:
        from analyzer_bootstrap import run as run_analyzer

        run_analyzer()
finally:
    st.set_page_config = _original_set_page_config
