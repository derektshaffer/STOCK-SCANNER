import os
from live_price_quality import price_view

from alpaca_live_stream import (
    ensure_live_stream as ensure_alpaca_stream,
    get_live_overlay as get_alpaca_overlay,
    get_live_state as get_alpaca_state,
)
from tradier_live_stream import (
    ensure_live_stream as ensure_tradier_stream,
    get_live_overlay as get_tradier_overlay,
    get_live_state as get_tradier_state,
)


def tradier_configured():
    return bool(
        os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
        or os.environ.get("TRADIER_TOKEN", "").strip()
    )


def configured_provider():
    return "tradier" if tradier_configured() else "alpaca"


def ensure_live_stream(symbol, feed="iex", metrics=None):
    if tradier_configured():
        state = ensure_tradier_stream(symbol, metrics=metrics)
        state["provider"] = "tradier"
        return state

    state = ensure_alpaca_stream(symbol, feed, metrics=metrics)
    state["provider"] = "alpaca"
    return state


def get_live_state(symbol=None):
    if tradier_configured():
        state = get_tradier_state(symbol)
        state["provider"] = "tradier"
        return state

    state = get_alpaca_state(symbol)
    state["provider"] = "alpaca"
    return state


def get_live_overlay(metrics):
    if tradier_configured():
        overlay = get_tradier_overlay(metrics)
        overlay["provider"] = "tradier"
        # An explicitly selected Alpaca fallback keeps its own stream/source.
        # Never stamp its snapshot with Tradier's transport provenance.
        if not overlay.get("live_price_available") and str((metrics or {}).get("live_price_source") or "").startswith("alpaca_"):
            fallback = get_alpaca_overlay(metrics)
            if price_view(fallback)["current"]:
                fallback["provider"] = "alpaca"
                fallback["live_price_is_fallback"] = True
                fallback["live_price_provider_errors"] = (
                    overlay.get("live_price_provider_errors") or []
                ) + (fallback.get("live_price_provider_errors") or [])
                return fallback
        return overlay

    overlay = get_alpaca_overlay(metrics)
    overlay["provider"] = "alpaca"
    return overlay
