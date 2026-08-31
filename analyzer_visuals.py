"""Vega-Lite visual snapshots for the Single Stock Analyzer.

These are explanatory views only. They use the exact compact OHLCV bars
returned by stock_analyzer.py so visual markers stay tied to the same evidence
that produced the Analyzer cards.
"""

from __future__ import annotations


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


def _price_line_layer(bars, *, x_type="temporal"):
    return {
        "data": {"values": bars},
        "mark": {
            "type": "line",
            "strokeWidth": 3,
            "color": "#f2f8ff",
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
                {"field": "c", "type": "quantitative", "title": "Close", "format": "$.2f"},
                {"field": "h", "type": "quantitative", "title": "High", "format": "$.2f"},
                {"field": "l", "type": "quantitative", "title": "Low", "format": "$.2f"},
            ],
        },
    }


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


def trade_plan_chart_spec(result):
    result = result or {}
    bars = _bars(result, "intraday")
    plan = result.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    if len(bars) < 2 or not selected:
        return None

    first_t, last_t = bars[0]["t"], bars[-1]["t"]
    entry_low = _num(selected.get("entry_low"))
    entry_high = _num(selected.get("entry_high"))
    layers = [_price_line_layer(bars)]

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
    return {
        "height": 280,
        "layer": layers,
        "resolve": {"scale": {"color": "independent"}},
        "config": _config(),
    }


def multi_bounce_chart_spec(result):
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
        if isinstance(low_idx, int) and 0 <= low_idx < len(bars):
            markers.append({
                "t": bars[low_idx]["t"],
                "price": _num(bounce.get("pullback_low")) or bars[low_idx]["c"],
                "label": f"B{number} dip",
                "kind": "dip",
            })
        if isinstance(peak_idx, int) and 0 <= peak_idx < len(bars):
            markers.append({
                "t": bars[peak_idx]["t"],
                "price": _num(bounce.get("bounce_peak")) or bars[peak_idx]["c"],
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

    layers = [_price_line_layer(bars)]
    if markers:
        layers.extend([
            {
                "data": {"values": markers},
                "mark": {"type": "point", "filled": True, "size": 120},
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                    "color": {
                        "field": "kind",
                        "type": "nominal",
                        "scale": {
                            "domain": ["confirmed", "dip", "developing"],
                            "range": ["#50fa9b", "#8be9fd", "#ffd166"],
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
            },
            {
                "data": {"values": markers},
                "mark": {
                    "type": "text",
                    "dy": -12,
                    "fontSize": 11,
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

    return {"height": 280, "layer": layers, "config": _config()}


def stair_step_chart_spec(result):
    result = result or {}
    bars = _bars(result, "daily")
    stair = result.get("stair_step") or {}
    if len(bars) < 3 or not stair.get("detected"):
        return None

    layers = [_price_line_layer(bars, x_type="temporal")]
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

    if stair.get("reaccelerating"):
        marker = [{
            "t": bars[-1]["t"],
            "price": bars[-1]["c"],
            "label": "Reacceleration active",
        }]
        layers.extend([
            {
                "data": {"values": marker},
                "mark": {"type": "point", "filled": True, "size": 135, "color": "#ffd166"},
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
                    "color": "#ffd166",
                },
                "encoding": {
                    "x": {"field": "t", "type": "temporal"},
                    "y": {"field": "price", "type": "quantitative"},
                    "text": {"field": "label"},
                },
            },
        ])

    return {"height": 280, "layer": layers, "config": _config()}


def impulse_pullback_chart_spec(result):
    result = result or {}
    bars = _bars(result, "intraday")
    impulse = result.get("impulse_pullback") or {}
    pull = ((result.get("trade_plan") or {}).get("pullback") or {})
    if len(bars) < 2 or not impulse.get("detected"):
        return None

    layers = [_price_line_layer(bars)]
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

    return {"height": 270, "layer": layers, "config": _config()}


def support_resistance_chart_spec(result):
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

    layers = [_price_line_layer(bars)]
    layers.extend(_horizontal_level_layers(levels))
    return {
        "height": 260,
        "layer": layers,
        "resolve": {"scale": {"color": "independent"}},
        "config": _config(),
    }
