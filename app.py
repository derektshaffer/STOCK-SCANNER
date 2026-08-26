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
        padding: 10px 12px 2px;
        margin: 2px 0 14px;
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


if view == "Momentum Scanner":
    candidates = _latest_scan_candidates()
    if candidates:
        labels = {}
        for row in candidates:
            score = row.get("score")
            day = row.get("day_pct")
            score_text = f"{float(score):.0f}" if score is not None else "—"
            day_text = f"{float(day):+.1f}%" if day is not None else "—"
            label = f'{row["symbol"]}  ·  Grade {row["grade"]}  ·  Score {score_text}  ·  {day_text} today'
            labels[label] = row["symbol"]

        st.markdown('<div class="combined-quick">', unsafe_allow_html=True)
        q1, q2 = st.columns([4, 1.25])
        with q1:
            quick_label = st.selectbox(
                "Quick Analyze — choose a ticker from the latest momentum scan",
                list(labels.keys()),
                index=0,
                key="combined_quick_analyze",
            )
        with q2:
            st.write("")
            st.write("")
            st.button(
                "🔎 Analyze Stock",
                type="primary",
                use_container_width=True,
                on_click=_open_analyzer,
                args=(labels.get(quick_label),),
            )
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.caption("Run a momentum scan to populate the Quick Analyze ticker picker.")


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
