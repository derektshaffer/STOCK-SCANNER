"""Display-only selection. Never scores, sorts, classifies or changes ACTION."""
import html
import math

VISIBILITY_VERSION = "cross-horizon-visibility-v1"
DESCRIPTION = (
    "Other best-fit horizons with meaningful intraday evidence. Uses the existing "
    "62-point moderate fit boundary plus positive 5-minute (≥0.3%) or "
    "15-minute (≥0.5%) momentum. Scanner order is preserved. "
    "Visibility only; Analyzer confirmation and all entry safeguards still apply."
)


def number(value):
    try:
        return float(value) if not isinstance(value, bool) and math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def cross_horizon_movers(records, selected="INTRADAY"):
    if selected != "INTRADAY":
        return []
    # Match both current surfaces conservatively, including malformed casing.
    visible = {str(r.get("symbol") or "").upper().strip() for r in records
               if str(r.get("timeframe_best_fit") or "").upper().strip() == "INTRADAY"
               or "INTRADAY" in {str(h).upper().strip() for h in (r.get("timeframe_fit_horizons") or [])}}
    result, seen = [], set(visible)
    for row in records:
        symbol = str(row.get("symbol") or "").upper().strip()
        fit = str(row.get("timeframe_best_fit") or "").upper().strip()
        score = number(row.get("timeframe_intraday_score"))
        m5, m15 = number(row.get("momentum_5m")), number(row.get("momentum_15m"))
        if (symbol and symbol not in seen and fit in {"SWING", "LONGER-TERM", "MIXED"}
                and row.get("source_mode") != "offhours_daily_timeframe"
                and score is not None and score >= 62
                and ((m5 is not None and m5 >= .3) or (m15 is not None and m15 >= .5))):
            result.append(row)
            seen.add(symbol)
    return result


def render_cross_horizon(st, records, selected, *, stale=False, on_analyze=None, card=None, source_payload=None):
    """A separate, uncapped surface; existing intraday and radar lists stay intact."""
    if selected != "INTRADAY":
        return
    rows = cross_horizon_movers(records, selected)
    st.subheader("Cross-Horizon Movers")
    st.caption(DESCRIPTION)
    if not rows:
        st.caption("No additional cross-horizon movers in this snapshot.")
    for row in rows:
        symbol = str(row["symbol"])
        if card:
            st.markdown(card(row), unsafe_allow_html=True)
        else:
            from live_price_quality import price_view, price_note
            price_record = dict(row)
            if st.session_state.get("_scanner_price_failure"):
                price_record.update(live_price_available=False,
                    live_price_provider_errors=[st.session_state["_scanner_price_failure"]])
            view = price_view(price_record)
            action = row.get("scanner_action") or "UNKNOWN"
            if not view["current"]:
                action = "DATA CHECK"
            text = (f"{symbol} · {row.get('timeframe_best_fit')} · "
                    f"Intraday fit {row.get('timeframe_intraday_score')} · {action}")
            st.markdown(f'<div class="cross-horizon-mover">{html.escape(text)}</div>', unsafe_allow_html=True)
            st.caption(price_note(view))
        if on_analyze:
            st.button(f"Analyze {symbol}", key=f"cross_horizon_analyze_{symbol}",
                      disabled=stale, on_click=on_analyze, args=(symbol, __import__("scanner_publication_identity").launch_publication(source_payload, symbol)))
