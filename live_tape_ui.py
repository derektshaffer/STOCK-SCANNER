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
    age = overlay.get("message_age_seconds")

    if status in {"idle", "disabled"} and not overlay.get("price"):
        return

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
    }:
        dot = "#ffd166"
        label = status.upper()
    else:
        dot = "#ff6b6b"
        label = "STREAM ERROR" if status == "error" else status.upper()

    age_text = f"{float(age):.1f}s ago" if age is not None else "waiting for tick"
    bid = _money(overlay.get("bid"))
    ask = _money(overlay.get("ask"))
    breakout = str(overlay.get("breakout_state") or "—")
    vwap_pos = str(overlay.get("vwap_position") or "N/A")

    cells = [
        ("LIVE PRICE", _money(overlay.get("price")), f"{label} · {age_text}"),
        ("BID / ASK", f"{bid} / {ask}", "streaming quote"),
        ("SPREAD", _pct(overlay.get("spread_pct")), "live quote spread"),
        ("LIVE VWAP", _money(overlay.get("vwap")), vwap_pos),
        (
            "SESSION VOL",
            _int(overlay.get("session_volume")),
            "regular-session seeded + streamed",
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

    if status == "rest_fallback":
        st.caption(
            "Alpaca's WebSocket connection is already in use, so this ticker is "
            "temporarily updating from REST snapshots instead. The app will retry "
            "the single live socket automatically."
        )
        if overlay.get("rest_error"):
            st.caption("REST fallback issue: " + str(overlay.get("rest_error"))[:180])
    elif overlay.get("error"):
        st.caption("Live stream: " + str(overlay.get("error"))[:180])
