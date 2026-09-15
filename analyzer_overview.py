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


def stair_summary(stair):
    count = int(number(stair.get("step_count")) or 0)
    step_label = " step" if count == 1 else " steps"
    if stair.get("breakdown_confirmed"):
        return "Lost plateau · " + str(count) + " historical" + step_label
    if stair.get("breakdown_developing"):
        return "Plateau loss developing"
    if stair.get("developing_step"):
        return fmt(stair.get("step_count") or 0, suffix=" confirmed · expansion pending")
    if stair.get("detected"):
        return str(count) + " confirmed" + step_label
    return "Not detected"


def inject_overview_theme(st):
    st.html("<style>" + Path(__file__).with_suffix(".css").read_text() + "</style>")
    # Load trusted repository scripts in a one-pixel component. The HTML
    # sanitizer in some Streamlit/browser builds strips script-only st.html
    # bodies even with unsafe_allow_javascript. No result data enters this code.
    scripts = "\n".join(Path(__file__).with_name(name).read_text() for name in (
        "analyzer_price_cursor.js", "analyzer_timeline.js", "analyzer_viewport.js", "analyzer_terminal.js"))
    st.iframe("<script>(function(window,document){" + scripts +
                    "})(parent,parent.document);</script>", height=1, tab_index=-1)



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
    if timeframe == "1m" and (result.get("chart_data") or {}).get("intraday_interval") != "1m":
        rows = []  # Older snapshots have no proven minute-resolution identity.
    if timeframe == "D":
        # The analysis already carries a longer daily series. Session keys stay dates.
        rows = list(result.get("daily_context_bars") or []) + rows
    else:
        history = result.get("overview_minute_history" if timeframe == "1m" else "overview_history") or {}
        if history.get("status") == "ok" and history.get("symbol") == result.get("symbol") and (timeframe != "1m" or history.get("timeframe") == "1m"):
            cutoff = str(result.get("reference_price_timestamp") if result.get("research_only") else result.get("as_of") or "")[:10]
            # Never mix coarser historical bars into the active/reference session.
            older = []
            for row in history.get("bars") or []:
                stamp = pd.to_datetime(row.get("t"), utc=True, errors="coerce")
                if pd.notna(stamp) and cutoff and stamp.tz_convert("America/New_York").date().isoformat() < cutoff:
                    older.append(row)
            rows = older + rows
    if not rows:
        return pd.DataFrame()
    if timeframe != "D":
        from datetime import datetime
        def aware(row):
            try:
                return datetime.fromisoformat(str(row.get("t") or "").replace("Z", "+00:00")).tzinfo is not None
            except ValueError:
                return False
        rows = [row for row in rows if aware(row)]
        if not rows:
            return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if timeframe == "D":
        frame["t"] = frame["t"].astype(str).str[:10]
    frame["t"] = pd.to_datetime(frame["t"], utc=True, errors="coerce", format="mixed")
    cutoff = pd.to_datetime(result.get("as_of"), utc=True, errors="coerce")
    cutoff = min(cutoff, pd.Timestamp.now(tz="UTC")) if pd.notna(cutoff) else pd.Timestamp.now(tz="UTC")
    if pd.notna(cutoff):
        if timeframe == "D":
            cutoff = pd.Timestamp(cutoff.tz_convert("America/New_York").date(), tz="UTC")
        frame = frame[frame["t"] <= cutoff]
    for col in ("o", "h", "l", "c", "v"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").replace([float("inf"), -float("inf")], float("nan"))
    frame = frame.dropna(subset=["t", "o", "h", "l", "c"]).sort_values("t").drop_duplicates("t", keep="last").set_index("t")
    frame = frame[(frame["l"] > 0) & (frame["h"] >= frame[["o", "c", "l"]].max(axis=1)) &
                  (frame["l"] <= frame[["o", "c"]].min(axis=1))]
    if timeframe != "D":
        rule = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h"}[timeframe]
        # Do not bridge missing periods or fabricate a price for an empty bucket.
        frame = frame.resample(rule).agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": lambda s: s.sum(min_count=1)})
        frame = frame.dropna(subset=["o", "h", "l", "c"])
    return frame


def price_figure(result, timeframe, window="All"):
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    frame = chart_frame(result, timeframe)
    if frame.empty:
        return None
    # Daily keys are session dates, not instants to be shifted into the prior day.
    x = frame.index.strftime("%Y-%m-%d") if timeframe == "D" else frame.index.tz_convert("America/Los_Angeles").strftime("%Y-%m-%d %H:%M")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.015, row_heights=[.83, .17])
    fig.add_trace(go.Candlestick(x=x, open=frame.o, high=frame.h, low=frame.l, close=frame.c,
                  increasing=dict(line=dict(color="#35dea6", width=1), fillcolor="#35dea6"), decreasing=dict(line=dict(color="#f77474", width=1), fillcolor="#f77474"), whiskerwidth=.25, name="Price", hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Bar(x=x, y=frame.v, marker_color=["#238f7b" if c >= o else "#94565c" for c, o in zip(frame.c, frame.o)],
                         name="Volume", hoverinfo="skip"), row=2, col=1)
    vwap = number(result.get("vwap"))
    session_stamp = pd.to_datetime(result.get("as_of"), utc=True, errors="coerce")
    expected_session = (str(result.get("reference_price_timestamp") or "")[:10] if result.get("research_only") else
                        session_stamp.tz_convert("America/New_York").date().isoformat() if pd.notna(session_stamp) else "")
    if vwap is not None and timeframe != "D" and frame.index[-1].tz_convert("America/New_York").date().isoformat() == expected_session:
        # A current-session VWAP is not a historical VWAP for every loaded day.
        last_session = frame.index.tz_convert("America/New_York").date[-1]
        session_mask = frame.index.tz_convert("America/New_York").date == last_session
        session_x = x[session_mask]
        fig.add_trace(go.Scatter(x=session_x, y=[vwap] * len(session_x), mode="lines",
            line=dict(color="#53d7ee", width=1, dash="dot"), name="Session VWAP", hoverinfo="skip"), row=1, col=1)
    current = price_view(result)
    marker = number(current.get("price")) if current.get("current") and not result.get("research_only") else float(frame.c.iloc[-1])
    marker_label = ("Fallback" if current.get("state") == "FALLBACK" else "Live") if current.get("current") and not result.get("research_only") else "Last bar"
    marker_color = "#35dea6" if marker >= float(frame.o.iloc[-1]) else "#f77474"
    fig.add_hline(y=marker, line_color=marker_color, line_width=.7, line_dash="dot", row=1, col=1)
    fig.add_annotation(x=1, xref="paper", y=marker, yref="y", text=f"{marker_label}<br>${marker:,.4f}" if abs(marker) < 1 else f"{marker_label}<br>${marker:,.2f}",
        showarrow=False, xanchor="left", xshift=3, bgcolor=marker_color, borderpad=4, font=dict(color="#06131c", size=10))
    dates = pd.DatetimeIndex(x)
    sessions = dates.normalize().unique()
    count = {"1D": 1, "5D": 5, "1M": 22, "3M": 66, "1Y": 252}.get(window, len(sessions))
    start = dates[dates.normalize() >= sessions[max(0, len(sessions) - count)]][0]
    start_index = int((dates < start).sum())
    visible = frame[dates >= start]
    low, high = float(visible.l.min()), float(visible.h.max())
    padding = max((high - low) * .08, high * .005)
    fig.update_layout(height=410, margin=dict(l=8, r=82, t=50, b=48, autoexpand=False), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#9bb1c5", size=11, family="Inter, -apple-system, sans-serif"),
                      showlegend=False, dragmode="pan", hovermode=False,
                      uirevision=f"overview:{result.get('symbol')}:{timeframe}:{window}:{frame.index[0].isoformat()}:{frame.index[-1].date()}:America/Los_Angeles")
    from analyzer_visuals import viewport_controls
    fig.update_layout(meta=dict(analyzerViewport=True, analyzerTerminal=True, defaultX=[start_index-.5, len(frame)-.5]), updatemenus=viewport_controls())
    fig.update_layout(updatemenus=[dict(fig.layout.updatemenus[0].to_plotly_json(), xanchor="left", y=1.03, yanchor="bottom", font=dict(size=10, color="#b8cddd"))])
    # Keep one slot per real candle. The local timeline formatter groups the
    # visible native tick labels by date after every Plotly pan/zoom/render.
    fig.update_xaxes(type="category", rangeslider_visible=False, gridcolor="rgba(133,169,197,.10)", nticks=7,
                     tickangle=0, ticklabeloverflow="allow", minallowed=-.5,
                     maxallowed=len(frame)-.5, range=[start_index-.5, len(frame)-.5])
    fig.update_yaxes(side="right", gridcolor="rgba(133,169,197,.10)", zeroline=False, fixedrange=False)
    fig.update_yaxes(range=[low - padding, high + padding], tickprefix="$", tickformat=".4f" if high < 1 else ".2f", nticks=6, row=1, col=1)
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
    guidance_available = not research and view["current"]
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
            _tile("Entry readiness", fmt(v2.get("entry_readiness"), suffix=" / 100") if guidance_available else "Unavailable", str(v2.get("entry_label") or "Decision support") if guidance_available else "Fresh quote required", "◷", "" if guidance_available else "ao-amber"),
            _tile("Session volume", fmt(result.get("session_volume") if result.get("session_volume") is not None else result.get("volume")), "Completed session" if research else str(result.get("volume_source") or "Reported volume"), "▤")]
        _html(st, '<div class="ao-metrics">' + ''.join(tiles) + '</div>')
        with st.container(key="ao_primary"):
            chart_col, ml_col = st.columns([1.6, 1], gap="small")
            with chart_col, st.container(key="ao_chart", border=True):
                title, timeframe_col = st.columns([1.5, 1], vertical_alignment="center")
                with title:
                    _html(st, '<h3 class="ao-heading">▥ &nbsp;Price &amp; key levels</h3>')
                with timeframe_col:
                    timeframe = st.segmented_control("Chart timeframe", ["1m", "5m", "15m", "1h", "D"], default="D" if research else "5m", key="overview_chart_timeframe", selection_mode="single", required=True, label_visibility="collapsed")
                caption_col, range_col = st.columns([1.4, 1], vertical_alignment="center")
                with range_col:
                    window = st.segmented_control("History range", ["1M", "3M", "1Y", "All"] if timeframe == "D" else (["1D", "5D", "All"] if timeframe == "1m" else ["1D", "5D", "1M", "All"]),
                        default="3M" if timeframe == "D" else "5D" if best_key in ("swing", "long_term") else "1D",
                        key="overview_history_daily" if timeframe == "D" else "overview_history_minute" if timeframe == "1m" else "overview_history_intraday",
                        required=True, selection_mode="single", label_visibility="collapsed")
                frame = chart_frame(result, timeframe)
                with caption_col:
                    coverage = ""
                    if not frame.empty:
                        coverage = " · " + frame.index[0].strftime("%b %d") + "–" + frame.index[-1].strftime("%b %d") + " loaded"
                    st.caption(("Daily sessions" if timeframe == "D" else "Pacific time") + coverage)
                fig = price_figure(result, timeframe, window)
                if fig is None:
                    st.info("No verified one-minute bars in this snapshot. Analyze again to load 1m history." if timeframe == "1m" else
                            "No " + ("daily" if timeframe == "D" else "intraday") + " bars returned for this analysis.")
                else:
                    st.plotly_chart(fig, key="analyzer_overview_chart", width="stretch", config={"displaylogo": False, "scrollZoom": True, "displayModeBar": False, "doubleClick": "reset", "responsive": True})
                st.caption("Pinch / wheel to zoom · drag chart to pan · drag right price axis or bottom time axis to scale · Reset view to restore")
                if timeframe == "1m":
                    st.caption("True 1-minute candles · up to 14 calendar days of prior regular sessions. All means loaded history; 1M is unavailable at this resolution.")
                if timeframe != "D":
                    history = result.get("overview_minute_history" if timeframe == "1m" else "overview_history") or {}
                    if history.get("status") == "ok":
                        note = "Earlier sessions: " + str(history.get("source")) + " · Chart context only"
                        if not (result.get("chart_data") or {}).get("intraday") or (timeframe == "1m" and (result.get("chart_data") or {}).get("intraday_interval") != "1m"):
                            note += " · Active/reference session intraday bars unavailable"
                    elif history.get("status") == "unavailable":
                        note = "Older intraday history unavailable (" + str(history.get("error") or "provider error") + "). Daily history is available under D."
                    elif history.get("status") == "empty":
                        note = "No older intraday bars returned. Daily history is available under D."
                    else:
                        note = "Analyze again to load older intraday history."
                    _html(st, '<div class="ao-chart-note">' + esc(note) + '</div>')
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
                        ("Stair-step", stair_summary(stair)))) + '</div>')
                st.caption("Completed-session readings" if research else "Structure at analysis time")
                detail_link(st, "Pattern evidence", "Patterns", "overview_pattern_details")
            with caution_col, st.container(key="ao_cautions", border=True):
                cautions = []
                if evidence is None or evidence < 40:
                    cautions.append("Weak supporting evidence" if evidence is not None else "Evidence strength unavailable")
                if not result.get("news"):
                    cautions.append("No recent news returned")
                if not guidance_available:
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
