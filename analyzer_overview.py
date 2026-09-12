"""Compact Analyzer presentation. Reads the existing result; never fetches or trains."""
from __future__ import annotations

import html
import math
from pathlib import Path

from live_price_quality import price_note, price_view


DETAILS = ("Overview", "Patterns", "Scenarios", "Historical matches", "ML diagnostics",
           "News", "Setup & timeframe", "Levels & liquidity", "Execution plan", "Sources")


def esc(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def fmt(value, digits=0, suffix=""):
    value = number(value)
    return "—" if value is None else f"{value:,.{digits}f}{suffix}"


def money(value):
    value = number(value)
    return "—" if value is None else f"${value:,.2f}"


def inject_overview_theme(st):
    st.html("<style>" + Path(__file__).with_suffix(".css").read_text() + "</style>")


def _html(st, body):
    st.html(body)


def render_quote(st, result, overlay=None):
    """Called by the existing fast fragment; re-age live observations every time."""
    research = bool(result.get("research_only"))
    record = overlay if overlay is not None else result
    if str(record.get("symbol") or "").upper() != str(result.get("symbol") or "").upper():
        record = {"symbol": result.get("symbol"), "live_price_available": False}
    view = price_view(record)
    value = money(result.get("price")) if research else money(view["price"])
    day = number(result.get("day_pct"))
    change = "—" if day is None else f"{day:+.2f}%"
    note = ("Reference close · " + str(result.get("reference_price_timestamp") or "date unavailable")[:10]
            if research else price_note(view))
    if not research and not view["current"]:
        value = view["state"]
        change = ""
        if view["last_known_price"] is not None:
            note += " · Last known " + money(view["last_known_price"])
    _html(st, '<div class="ao-quote"><strong>' + esc(result.get("symbol")) + '</strong>'
          '<b>' + esc(value) + '</b><span class="' + ("ao-green" if (day or 0) >= 0 else "ao-red")
          + '">' + esc(change) + '</span><small>' + esc(note) + '</small></div>')


def ml_summary(result):
    """Keep availability, validation and research mode distinct, including empty results."""
    from ml_ui import _pct_value
    ml = result.get("ml_prediction") or {}
    status = ml.get("status") or "unavailable"
    available = status == "ok"
    research = bool(result.get("research_only"))
    if status == "insufficient_history":
        title, reason = "Predictions unavailable", "Not enough 5-minute bars to train."
    elif not available:
        title, reason = "Predictions unavailable", str(ml.get("error") or "No model result was returned for this analysis.")
    else:
        title = "Completed-session estimates" if research else "Model estimates"
        reason = "Research context only · no live prediction." if research else "Advisory estimates unless individually validated."
    models = ml.get("models") or {}
    edge = ml.get("ml_edge_score")
    edge_ok = available and int(ml.get("validated_edge_model_count") or 0) > 0
    cells = [("Validated edge", fmt(edge, suffix=" / 100") if edge_ok else "—")]
    for label, key in (("Target before stop", "target_before_stop"), ("30m higher", "higher_30"),
                       ("60m higher", "higher_60"), ("Breakout hold", "breakout_hold"), ("Reversal risk", "reversal_30")):
        value = _pct_value(models.get(key)) if available else "—"
        if available and key == "breakout_hold" and not ml.get("breakout_relevant"):
            value = "N/A"
        cells.append((label, value))
    count = ml.get("bar_count")
    history = (f"{fmt(count)} five-minute bars returned" if count is not None else "History count not reported")
    if status == "insufficient_history":
        # This is the existing predictor's minimum, not a progress/training estimate.
        history += " · 700 required"
    elif status == "history_unavailable":
        history += " · incomplete fetch; training not started"
    return {"title": title, "reason": reason, "badge": status.replace("_", " ").upper() if not available else
            ("RESEARCH ONLY" if research else str(ml.get("validation_gate") or "ADVISORY ONLY")),
            "cells": cells, "history": history,
            "source": str(ml.get("source") or "Source not reported"), "status": status}


def chart_frame(result, timeframe):
    """Aggregate only stored OHLCV observations; never substitute daily for intraday."""
    import pandas as pd
    from analyzer_visuals import _bars
    kind = "daily" if timeframe == "D" else "intraday"
    rows = _bars(result, kind)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["t"] = pd.to_datetime(frame["t"], utc=True, errors="coerce")
    for col in ("o", "h", "l", "c", "v"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").replace([float("inf"), -float("inf")], float("nan"))
    frame = frame.dropna(subset=["t", "o", "h", "l", "c"]).sort_values("t").drop_duplicates("t", keep="last").set_index("t")
    frame = frame[(frame["l"] > 0) & (frame["h"] >= frame[["o", "c", "l"]].max(axis=1)) &
                  (frame["l"] <= frame[["o", "c"]].min(axis=1))]
    if timeframe != "D":
        rule = {"5m": "5min", "15m": "15min", "1h": "1h"}[timeframe]
        # Do not bridge missing periods or fabricate a price for an empty bucket.
        frame = frame.resample(rule).agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": lambda s: s.sum(min_count=1)})
        frame = frame.dropna(subset=["o", "h", "l", "c"])
    return frame


def price_figure(result, timeframe):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    frame = chart_frame(result, timeframe)
    if frame.empty:
        return None
    # Daily keys are session dates, not instants to be shifted into the prior day.
    x = frame.index.strftime("%Y-%m-%d") if timeframe == "D" else frame.index.tz_convert("America/Los_Angeles").strftime("%Y-%m-%d %H:%M")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.015, row_heights=[.83, .17])
    fig.add_trace(go.Candlestick(x=x, open=frame.o, high=frame.h, low=frame.l, close=frame.c,
                  increasing_line_color="#35dea6", decreasing_line_color="#f77474", name="Price"), row=1, col=1)
    fig.add_trace(go.Bar(x=x, y=frame.v, marker_color=["#238f7b" if c >= o else "#94565c" for c, o in zip(frame.c, frame.o)],
                         name="Volume", hovertemplate="%{x}<br>Volume %{y:,.0f}<extra></extra>"), row=2, col=1)
    vwap = number(result.get("vwap"))
    if vwap is not None:
        fig.add_hline(y=vwap, line_color="#53d7ee", line_dash="dash", line_width=1,
                      annotation_text="Session VWAP", annotation_font_color="#53d7ee", row=1, col=1)
    fig.update_layout(height=277, margin=dict(l=0, r=4, t=8, b=0), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#abc4da", size=11),
                      showlegend=False, dragmode="pan", hovermode="x unified",
                      uirevision=f"overview:{result.get('symbol')}:{timeframe}")
    fig.update_xaxes(type="category", rangeslider_visible=False, gridcolor="#193343", nticks=7)
    fig.update_yaxes(side="right", gridcolor="#193343", zeroline=False, fixedrange=False)
    fig.update_yaxes(showticklabels=False, row=2, col=1)
    return fig


def _open_detail(st, name):
    st.session_state["analyzer_detail"] = name
    st.session_state["_analyzer_detail_selection"] = name


def _remember_detail(st):
    st.session_state["_analyzer_detail_selection"] = st.session_state.get("analyzer_detail") or "Overview"


def detail_link(st, label, target, key):
    st.button(label + "  →", key=key, type="tertiary", on_click=_open_detail, args=(st, target))


def _tile(label, value, note, icon="▥", tone=""):
    return ('<article class="ao-tile"><span class="ao-icon">' + esc(icon) + '</span><div><label>' + esc(label)
            + '</label><strong class="' + esc(tone) + '">' + esc(value) + '</strong><small>' + esc(note) + '</small></div></article>')


def render_overview(st, pd, result, card, pp, render_details):
    inject_overview_theme(st)
    research = bool(result.get("research_only"))
    v2 = result.get("decision_v2") or {}
    tf = v2.get("timeframe_analysis") or {}
    best = str(tf.get("stable_best_fit") or tf.get("best_fit") or "MIXED")
    best_key = {"INTRADAY": "intraday", "SWING": "swing", "LONGER-TERM": "long_term", "LONG TERM": "long_term", "LONG_TERM": "long_term"}.get(best.upper())
    evidence = number(v2.get("evidence_strength"))
    view = price_view(result)
    if research:
        notice = "RESEARCH ONLY · Market closed. Live entry, stop and target guidance disabled."
    elif not view["current"]:
        notice = price_note(view) + " · Current entry/exit guidance unavailable."
    elif view["state"] == "FALLBACK":
        notice = "FALLBACK PRICE · " + price_note(view) + " · " + str(result.get("live_price_fallback_reason") or "Fresh alternate observation in use.")
    else:
        notice = "LIVE ANALYSIS · " + price_note(view)
    with st.container(key="analyzer_overview"):
        _html(st, '<div class="ao-notice' + (' ao-live' if not research and view["state"] == "LIVE" else '') + '">◈ &nbsp; ' + esc(notice) + '</div>')
        refresh_error = st.session_state.get("_analyzer_refresh_error")
        if refresh_error:
            st.warning("Refresh failed; previous analysis retained. " + str(refresh_error))
        tiles = [
            _tile("Research score" if research else "Setup score", fmt(result.get("score"), 1, " / 100"), "Grade " + str(result.get("grade") or "—"), "▥"),
            _tile("Upside research" if research else "Upside potential", fmt(v2.get("potential_score"), suffix=" / 100"), "Setup score", "◎"),
            _tile("Evidence strength", fmt(evidence, suffix=" / 100"), "Unavailable" if evidence is None else "Low" if evidence < 40 else "Supporting evidence", "△"),
            _tile("Best timeframe", best, "Stable fit" if tf.get("stable_best_fit") else "Strongest fit", "▥"),
            _tile("Entry readiness", "Unavailable" if research else fmt(v2.get("entry_readiness"), suffix=" / 100"), "Fresh quote required" if research else str(v2.get("entry_label") or "Decision support"), "◷", "ao-amber" if research else ""),
            _tile("Session volume", fmt(result.get("session_volume") if result.get("session_volume") is not None else result.get("volume")), "Completed session" if research else str(result.get("volume_source") or "Reported volume"), "▤")]
        _html(st, '<div class="ao-metrics">' + ''.join(tiles) + '</div>')
        with st.container(key="ao_primary"):
            chart_col, ml_col = st.columns([1.6, 1], gap="small")
            with chart_col, st.container(key="ao_chart", border=True):
                title, timeframe_col = st.columns([1.5, 1], vertical_alignment="center")
                with title:
                    _html(st, '<h3 class="ao-heading">▥ &nbsp;Price &amp; key levels</h3>')
                with timeframe_col:
                    timeframe = st.segmented_control("Chart timeframe", ["5m", "15m", "1h", "D"], default="D" if research else "5m", key="overview_chart_timeframe", selection_mode="single", required=True, label_visibility="collapsed")
                st.caption(("Completed-session context" if research else "Analysis snapshot") + " · " + ("Daily sessions" if timeframe == "D" else "Pacific time"))
                fig = price_figure(result, timeframe)
                if fig is None:
                    st.info("No " + ("daily" if timeframe == "D" else "intraday") + " bars returned for this analysis.")
                else:
                    st.plotly_chart(fig, key="analyzer_overview_chart", width="stretch", config={"displaylogo": False, "scrollZoom": False, "displayModeBar": "hover", "doubleClick": "reset", "responsive": True})
                _html(st, '<div class="ao-levels"><span>VWAP <b>' + esc(money(result.get("vwap"))) + '</b></span><span>Low <b class="ao-red">' + esc(money(result.get("day_low"))) + '</b></span><span>High <b class="ao-green">' + esc(money(result.get("day_high"))) + '</b></span></div>')
            with ml_col, st.container(key="ao_ml", border=True):
                ml = ml_summary(result)
                _html(st, '<div class="ao-panel-head"><h3 class="ao-heading">♧ &nbsp;ML outlook</h3><span class="ao-badge">' + esc(ml["badge"]) + '</span></div>'
                      '<div class="ao-ml-message"><strong>' + esc(ml["title"]) + '</strong><p>' + esc(ml["reason"]) + '</p></div>'
                      '<div class="ao-ml-grid">' + ''.join('<div><label>' + esc(k) + '</label><b>' + esc(v) + '</b></div>' for k, v in ml["cells"]) + '</div>')
                st.caption(ml["history"] + " · " + ml["source"])
                detail_link(st, "Model diagnostics", "ML diagnostics", "overview_ml_details")
        with st.container(key="ao_secondary"):
            fit_col, structure_col, caution_col = st.columns(3, gap="small")
            with fit_col, st.container(key="ao_fit", border=True):
                _html(st, '<h3 class="ao-heading">▥ &nbsp;Timeframe fit</h3>')
                rows = []
                for label, key in (("Intraday", "intraday"), ("Swing", "swing"), ("Longer-term", "long_term")):
                    value = number((tf.get("scores") or {}).get(key))
                    width = max(0, min(100, value or 0))
                    rows.append('<div class="ao-fit-row"><span>' + label + '</span><div class="ao-track"><i style="width:' + str(width) + '%;background:' + ('#3bdfa1' if key == best_key else '#60afe5') + '"></i></div><b>' + fmt(value, suffix=" / 100") + '</b></div>')
                _html(st, '<div class="ao-fit-rows">' + ''.join(rows) + '</div>')
                detail_link(st, "Why this fit", "Setup & timeframe", "overview_fit_details")
            with structure_col, st.container(key="ao_structure", border=True):
                impulse, bounce, stair = (result.get(k) or {} for k in ("impulse_pullback", "bounce_sequence", "stair_step"))
                _html(st, '<h3 class="ao-heading">♧ &nbsp;Structure snapshot</h3><div class="ao-structure-rows">' + ''.join(
                    '<div><span>' + esc(k) + '</span><b>' + esc(v) + '</b></div>' for k, v in (
                        ("Impulse / pullback", str(impulse.get("phase") or "Not detected").replace("_", " ").capitalize()),
                        ("Bounce sequence", fmt(bounce.get("completed_bounces"), suffix=" completed") if bounce.get("detected") else "Not detected"),
                        ("Stair-step", fmt(stair.get("step_count"), suffix=" reported steps") if stair.get("detected") else "Not detected"))) + '</div>')
                st.caption("Completed-session readings" if research else "Structure at analysis time")
                detail_link(st, "Pattern evidence", "Patterns", "overview_pattern_details")
            with caution_col, st.container(key="ao_cautions", border=True):
                cautions = []
                if evidence is None or evidence < 40:
                    cautions.append("Weak supporting evidence" if evidence is not None else "Evidence strength unavailable")
                if not result.get("news"):
                    cautions.append("No recent news returned")
                if research:
                    cautions.append("Live guidance unavailable")
                else:
                    cautions.append(str((result.get("trade_plan") or {}).get("action") or "Review the execution plan before acting"))
                _html(st, '<h3 class="ao-heading">△ &nbsp;Context &amp; cautions</h3><ul class="ao-cautions">' + ''.join('<li>' + esc(x) + '</li>' for x in cautions) + '</ul><div class="ao-score-note">Scores are not probabilities.</div>')
                detail_link(st, "Review sources", "Sources", "overview_sources_details")
        with st.container(key="ao_explore", border=True):
            st.caption("EXPLORE DETAILS")
            st.session_state.setdefault("analyzer_detail", st.session_state.get("_analyzer_detail_selection", "Overview"))
            selected = st.pills("Explore details", DETAILS, key="analyzer_detail", label_visibility="collapsed", selection_mode="single", required=True, on_change=_remember_detail, args=(st,))
        if selected != "Overview":
            with st.container(key="ao_detail_content", border=True):
                st.subheader(selected)
                if selected == "ML diagnostics":
                    from ml_ui import render_ml_prediction
                    ml = result.get("ml_prediction") or {}
                    st.caption(ml_summary(result)["history"] + " · " + ml_summary(result)["source"])
                    render_ml_prediction(st, pd, result, card)
                elif selected == "Historical matches":
                    from historical_ui import render_historical_setup
                    render_details(selected)
                    render_historical_setup(st, pd, result, card, pp)
                elif selected == "Sources":
                    st.caption("Source observations at analysis time · " + str(result.get("as_of") or "unknown"))
                    fields = {k: result.get(k) for k in ("symbol", "as_of", "analysis_mode", "reference_price_source", "reference_price_timestamp", "live_price_source", "live_price_timestamp", "live_price_fallback_reason", "live_provider_error", "alpaca_fallback_error", "bid", "ask", "spread_pct", "volume_source", "historical_feed", "engine_version")}
                    st.dataframe(pd.DataFrame([{"Field": k.replace("_", " ").title(), "Value": str(v) if v is not None else "—"} for k, v in fields.items()]), hide_index=True, width="stretch")
                    st.caption("Scores describe setup strength, not probabilities. Historical matches are research context; they do not override live freshness or validation gates.")
                else:
                    render_details(selected)
        source = (result.get("reference_price_source") if research else result.get("live_price_source")) or "Source unavailable"
        st.caption(("Research generated " if research else "Analysis as of ") + str(result.get("as_of") or "unknown") + " · " + str(source))
