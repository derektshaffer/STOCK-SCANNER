import os

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
        return overlay

    overlay = get_alpaca_overlay(metrics)
    overlay["provider"] = "alpaca"
    return overlay
