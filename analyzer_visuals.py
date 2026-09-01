"""Vega-Lite visual snapshots for the Single Stock Analyzer.

These are explanatory views only. They use the exact compact OHLCV bars
returned by stock_analyzer.py so visual markers stay tied to the same evidence
that produced the Analyzer cards.
"""

from __future__ import annotations

import plotly.graph_objects as go


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _bars(result, kind):
    raw = ((result or {}).get("chart_data") or {}).get(kind) or []
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        t = row.get("t")
        c = _num(row.get("c"))
        if not t or c is None:
            continue
        rec = {"t": str(t), "c": c}
        for key in ("o", "h", "l", "v"):
            value = _num(row.get(key))
            rec[key] = value
        out.append(rec)
    return out


def _config():
    return {
        "background": "#08111f",
        "view": {
            "fill": "#08111f",
            "stroke": "#18314a",
        },
        "axis": {
            "labelColor": "#b8c9dc",
            "titleColor": "#dcecff",
            "gridColor": "#28435d",
            "domainColor": "#31516f",
            "tickColor": "#31516f",
        },
        "legend": {
            "labelColor": "#dcecff",
            "titleColor": "#dcecff",
        },
    }


def _interactive_params():
    """Use one Vega-Lite scale-bound interval for reliable chart rendering.

    The previous two-selection implementation bound separate x/y intervals to
    the same layered chart and used custom event-stream expressions. That can
    fail validation/rendering in the Vega-Lite runtime used by Streamlit,
    leaving every Analyzer chart blank. One standard scale-bound interval keeps
    wheel/drag interaction while remaining portable across all layered specs.
    """
    return [
        {
            "name": "chart_zoom",
            "select": {
                "type": "interval",
                "encodings": ["x", "y"],
                "clear": "dblclick",
            },
            "bind": "scales",
        }
    ]


def _interactive_chart(layers, *, height, resolve=None):
    """Return a conservative layered Vega-Lite spec that reliably renders in Streamlit.

    Scale-bound interval params have repeatedly produced blank chart bodies in the
    Streamlit Cloud Vega runtime. Rendering the chart is more important than
    fragile in-chart zoom bindings, so keep the base spec interaction-free here.
    """
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "height": height,
        "layer": layers,
        "config": _config(),
    }
    if resolve:
        spec["resolve"] = resolve
    return spec


def _price_line_layer(bars, *, x_type="temporal", opacity=0.72):
    return {
        "data": {"values": bars},
        "mark": {
            "type": "line",
            "strokeWidth": 2,
            "color": "#dbeafe",
            "opacity": opacity,
            "tooltip": True,
        },
        "encoding": {
            "x": {
                "field": "t",
                "type": x_type,
                "title": None,
                "axis": {"labelOverlap": True},
            },
            "y": {
                "field": "c",
                "type": "quantitative",
                "title": "Price",
                "scale": {"zero": False},
            },
            "tooltip": [
                {"field": "t", "type": x_type, "title": "Time"},
                {"field": "o", "type": "quantitative", "title": "Open", "format": "$.2f"},
                {"field": "h", "type": "quantitative", "title": "High", "format": "$.2f"},
                {"field": "l", "type": "quantitative", "title": "Low", "format": "$.2f"},
                {"field": "c", "type": "quantitative", "title": "Close", "format": "$.2f"},
                {"field": "v", "type": "quantitative", "title": "Volume", "format": ",.0f"},
            ],
        },
    }


def _candlestick_layers(bars, *, x_type="temporal", line_overlay=False):
    candles=[
        row for row in bars
        if all(_num(row.get(key)) is not None for key in ("o","h","l","c"))
    ]
    if not candles:
        return [_price_line_layer(bars, x_type=x_type)]

    color_encoding={
        "condition":{"test":"datum.c >= datum.o","value":"#00d26a"},
        "value":"#ff5a1f",
    }
    tooltip=[
        {"field":"t","type":x_type,"title":"Time"},
        {"field":"o","type":"quantitative","title":"Open","format":"$.2f"},
        {"field":"h","type":"quantitative","title":"High","format":"$.2f"},
        {"field":"l","type":"quantitative","title":"Low","format":"$.2f"},
        {"field":"c","type":"quantitative","title":"Close","format":"$.2f"},
        {"field":"v","type":"quantitative","title":"Volume","format":",.0f"},
    ]
    layers=[
        {
            "data":{"values":candles},
            "mark":{"type":"rule","strokeWidth":1.25},
            "encoding":{
                "x":{
                    "field":"t","type":x_type,"title":None,
                    "axis":{"labelOverlap":True},
                },
                "y":{
                    "field":"l","type":"quantitative","title":"Price",
                    "scale":{"zero":False},
                },
                "y2":{"field":"h"},
                "color":color_encoding,
                "tooltip":tooltip,
            },
        },
        {
            "data":{"values":candles},
            "mark":{"type":"bar","size":7},
            "encoding":{
                "x":{"field":"t","type":x_type},
                "y":{"field":"o","type":"quantitative","scale":{"zero":False}},
                "y2":{"field":"c"},
                "color":color_encoding,
                "tooltip":tooltip,
            },
        },
    ]
    if line_overlay:
        layers.append(_price_line_layer(candles,x_type=x_type,opacity=0.55))
    return layers


def _horizontal_level_layers(levels):
    levels = [
        row for row in levels
        if _num(row.get("price")) is not None and row.get("label")
    ]
    if not levels:
        return []

    domain = [row["kind"] for row in levels]
    palette = {
        "stop": "#ff6978",
        "target": "#50fa9b",
        "stretch": "#8be9fd",
        "vwap": "#bd93f9",
        "support": "#50fa9b",
        "resistance": "#ffb86c",
        "current": "#f8f8f2",
    }
    colors = [palette.get(kind, "#ffd166") for kind in domain]
    scale = {"domain": domain, "range": colors}

    return [
        {
            "data": {"values": levels},
            "mark": {"type": "rule", "strokeDash": [5, 4], "strokeWidth": 1.8},
            "encoding": {
                "y": {"field": "price", "type": "quantitative"},
                "color": {
                    "field": "kind",
                    "type": "nominal",
                    "scale": scale,
                    "legend": None,
                },
            },
        },
        {
            "data": {"values": levels},
            "mark": {
                "type": "text",
                "align": "right",
                "baseline": "bottom",
                "dx": -4,
                "dy": -2,
                "fontSize": 11,
                "fontWeight": "bold",
            },
            "encoding": {
                "x": {"aggregate": "max", "field": "t", "type": "temporal"},
                "y": {"field": "price", "type": "quantitative"},
                "text": {"field": "label"},
                "color": {
                    "field": "kind",
                    "type": "nominal",
                    "scale": scale,
                    "legend": None,
                },
            },
        },
    ]


def trade_plan_chart_spec(result, line_overlay=False):
    result = result or {}
    bars = _bars(result, "intraday")
    plan = result.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    if len(bars) < 2 or not selected:
        return None

    first_t, last_t = bars[0]["t"], bars[-1]["t"]
    entry_low = _num(selected.get("entry_low"))
    entry_high = _num(selected.get("entry_high"))
    layers = _candlestick_layers(bars, line_overlay=line_overlay)

    if entry_low is not None and entry_high is not None:
        layers.append(
            {
                "data": {
                    "values": [
                        {"t": first_t, "low": entry_low, "high": entry_high},
                        {"t": last_t, "low": entry_low, "high": entry_high},
                    ]
                },
                "mark": {"type": "area", "opacity": 0.22, "color": "#b8872f"},
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {
                        "field": "low",
                        "type": "quantitative",
                        "scale": {"zero": False},
                    },
                    "y2": {"field": "high"},
                },
            }
        )

    levels = []
    for key, label, kind in (
        ("stop", "Stop", "stop"),
        ("target1", "Target 1", "target"),
        ("target2", "Target 2", "target"),
        ("stretch_target", "Stretch", "stretch"),
    ):
        value = _num(selected.get(key))
        if value is not None:
            levels.append({"t": last_t, "price": value, "label": label, "kind": kind})

    vwap = _num(result.get("vwap"))
    if vwap is not None:
        levels.append({"t": last_t, "price": vwap, "label": "VWAP", "kind": "vwap"})

    layers.extend(_horizontal_level_layers(levels))
    return _interactive_chart(
        layers,
        height=280,
        resolve={"scale": {"color": "independent"}},
    )


def multi_bounce_chart_spec(result, line_overlay=False):
    result = result or {}
    bars = _bars(result, "intraday")
    sequence = result.get("bounce_sequence") or {}
    if len(bars) < 2 or not sequence.get("detected"):
        return None

    markers = []
    for bounce in sequence.get("bounces") or []:
        number = int(bounce.get("number") or 0)
        low_idx = bounce.get("pullback_low_index")
        peak_idx = bounce.get("bounce_peak_index")
        low_time = bounce.get("pullback_low_time")
        peak_time = bounce.get("bounce_peak_time")
        if low_time or (isinstance(low_idx, int) and 0 <= low_idx < len(bars)):
            markers.append({
                "t": str(low_time or bars[low_idx]["t"]),
                "price": _num(bounce.get("pullback_low")) or (
                    bars[low_idx]["c"]
                    if isinstance(low_idx,int) and 0 <= low_idx < len(bars)
                    else None
                ),
                "label": f"B{number} low",
                "kind": "dip",
            })
        if peak_time or (isinstance(peak_idx, int) and 0 <= peak_idx < len(bars)):
            markers.append({
                "t": str(peak_time or bars[peak_idx]["t"]),
                "price": _num(bounce.get("bounce_peak")) or (
                    bars[peak_idx]["c"]
                    if isinstance(peak_idx,int) and 0 <= peak_idx < len(bars)
                    else None
                ),
                "label": f"Bounce #{number} ✓",
                "kind": "confirmed",
            })

    next_num = int(sequence.get("next_bounce_number") or 1)
    current_leg = str(sequence.get("current_leg") or "").upper()
    reference_idx = sequence.get("reference_peak_index")
    if (
        isinstance(reference_idx, int)
        and 0 <= reference_idx < len(bars) - 1
        and sequence.get("current_dip_low") is not None
    ):
        tail = bars[reference_idx + 1:]
        if tail:
            dip_offset = min(
                range(len(tail)),
                key=lambda i: abs(
                    (_num(tail[i].get("l")) or tail[i]["c"])
                    - (_num(sequence.get("current_dip_low")) or tail[i]["c"])
                ),
            )
            dip_idx = reference_idx + 1 + dip_offset
            markers.append({
                "t": bars[dip_idx]["t"],
                "price": _num(sequence.get("current_dip_low")) or bars[dip_idx]["c"],
                "label": f"B{next_num} forming dip",
                "kind": "developing",
            })

    if current_leg == "BOUNCING":
        markers.append({
            "t": bars[-1]["t"],
            "price": bars[-1]["c"],
            "label": f"Bounce #{next_num} developing",
            "kind": "developing",
        })

    layers = _candlestick_layers(bars, line_overlay=line_overlay)
    if markers:
        valid_markers=[m for m in markers if _num(m.get("price")) is not None]
        top_markers=[m for m in valid_markers if m.get("kind") != "dip"]
        dip_markers=[m for m in valid_markers if m.get("kind") == "dip"]
        layers.append({
            "data": {"values": valid_markers},
            "mark": {"type": "point", "filled": True, "size": 120},
            "encoding": {
                "x": {"field": "t", "type": "temporal"},
                "y": {"field": "price", "type": "quantitative"},
                "color": {
                    "field": "kind",
                    "type": "nominal",
                    "scale": {
                        "domain": ["confirmed", "dip", "developing"],
                        "range": ["#57f287", "#8be9fd", "#ffd166"],
                    },
                    "legend": None,
                },
                "shape": {
                    "field": "kind",
                    "type": "nominal",
                    "scale": {
                        "domain": ["confirmed", "dip", "developing"],
                        "range": ["circle", "triangle-up", "diamond"],
                    },
                    "legend": None,
                },
                "tooltip": [
                    {"field": "label", "title": "Pattern"},
                    {"field": "price", "type": "quantitative", "title": "Price", "format": "$.2f"},
                ],
            },
        })
        if top_markers:
            layers.append({
                "data":{"values":top_markers},
                "mark":{
                    "type":"text","dy":-14,"fontSize":11,
                    "fontWeight":"bold","color":"#f2f8ff",
                },
                "encoding":{
                    "x":{"field":"t","type":"temporal"},
                    "y":{"field":"price","type":"quantitative"},
                    "text":{"field":"label"},
                },
            })
        if dip_markers:
            layers.append({
                "data":{"values":dip_markers},
                "mark":{
                    "type":"text","dy":16,"fontSize":10,
                    "fontWeight":"bold","color":"#c7e9f7",
                },
                "encoding":{
                    "x":{"field":"t","type":"temporal"},
                    "y":{"field":"price","type":"quantitative"},
                    "text":{"field":"label"},
                },
            })


    return _interactive_chart(layers, height=280)


def stair_step_chart_spec(result, line_overlay=False):
    result = result or {}
    bars = _bars(result, "daily")
    stair = result.get("stair_step") or {}
    if len(bars) < 3 or not stair.get("detected"):
        return None

    layers = _candlestick_layers(bars, x_type="temporal", line_overlay=line_overlay)
    steps = []
    for idx, step in enumerate(stair.get("steps") or [], start=1):
        price = _num(step.get("step_close"))
        date = step.get("date")
        if date and price is not None:
            steps.append({
                "t": str(date),
                "price": price,
                "label": f"Step {idx} +{_num(step.get('step_pct')) or 0:.1f}%",
            })

    if steps:
        layers.extend([
            {
                "data": {"values": steps},
                "mark": {"type": "point", "filled": True, "size": 130, "color": "#57f287"},
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                    "tooltip": [
                        {"field": "label", "title": "Step"},
                        {"field": "price", "type": "quantitative", "title": "Close", "format": "$.2f"},
                    ],
                },
            },
            {
                "data": {"values": steps},
                "mark": {
                    "type": "text",
                    "dy": -13,
                    "fontSize": 10,
                    "fontWeight": "bold",
                    "color": "#b7ffcf",
                },
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                    "text": {"field": "label"},
                },
            },
        ])

    center = _num(stair.get("current_plateau_center"))
    range_pct = _num(stair.get("current_plateau_range_pct"))
    if center is not None and steps:
        half = center * max(0.0025, (range_pct or 0.5) / 200.0)
        start_t = steps[-1]["t"]
        end_t = bars[-1]["t"]
        layers.append({
            "data": {
                "values": [
                    {"t": start_t, "low": center - half, "high": center + half},
                    {"t": end_t, "low": center - half, "high": center + half},
                ]
            },
            "mark": {"type": "area", "opacity": 0.18, "color": "#1e6f8c"},
            "encoding": {
                "x": {"field": "t", "type": "temporal"},
                "y": {
                    "field": "low",
                    "type": "quantitative",
                    "scale": {"zero": False},
                },
                "y2": {"field": "high"},
            },
        })

    _reaccel_label=None
    _reaccel_color="#ffd166"
    if stair.get("reaccelerating"):
        _reaccel_label="Reacceleration ✓ confirmed"
        _reaccel_color="#57f287"
    elif stair.get("reacceleration_developing"):
        _reaccel_label="Reacceleration developing"
        _reaccel_color="#ffd166"

    if _reaccel_label:
        marker = [{
            "t": bars[-1]["t"],
            "price": bars[-1]["c"],
            "label": _reaccel_label,
        }]
        layers.extend([
            {
                "data": {"values": marker},
                "mark": {"type": "point", "filled": True, "size": 135, "color": _reaccel_color},
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                },
            },
            {
                "data": {"values": marker},
                "mark": {
                    "type": "text",
                    "dy": -14,
                    "fontSize": 11,
                    "fontWeight": "bold",
                    "color": _reaccel_color,
                },
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                    "text": {"field": "label"},
                },
            },
        ])

    return _interactive_chart(layers, height=280)


def impulse_pullback_chart_spec(result, line_overlay=False):
    result = result or {}
    bars = _bars(result, "intraday")
    impulse = result.get("impulse_pullback") or {}
    pull = ((result.get("trade_plan") or {}).get("pullback") or {})
    if len(bars) < 2 or not impulse.get("detected"):
        return None

    layers = _candlestick_layers(bars, line_overlay=line_overlay)
    markers = []
    low = _num(impulse.get("impulse_low"))
    high = _num(impulse.get("impulse_high"))
    if low is not None:
        idx = min(
            range(len(bars)),
            key=lambda i: abs((_num(bars[i].get("l")) or bars[i]["c"]) - low),
        )
        markers.append({"t": bars[idx]["t"], "price": low, "label": "Impulse low"})
    if high is not None:
        idx = min(
            range(len(bars)),
            key=lambda i: abs((_num(bars[i].get("h")) or bars[i]["c"]) - high),
        )
        markers.append({"t": bars[idx]["t"], "price": high, "label": "Impulse high"})

    if markers:
        layers.extend([
            {
                "data": {"values": markers},
                "mark": {"type": "point", "filled": True, "size": 115, "color": "#8be9fd"},
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                },
            },
            {
                "data": {"values": markers},
                "mark": {
                    "type": "text",
                    "dy": -12,
                    "fontSize": 10,
                    "fontWeight": "bold",
                    "color": "#f2f8ff",
                },
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                    "text": {"field": "label"},
                },
            },
        ])

    entry_low = _num(pull.get("entry_low"))
    entry_high = _num(pull.get("entry_high"))
    if entry_low is not None and entry_high is not None:
        layers.append({
            "data": {
                "values": [
                    {"t": bars[0]["t"], "low": entry_low, "high": entry_high},
                    {"t": bars[-1]["t"], "low": entry_low, "high": entry_high},
                ]
            },
            "mark": {"type": "area", "opacity": 0.22, "color": "#b8872f"},
            "encoding": {
                "x": {"field": "t", "type": "temporal"},
                "y": {
                    "field": "low",
                    "type": "quantitative",
                    "scale": {"zero": False},
                },
                "y2": {"field": "high"},
            },
        })

    if impulse.get("bounce_confirmed"):
        marker = [{
            "t": bars[-1]["t"],
            "price": bars[-1]["c"],
            "label": "Reclaim ✓ confirmed",
        }]
        layers.append({
            "data": {"values": marker},
            "mark": {
                "type": "text",
                "dy": -14,
                "fontSize": 11,
                "fontWeight": "bold",
                "color": "#57f287",
            },
            "encoding": {
                "x": {"field": "t", "type": "temporal"},
                "y": {"field": "price", "type": "quantitative"},
                "text": {"field": "label"},
            },
        })

    return _interactive_chart(layers, height=270)


def support_resistance_chart_spec(result, line_overlay=False):
    result = result or {}
    bars = _bars(result, "intraday")
    if len(bars) < 2:
        return None

    levels = []
    last_t = bars[-1]["t"]
    for row in (result.get("supports") or [])[:4]:
        price = _num(row.get("price"))
        if price is not None:
            levels.append({
                "t": last_t,
                "price": price,
                "label": f"Support {price:.2f}",
                "kind": "support",
            })
    for row in (result.get("resistances") or [])[:4]:
        price = _num(row.get("price"))
        if price is not None:
            levels.append({
                "t": last_t,
                "price": price,
                "label": f"Resistance {price:.2f}",
                "kind": "resistance",
            })

    layers = _candlestick_layers(bars, line_overlay=line_overlay)
    layers.extend(_horizontal_level_layers(levels))
    return _interactive_chart(
        layers,
        height=260,
        resolve={"scale": {"color": "independent"}},
    )



def _plotly_candlestick_base(result, kind="intraday", *, height=320, line_overlay=False):
    bars = _bars(result or {}, kind)
    if len(bars) < 2:
        return None
    x = [row["t"] for row in bars]
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=x,
            open=[row.get("o") for row in bars],
            high=[row.get("h") for row in bars],
            low=[row.get("l") for row in bars],
            close=[row.get("c") for row in bars],
            name="Price",
            increasing_line_color="#00d26a",
            decreasing_line_color="#ff5a1f",
            increasing_fillcolor="#00d26a",
            decreasing_fillcolor="#ff5a1f",
        )
    )
    if line_overlay:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=[row.get("c") for row in bars],
                mode="lines",
                name="Close",
                line={"width": 1.4, "color": "#dbeafe"},
                opacity=0.62,
            )
        )
    fig.update_layout(
        height=height,
        template="plotly_dark",
        paper_bgcolor="#08111f",
        plot_bgcolor="#08111f",
        margin={"l": 8, "r": 8, "t": 24, "b": 8},
        dragmode="pan",
        hovermode="x unified",
        showlegend=False,
        xaxis_rangeslider_visible=False,
        uirevision="analyzer-plotly-v1",
    )
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        fixedrange=False,
        color="#b8c9dc",
    )
    fig.update_yaxes(
        title_text="Price",
        showgrid=True,
        gridcolor="#28435d",
        zeroline=False,
        fixedrange=False,
        color="#b8c9dc",
    )
    return fig


def _add_plotly_level(fig, price, label, color, *, dash="dash"):
    value = _num(price)
    if value is None:
        return
    fig.add_hline(
        y=value,
        line_dash=dash,
        line_width=1.5,
        line_color=color,
        annotation_text=label,
        annotation_position="top right",
        annotation_font_color=color,
    )


def trade_plan_plotly_figure(result, line_overlay=False):
    result = result or {}
    fig = _plotly_candlestick_base(result, "intraday", height=340, line_overlay=line_overlay)
    plan = result.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    if fig is None or not selected:
        return None

    entry_low = _num(selected.get("entry_low"))
    entry_high = _num(selected.get("entry_high"))
    if entry_low is not None and entry_high is not None:
        fig.add_hrect(
            y0=min(entry_low, entry_high),
            y1=max(entry_low, entry_high),
            fillcolor="#b8872f",
            opacity=0.20,
            line_width=0,
            annotation_text="Entry zone",
            annotation_position="top left",
        )

    _add_plotly_level(fig, selected.get("stop"), "Stop", "#ff6978")
    _add_plotly_level(fig, selected.get("target1"), "Target 1", "#50fa9b")
    _add_plotly_level(fig, selected.get("target2"), "Target 2", "#50fa9b")
    _add_plotly_level(fig, selected.get("stretch_target"), "Stretch", "#8be9fd")
    _add_plotly_level(fig, result.get("vwap"), "VWAP", "#bd93f9")
    return fig


def impulse_pullback_plotly_figure(result, line_overlay=False):
    result = result or {}
    fig = _plotly_candlestick_base(result, "intraday", height=330, line_overlay=line_overlay)
    impulse = result.get("impulse_pullback") or {}
    pull = ((result.get("trade_plan") or {}).get("pullback") or {})
    bars = _bars(result, "intraday")
    if fig is None or not impulse.get("detected"):
        return None

    for key, label in (("impulse_low", "Impulse low"), ("impulse_high", "Impulse high")):
        price = _num(impulse.get(key))
        if price is None:
            continue
        nearest = min(
            bars,
            key=lambda row: abs((_num(row.get("l" if key.endswith("low") else "h")) or row["c"]) - price),
        )
        fig.add_trace(
            go.Scatter(
                x=[nearest["t"]],
                y=[price],
                mode="markers+text",
                text=[label],
                textposition="top center",
                marker={"size": 9, "color": "#8be9fd"},
                hoverinfo="skip",
                showlegend=False,
            )
        )

    entry_low = _num(pull.get("entry_low"))
    entry_high = _num(pull.get("entry_high"))
    if entry_low is not None and entry_high is not None:
        fig.add_hrect(
            y0=min(entry_low, entry_high),
            y1=max(entry_low, entry_high),
            fillcolor="#b8872f",
            opacity=0.20,
            line_width=0,
            annotation_text="Pullback entry zone",
            annotation_position="top left",
        )
    if impulse.get("bounce_confirmed") and bars:
        fig.add_annotation(
            x=bars[-1]["t"],
            y=bars[-1]["c"],
            text="Reclaim ✓ confirmed",
            showarrow=True,
            arrowhead=2,
            font={"color": "#57f287"},
        )
    return fig


def multi_bounce_plotly_figure(result, line_overlay=False):
    result = result or {}
    fig = _plotly_candlestick_base(result, "intraday", height=340, line_overlay=line_overlay)
    sequence = result.get("bounce_sequence") or {}
    bars = _bars(result, "intraday")
    if fig is None or not sequence.get("detected"):
        return None

    for bounce in sequence.get("bounces") or []:
        number = int(bounce.get("number") or 0)
        low_time = bounce.get("pullback_low_time")
        peak_time = bounce.get("bounce_peak_time")
        low_idx = bounce.get("pullback_low_index")
        peak_idx = bounce.get("bounce_peak_index")

        if not low_time and isinstance(low_idx, int) and 0 <= low_idx < len(bars):
            low_time = bars[low_idx]["t"]
        if not peak_time and isinstance(peak_idx, int) and 0 <= peak_idx < len(bars):
            peak_time = bars[peak_idx]["t"]

        low_price = _num(bounce.get("pullback_low"))
        peak_price = _num(bounce.get("bounce_peak"))
        if low_time and low_price is not None:
            fig.add_trace(
                go.Scatter(
                    x=[str(low_time)],
                    y=[low_price],
                    mode="markers+text",
                    text=[f"B{number} low"],
                    textposition="bottom center",
                    marker={"size": 9, "color": "#8be9fd", "symbol": "triangle-up"},
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
        if peak_time and peak_price is not None:
            fig.add_trace(
                go.Scatter(
                    x=[str(peak_time)],
                    y=[peak_price],
                    mode="markers+text",
                    text=[f"Bounce #{number} ✓"],
                    textposition="top center",
                    marker={"size": 9, "color": "#57f287"},
                    hoverinfo="skip",
                    showlegend=False,
                )
            )

    next_num = int(sequence.get("next_bounce_number") or 1)
    if str(sequence.get("current_leg") or "").upper() == "BOUNCING" and bars:
        fig.add_annotation(
            x=bars[-1]["t"],
            y=bars[-1]["c"],
            text=f"Bounce #{next_num} developing",
            showarrow=True,
            arrowhead=2,
            font={"color": "#ffd166"},
        )
    return fig


def stair_step_plotly_figure(result, line_overlay=False):
    result = result or {}
    fig = _plotly_candlestick_base(result, "daily", height=340, line_overlay=line_overlay)
    stair = result.get("stair_step") or {}
    bars = _bars(result, "daily")
    if fig is None or not stair.get("detected"):
        return None

    steps = []
    for idx, step in enumerate(stair.get("steps") or [], start=1):
        price = _num(step.get("step_close"))
        date = step.get("date")
        if date and price is not None:
            steps.append((str(date), price, f"Step {idx} +{_num(step.get('step_pct')) or 0:.1f}%"))
    if steps:
        fig.add_trace(
            go.Scatter(
                x=[row[0] for row in steps],
                y=[row[1] for row in steps],
                mode="markers+text",
                text=[row[2] for row in steps],
                textposition="top center",
                marker={"size": 10, "color": "#57f287"},
                hoverinfo="skip",
                showlegend=False,
            )
        )

    center = _num(stair.get("current_plateau_center"))
    range_pct = _num(stair.get("current_plateau_range_pct"))
    if center is not None:
        half = center * max(0.0025, (range_pct or 0.5) / 200.0)
        fig.add_hrect(
            y0=center - half,
            y1=center + half,
            fillcolor="#1e6f8c",
            opacity=0.18,
            line_width=0,
            annotation_text="Current plateau",
            annotation_position="top left",
        )

    if bars and (stair.get("reaccelerating") or stair.get("reacceleration_developing")):
        label = "Reacceleration ✓ confirmed" if stair.get("reaccelerating") else "Reacceleration developing"
        color = "#57f287" if stair.get("reaccelerating") else "#ffd166"
        fig.add_annotation(
            x=bars[-1]["t"],
            y=bars[-1]["c"],
            text=label,
            showarrow=True,
            arrowhead=2,
            font={"color": color},
        )
    return fig


def support_resistance_plotly_figure(result, line_overlay=False):
    result = result or {}
    fig = _plotly_candlestick_base(result, "intraday", height=330, line_overlay=line_overlay)
    if fig is None:
        return None
    for row in (result.get("supports") or [])[:4]:
        price = _num(row.get("price"))
        if price is not None:
            _add_plotly_level(fig, price, f"Support {price:.2f}", "#50fa9b", dash="dot")
    for row in (result.get("resistances") or [])[:4]:
        price = _num(row.get("price"))
        if price is not None:
            _add_plotly_level(fig, price, f"Resistance {price:.2f}", "#ffb86c", dash="dot")
    return fig
