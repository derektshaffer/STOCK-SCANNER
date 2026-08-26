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
        min-height: 68px;
        display: grid;
        grid-template-columns: minmax(125px, 1.4fr) repeat(4, minmax(92px, 1fr));
        gap: 10px;
        align-items: stretch;
        border-bottom: 1px solid rgba(120,150,190,.18);
        padding: 7px 0;
    }
    .combined-ticker-symbol-wrap,
    .combined-stat {
        display: flex;
        flex-direction: column;
        justify-content: center;
        min-width: 0;
    }
    .combined-ticker-symbol {
        font-size: 24px;
        line-height: 1.05;
        font-weight: 950;
        letter-spacing: .01em;
        color: #f4f7fb;
    }
    .combined-ticker-caption {
        color: #91a7c2;
        font-size: 11px;
        font-weight: 800;
        letter-spacing: .07em;
        margin-top: 5px;
    }
    .combined-stat {
        background: rgba(22,35,58,.72);
        border: 1px solid rgba(120,150,190,.20);
        border-radius: 10px;
        padding: 8px 12px;
    }
    .combined-stat-label {
        color: #91a7c2;
        font-size: 10px;
        font-weight: 900;
        letter-spacing: .09em;
        text-transform: uppercase;
        line-height: 1;
    }
    .combined-stat-value {
        color: #f4f7fb;
        font-size: 22px;
        font-weight: 950;
        line-height: 1.1;
        margin-top: 5px;
        white-space: nowrap;
    }
    .combined-stat-value.grade-a,
    .combined-stat-value.change-pos,
    .combined-stat-value.volume-strong { color: #65e98d; }
    .combined-stat-value.grade-b,
    .combined-stat-value.volume-normal { color: #7dd3fc; }
    .combined-stat-value.grade-c { color: #ffd166; }
    .combined-stat-value.grade-reject,
    .combined-stat-value.change-neg { color: #ff8181; }
    .combined-stat-value.volume-slow { color: #9fb0c9; }

    /* Keep every one-click Analyze button readable. */
    div[data-testid="stButton"] button[kind="primary"] {
        font-weight: 900;
        min-height: 58px;
        border-radius: 12px;
    }

    @media (max-width: 1050px) {
        .combined-ticker-row {
            grid-template-columns: minmax(105px, 1.2fr) repeat(4, minmax(76px, 1fr));
            gap: 6px;
        }
        .combined-stat { padding: 7px 8px; }
        .combined-stat-value { font-size: 17px; }
        .combined-ticker-symbol { font-size: 20px; }
        .combined-stat-label { font-size: 9px; }
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
                "volume_pace": row.get("volume_pace"),
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


def _grade_class(grade):
    grade = str(grade or "").upper()
    if grade == "A":
        return "grade-a"
    if grade == "B":
        return "grade-b"
    if grade == "C":
        return "grade-c"
    return "grade-reject"


def _change_class(value):
    try:
        return "change-pos" if float(value) >= 0 else "change-neg"
    except (TypeError, ValueError):
        return ""


def _volume_class(value):
    try:
        pace = float(value)
    except (TypeError, ValueError):
        return "volume-slow"
    if pace >= 1.5:
        return "volume-strong"
    if pace >= 1.0:
        return "volume-normal"
    return "volume-slow"


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

        # Make each row useful at a glance: ticker, grade, score, today's move,
        # and current volume pace fill the width, with Analyze at the end.
        for idx, row in enumerate(candidates):
            symbol = row["symbol"]
            grade = row.get("grade") or "—"
            score_text = _fmt_num(row.get("score"), "{:.0f}")
            day_text = _fmt_num(row.get("day_pct"), "{:+.1f}%")
            volume_text = _fmt_num(row.get("volume_pace"), "{:.2f}x")
            grade_cls = _grade_class(grade)
            change_cls = _change_class(row.get("day_pct"))
            volume_cls = _volume_class(row.get("volume_pace"))

            left, right = st.columns([7.2, 1.55], vertical_alignment="center")
            with left:
                st.markdown(
                    f'<div class="combined-ticker-row">'
                    f'  <div class="combined-ticker-symbol-wrap">'
                    f'    <div class="combined-ticker-symbol">{symbol}</div>'
                    f'    <div class="combined-ticker-caption">MOMENTUM CANDIDATE</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">Grade</div>'
                    f'    <div class="combined-stat-value {grade_cls}">{grade}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">Score</div>'
                    f'    <div class="combined-stat-value">{score_text}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">Today</div>'
                    f'    <div class="combined-stat-value {change_cls}">{day_text}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">Volume Pace</div>'
                    f'    <div class="combined-stat-value {volume_cls}">{volume_text}</div>'
                    f'  </div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with right:
                st.button(
                    f"Analyze {symbol}",
                    key=f"combined_analyze_{idx}_{symbol}",
                    type="primary",
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
