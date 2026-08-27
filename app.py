import html
import json
import os
import runpy
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


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

    /* Hide Streamlit Cloud's built-in white header/toolbar so the dashboard
       begins at the top of the viewport. This removes Share/menu/manage-app
       controls from the normal app view. */
    header[data-testid="stHeader"],
    [data-testid="stHeader"] {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        visibility: hidden !important;
    }
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"] {
        display: none !important;
    }
    [data-testid="stAppViewContainer"] {
        padding-top: 0 !important;
    }
    .block-container {
        padding-top: .35rem !important;
    }

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
        grid-template-columns: minmax(115px, 1.25fr) repeat(5, minmax(82px, 1fr));
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
    .combined-action-value {
        font-size: 16px;
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

    /* Technical-term hover definitions. The labels stay compact until the
       user points at one; the floating explanation does not shift the page. */
    [data-tech-tooltip] {
        text-decoration-line: underline;
        text-decoration-style: dotted;
        text-decoration-thickness: 1px;
        text-underline-offset: 3px;
        cursor: help !important;
    }
    #stock-tech-tooltip {
        position: fixed;
        display: none;
        z-index: 2147483000;
        width: max-content;
        max-width: min(360px, calc(100vw - 28px));
        box-sizing: border-box;
        padding: 10px 12px;
        border-radius: 10px;
        border: 1px solid #355071;
        background: #101b2d;
        color: #eef5ff;
        font-size: 13px;
        line-height: 1.42;
        font-weight: 600;
        letter-spacing: 0;
        text-transform: none;
        box-shadow: 0 10px 30px rgba(0,0,0,.35);
        pointer-events: none;
        white-space: normal;
    }

    /* Keep one-click scanner Analyze buttons readable without forcing every
       primary control (such as Run Fresh Scan) to be oversized. */
    [class*="st-key-combined_analyze_"] button {
        font-weight: 900 !important;
        min-height: 58px !important;
        border-radius: 12px !important;
    }

    @media (max-width: 1050px) {
        .combined-ticker-row {
            grid-template-columns: minmax(98px, 1.1fr) repeat(5, minmax(68px, 1fr));
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
previous_rendered_view = st.session_state.get("_rendered_app_view")
view = st.radio(
    "Stock Workspace",
    VIEWS,
    key="app_view",
    horizontal=True,
    label_visibility="collapsed",
)

# Returning from the Analyzer should be an instant view switch. Do not let an
# overdue automatic scan run inside this same full-app rerun; give Streamlit a
# moment to finish replacing the Analyzer DOM with the cached Scanner view.
if view != previous_rendered_view:
    if view == "Momentum Scanner" and previous_rendered_view == "Stock Analyzer":
        st.session_state["_scanner_return_grace_until"] = time.time() + 2.5
    st.session_state["_rendered_app_view"] = view

# Real render slots for the Momentum Scanner controls. scanner_app.py fills
# these later, but their position is fixed here directly under the workspace
# selector, before the one-click candidate list or any other scanner content.
if view == "Momentum Scanner":
    st.session_state["_scanner_controls_mount"] = st.empty()
    st.session_state["_scanner_status_mount"] = st.empty()


if view == "Momentum Scanner":
    st.markdown(
        """
        <style>
        /* Never leave detached Analyzer widgets visible/clickable while the
           Scanner is active. These can briefly remain in Streamlit's DOM
           during a full view switch, but their widget handlers are already
           gone, which creates "dead" buttons. */
        .st-key-saved_stocks_top,
        .st-key-analyzer_header,
        .st-key-analyzer_controls,
        .st-key-analyzer_live_fragment {
            display: none !important;
            pointer-events: none !important;
        }

        /* Compact the combined one-click scanner section without affecting
           the Stock Analyzer page. */
        .combined-quick {
            padding: 5px 9px 4px !important;
            margin: 0 0 5px !important;
            border-radius: 8px !important;
        }
        .combined-quick-title {
            font-size: 14px !important;
            line-height: 1.1 !important;
            margin-bottom: 1px !important;
        }
        .combined-quick-sub {
            font-size: 11px !important;
            line-height: 1.2 !important;
            margin-bottom: 3px !important;
        }
        .combined-ticker-row {
            min-height: 44px !important;
            gap: 5px !important;
            padding: 3px 0 !important;
        }
        .combined-ticker-symbol {
            font-size: 20px !important;
        }
        .combined-ticker-caption {
            font-size: 9px !important;
            margin-top: 2px !important;
        }
        .combined-stat {
            padding: 4px 7px !important;
            border-radius: 7px !important;
        }
        .combined-stat-label {
            font-size: 8.5px !important;
        }
        .combined-stat-value {
            font-size: 16px !important;
            margin-top: 1px !important;
        }
        .combined-action-value {
            font-size: 13px !important;
        }

        /* Align ticker cells with metric rows */
        .combined-ticker-row {
            align-items: center !important;
        }
        .combined-ticker-symbol-wrap {
            height: 100% !important;
            display: flex !important;
            justify-content: center !important;
        }
        .combined-ticker-symbol {
            display: flex !important;
            align-items: center !important;
            height: 100% !important;
            line-height: 1 !important;
        }
        .combined-ticker-symbol-wrap {
            flex-direction: row !important;
            align-items: center !important;
            gap: 8px !important;
        }
        .combined-rank {
            min-width: 20px !important;
            text-align: right !important;
            color: #91a7c2 !important;
            font-size: 12px !important;
            font-weight: 900 !important;
            line-height: 1 !important;
        }
        .combined-ticker-caption {
            display: none !important;
        }

        [class*="st-key-combined_analyze_"] button {
            min-height: 42px !important;
            height: 42px !important;
            border-radius: 8px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
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
                "ml_probability": row.get("ml_continuation_prob_pct"),
                "ml_validated": bool(row.get("ml_validated")),
                "ml_status": row.get("ml_status"),
                "opportunity_score": row.get("opportunity_score"),
                "scanner_action": row.get("scanner_action"),
                "scanner_action_tier": row.get("scanner_action_tier"),
                "scanner_action_reason": row.get("scanner_action_reason"),
                "day_pct": row.get("day_pct"),
                "volume_pace": row.get("volume_pace"),
                "volume_pace_display": row.get("volume_pace_display"),
            }
        )
    return out[:15]
def _open_analyzer(symbol):
    """Build the selected analysis first, then switch to Analyzer view."""
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return

    st.session_state["ticker"] = symbol
    st.session_state["ticker_search_request"] = symbol
    st.session_state.pop("ticker_picker", None)
    st.session_state.pop("result", None)

    # Streamlit runs button callbacks before the rest of the rerun. Do all of
    # the expensive market/history/ML work here while the existing Scanner is
    # still the page in the browser. Only after the result is ready do we flip
    # app_view, so Analyzer renders already populated instead of progressively.
    try:
        from analyzer_bootstrap import prepare_analyzer_result

        launch_error = prepare_analyzer_result(symbol)
    except Exception as exc:
        launch_error = str(exc)

    if launch_error:
        st.session_state["_analyzer_launch_error"] = launch_error
        return

    st.session_state.pop("_analyzer_launch_error", None)
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


def _action_display(row):
    action = str(row.get("scanner_action") or "WATCH").upper()
    tier = str(row.get("scanner_action_tier") or "watch").lower()
    cls = {
        "ready": "volume-strong",
        "breakout": "grade-b",
        "pullback": "grade-c",
        "avoid": "change-neg",
        "caution": "grade-c",
        "watch": "volume-normal",
    }.get(tier, "volume-normal")

    label = "ACTION"
    if row.get("ml_validated"):
        try:
            probability = float(row.get("ml_probability"))
            label += f" · ML {probability:.0f}%"
        except (TypeError, ValueError):
            pass
    return action, cls, label


def _volume_pace_display(row):
    value = (
        row.get("volume_pace_display")
        if row.get("volume_pace_display") is not None
        else row.get("volume_pace")
    )
    try:
        pace = float(value)
    except (TypeError, ValueError):
        return "—", None
    if pace >= 100:
        return "100x+", pace
    if pace >= 10:
        return f"{pace:.1f}x", pace
    return f"{pace:.2f}x", pace


if view == "Momentum Scanner":
    launch_error = st.session_state.pop("_analyzer_launch_error", None)
    if launch_error:
        st.error(f"Could not analyze the selected ticker: {launch_error}")

    candidates = _latest_scan_candidates()
    if candidates:
        # Make each row useful at a glance: ticker, grade, score, scanner ACTION,
        # today's move and Analyzer-aligned volume pace, with Analyze at the end.
        for idx, row in enumerate(candidates):
            symbol = row["symbol"]
            grade = row.get("grade") or "—"
            score_text = _fmt_num(row.get("score"), "{:.0f}")
            action_text, action_cls, action_label = _action_display(row)
            day_text = _fmt_num(row.get("day_pct"), "{:+.1f}%")
            volume_text, volume_value = _volume_pace_display(row)
            grade_cls = _grade_class(grade)
            change_cls = _change_class(row.get("day_pct"))
            volume_cls = _volume_class(volume_value)

            left, right = st.columns([7.2, 1.55], vertical_alignment="center")
            with left:
                st.markdown(
                    f'<div class="combined-ticker-row">'
                    f'  <div class="combined-ticker-symbol-wrap">'
                    f'    <div class="combined-rank">{idx + 1}.</div>'
                    f'    <div class="combined-ticker-symbol">{symbol}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">Grade</div>'
                    f'    <div class="combined-stat-value {grade_cls}">{grade}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">Score</div>'
                    f'    <div class="combined-stat-value">{score_text}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat" title="{html.escape(str(row.get("scanner_action_reason") or ""))}">'
                    f'    <div class="combined-stat-label">{action_label}</div>'
                    f'    <div class="combined-stat-value combined-action-value {action_cls}">{action_text}</div>'
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


# One shared tooltip layer for both child apps.  The analyzer already contains
# a full glossary; these are short hover versions of its most common terms.
TECHNICAL_TOOLTIPS = {
    "VOLUME PACE": "Current volume compared with what this stock normally trades by this time of day. 1.0x is about normal; 2.0x is about twice normal.",
    "VOL PACE": "Current volume compared with what this stock normally trades by this time of day. 1.0x is about normal; 2.0x is about twice normal.",
    "TOD VOL PACE": "Time-of-day volume pace: current volume versus the stock's normal volume by this exact time of day.",
    "NORMAL VOL BY NOW": "The share of a normal day's volume this ticker historically tends to have completed by the current time.",
    "VWAP": "Volume-Weighted Average Price: the session's average traded price weighted by volume. Holding above it is often constructive for intraday momentum.",
    "VWAP PRICE": "The current session's Volume-Weighted Average Price—the average traded price with heavier-volume prices given more weight.",
    "VWAP EXTENSION": "How far the current price is above or below VWAP. A large positive extension can mean the stock is becoming risky to chase.",
    "LIQUIDITY": "How easily shares can be bought or sold without moving price much. Higher liquidity usually means tighter spreads and less slippage.",
    "LIVE SPREAD": "The percentage gap between the current live bid and ask. With Tradier configured this is consolidated market data; smaller is generally better for entries, exits, and slippage.",
    "BID/ASK SPREAD": "The gap between the best current buying price and selling price. Wider spreads generally increase trading friction and slippage.",
    "DOLLAR VOLUME": "Share volume multiplied by price. It estimates how much money is changing hands and helps compare liquidity between stocks.",
    "FROM HIGH": "How far the current price has fallen from today's session high. A small value means price is still trading close to its high.",
    "5 MIN": "Price momentum over roughly the last five minutes. Positive values show short-term upward movement; negative values show weakening.",
    "15 MIN": "Price momentum over roughly the last fifteen minutes, giving a broader view than the five-minute reading.",
    "MOMENTUM": "The speed and persistence of price movement. Stronger momentum means price is moving more decisively in one direction.",
    "SETUP SCORE": "A combined technical-quality score using factors such as momentum, VWAP, volume, liquidity and price location. It is not a probability of profit.",
    "SCORE": "A combined technical-quality score used to rank the scanner's setups. Higher is stronger, but it is not a guaranteed probability of success.",
    "ACTION": "A scanner-level entry cue. ENTRY READY means momentum, participation, VWAP and execution checks align; still open Analyzer for the exact entry zone, stop and targets.",
    "ENTRY READY": "The scanner's strongest current entry cue. It means the scanner conditions align now, but the Analyzer should still confirm the exact trade plan.",
    "WAIT PULLBACK": "Momentum may remain attractive, but the current price looks too stretched to chase. Wait for a better pullback area and confirm it in Analyzer.",
    "BREAKOUT WATCH": "Price is pressing the session high with constructive momentum. Wait for breakout confirmation and check the exact trigger in Analyzer.",
    "ML 60M": "Validated XGBoost estimate of the chance this scanner setup will be at least 3% higher 60 minutes later. It stays in Learning mode until chronological validation passes.",
    "OPPORTUNITY": "Combined ranking score using 70% of the existing scanner score and 30% of validated ML probability. ML has no ranking weight until validation passes.",
    "GRADE": "A quick quality tier based on the scanner's rules. A is strongest, followed by B and C; the grade is not a guarantee of profit.",
    "DAY RANGE": "The lowest and highest prices traded during the current session.",
    "BASE SETUP": "The analyzer's overall read of the current technical setup before considering a specific entry, stop and targets.",
    "ENTRY ZONE": "A price range where the analyzer sees a more favorable balance of potential upside versus downside rather than one exact entry penny.",
    "STOP / INVALIDATION": "The price area where the trade idea is considered wrong or materially weakened, based on technical structure and volatility.",
    "TARGET 1": "The first, more conservative profit objective, usually based on nearby resistance or another meaningful technical level.",
    "TARGET 2": "A second profit objective beyond Target 1, typically requiring stronger continuation.",
    "STRETCH": "A more aggressive upside target that generally requires unusually strong continuation and should be treated as lower probability.",
    "STRETCH TARGET": "A more aggressive upside target that generally requires unusually strong continuation and should be treated as lower probability.",
    "REWARD / RISK": "Potential reward divided by potential loss from the entry to the stop. For example, 2:1 means two dollars of potential reward for each dollar at risk.",
    "SUPPORT": "A price area where buying has previously been strong enough to slow or reverse a decline. It is a zone, not a guaranteed floor.",
    "RESISTANCE": "A price area where selling has previously been strong enough to slow or reverse a rise.",
    "BREAKOUT": "A move through an important resistance level. Higher-quality breakouts are usually supported by strong volume and price holding above the level.",
    "BREAKOUT CONFIRMATION": "Evidence that a breakout is holding, such as sustained price above the level, strong volume or a successful retest.",
    "ATR": "Average True Range: a measure of how much the stock normally moves. Higher ATR means wider normal price swings.",
    "ATR %": "ATR expressed as a percentage of the stock price, making volatility easier to compare across different-priced stocks.",
    "MFE": "Maximum Favorable Excursion: the largest favorable move seen after a comparable historical setup.",
    "MAE": "Maximum Adverse Excursion: the largest move against the position after a comparable historical setup.",
    "CATALYST": "A news event or company development that can materially change demand for the stock, such as earnings, FDA news, a contract or financing.",
    "FLOAT": "The number of shares readily available for public trading. Lower-float stocks can move more sharply because fewer shares are available.",
    "MARKET CAP": "Share price multiplied by shares outstanding—an estimate of the market value of the company's equity.",
    "SHORT INTEREST": "Shares that have been sold short and remain open. High short interest can add squeeze potential but can also reflect bearish conviction.",
    "SHORT FLOAT": "Short interest expressed as a percentage of the publicly tradable float.",
    "WARRANT OVERHANG": "Potential selling or dilution pressure from outstanding warrants that may be exercised if the stock rises enough.",
    "DILUTION": "An increase in shares outstanding. New shares can reduce existing owners' proportional stake and sometimes pressure price.",
}


def _install_technical_tooltips():
    tooltip_json = json.dumps(TECHNICAL_TOOLTIPS)
    components.html(
        f"""
        <script>
        (() => {{
          const p = window.parent;
          const d = p.document;
          const tips = {tooltip_json};
          const selector = '.combined-stat-label, .mk, .k, .legend-term';

          const normalize = (text) => String(text || '')
            .replace(/\s+/g, ' ')
            .trim()
            .toUpperCase();

          function applyTips() {{
            d.querySelectorAll(selector).forEach((el) => {{
              const key = normalize(el.textContent);
              const text = tips[key];
              if (text) {{
                el.setAttribute('data-tech-tooltip', text);
                el.setAttribute('tabindex', '0');
                el.setAttribute('aria-label', `${{el.textContent.trim()}}. ${{text}}`);
              }}
            }});
          }}

          let box = d.getElementById('stock-tech-tooltip');
          if (!box) {{
            box = d.createElement('div');
            box.id = 'stock-tech-tooltip';
            box.setAttribute('role', 'tooltip');
            d.body.appendChild(box);
          }}

          function show(el) {{
            const text = el && el.getAttribute('data-tech-tooltip');
            if (!text) return;
            box.textContent = text;
            box.style.display = 'block';

            const r = el.getBoundingClientRect();
            const pad = 10;
            const width = box.offsetWidth;
            const height = box.offsetHeight;
            let left = r.left;
            let top = r.bottom + 8;

            if (left + width > p.innerWidth - pad) left = p.innerWidth - width - pad;
            if (left < pad) left = pad;
            if (top + height > p.innerHeight - pad) top = r.top - height - 8;
            if (top < pad) top = pad;

            box.style.left = `${{Math.round(left)}}px`;
            box.style.top = `${{Math.round(top)}}px`;
          }}

          function hide() {{
            box.style.display = 'none';
          }}

          const old = p.__stockTechnicalTooltips;
          if (old) {{
            try {{ old.observer.disconnect(); }} catch (_) {{}}
            try {{ d.removeEventListener('mouseover', old.over); }} catch (_) {{}}
            try {{ d.removeEventListener('mousemove', old.move); }} catch (_) {{}}
            try {{ d.removeEventListener('mouseout', old.out); }} catch (_) {{}}
            try {{ d.removeEventListener('focusin', old.focusin); }} catch (_) {{}}
            try {{ d.removeEventListener('focusout', old.focusout); }} catch (_) {{}}
          }}

          // A number of analyzer labels are flex/grid items whose element box
          // stretches well past the visible word. Hit-test the rendered text
          // itself so hovering blank space to the right does not open a tip.
          function pointerIsOverRenderedText(el, event) {{
            if (!el || event.clientX == null || event.clientY == null) return false;
            const range = d.createRange();
            range.selectNodeContents(el);
            const rects = Array.from(range.getClientRects());
            if (range.detach) range.detach();
            return rects.some((r) =>
              event.clientX >= r.left && event.clientX <= r.right &&
              event.clientY >= r.top && event.clientY <= r.bottom
            );
          }}

          let activeHover = null;
          const over = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el && pointerIsOverRenderedText(el, event)) {{
              activeHover = el;
              show(el);
            }} else if (activeHover) {{
              activeHover = null;
              hide();
            }}
          }};
          const move = over;
          const out = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el && (!event.relatedTarget || !el.contains(event.relatedTarget))) {{
              activeHover = null;
              hide();
            }}
          }};
          const focusin = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el) show(el);
          }};
          const focusout = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el) hide();
          }};

          d.addEventListener('mouseover', over);
          d.addEventListener('mousemove', move);
          d.addEventListener('mouseout', out);
          d.addEventListener('focusin', focusin);
          d.addEventListener('focusout', focusout);

          let queued = false;
          const observer = new MutationObserver(() => {{
            if (queued) return;
            queued = true;
            p.requestAnimationFrame(() => {{
              queued = false;
              applyTips();
            }});
          }});
          if (d.body) observer.observe(d.body, {{childList: true, subtree: true}});

          p.__stockTechnicalTooltips = {{observer, over, move, out, focusin, focusout}};
          applyTips();
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


_install_technical_tooltips()
