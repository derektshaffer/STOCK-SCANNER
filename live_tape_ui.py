import html


def _money(value):
    try:
        return "$" + f"{float(value):.4f}".rstrip("0").rstrip(".")
    except Exception:
        return "—"


def _pct(value):
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "—"


def _int(value):
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return "—"


def render_live_tape(st, overlay):
    overlay = overlay or {}
    status = str(overlay.get("status") or "idle")
    feed = str(overlay.get("feed") or "—").upper()
    trade_age = overlay.get("trade_age_seconds")
    quote_age = overlay.get("quote_age_seconds")

    if status == "streaming":
        dot = "#35e06f"
        label = f"{feed} LIVE"
    elif status == "rest_fallback":
        dot = "#ffd166"
        label = f"{feed} REST FALLBACK"
    elif status in {
        "connecting",
        "authenticating",
        "subscribing",
        "switching",
        "reconnecting",
        "connection_limit",
        "session_limit",
    }:
        dot = "#ffd166"
        label = status.upper()
    else:
        dot = "#ff6b6b"
        label = "STREAM ERROR" if status == "error" else status.upper()

    def _age_text(value):
        try:
            seconds = float(value)
        except Exception:
            return "waiting"
        if seconds < 1:
            return "<1s"
        if seconds < 60:
            return f"{seconds:.1f}s"
        return f"{seconds / 60.0:.1f}m"
    bid = _money(overlay.get("bid"))
    ask = _money(overlay.get("ask"))
    breakout = str(overlay.get("breakout_state") or "—")
    vwap_pos = str(overlay.get("vwap_position") or "N/A")

    live_price_value=(
        _money(overlay.get("price"))
        if overlay.get("live_price_available") is not False and overlay.get("price") is not None
        else "UNAVAILABLE"
    )
    cells = [
        ("LIVE PRICE", live_price_value, label),
        ("BID / ASK", f"{bid} / {ask}", "streaming quote"),
        ("SPREAD", _pct(overlay.get("spread_pct")), "live quote spread"),
        ("LIVE VWAP", _money(overlay.get("vwap")), vwap_pos),
        (
            "SESSION VOL",
            _int(overlay.get("session_volume")),
            "Tradier session + stream" if str(overlay.get("provider") or "").lower() == "tradier"
            else "session seeded + streamed",
        ),
        ("BREAKOUT", breakout, "vs current trade-plan trigger"),
    ]

    rendered = []
    for title, value, note in cells:
        rendered.append(
            '<div style="flex:1;min-width:0;padding:6px 9px;border:1px solid #223d5a;'
            'border-radius:9px;background:#0c1828;">'
            f'<div style="font-size:9px;font-weight:800;letter-spacing:.08em;color:#91a7c2;">'
            f'{html.escape(title)}</div>'
            f'<div style="font-size:17px;font-weight:800;color:#e9f2ff;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis;">{html.escape(str(value))}</div>'
            f'<div style="font-size:9px;color:#91a7c2;white-space:nowrap;overflow:hidden;'
            f'text-overflow:ellipsis;">{html.escape(str(note))}</div></div>'
        )

    st.markdown(
        '<div style="display:flex;gap:7px;margin:3px 0 6px;align-items:stretch;">'
        f'<div style="width:8px;border-radius:8px;background:{dot};"></div>'
        + "".join(rendered)
        + '</div>',
        unsafe_allow_html=True,
    )

    if status == "streaming":
        try:
            quote_is_stale = float(quote_age) >= 15.0
        except Exception:
            quote_is_stale = False

        freshness_text = (
            "Freshness · trade "
            + _age_text(trade_age)
            + " · quote "
            + _age_text(quote_age)
        )
        if quote_is_stale:
            freshness_text += " · live data may be stale"
        st.caption(freshness_text)

    if status == "rest_fallback":
        st.caption(
            "Alpaca's WebSocket connection is already in use, so this ticker is "
            "temporarily updating from REST snapshots instead. The app will retry "
            "the single live socket automatically."
        )
        if overlay.get("rest_error"):
            st.caption("REST fallback issue: " + str(overlay.get("rest_error"))[:180])
    elif status == "session_limit":
        st.caption(
            "Tradier reports that its one market-data stream session is already in use. "
            "Close the other Tradier stream/session and this app will reconnect automatically."
        )
    elif overlay.get("error"):
        st.caption("Live stream: " + str(overlay.get("error"))[:180])
