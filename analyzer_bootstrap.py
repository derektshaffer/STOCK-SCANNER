from pathlib import Path
import os
import runpy

import pandas as pd
import streamlit as st


def _preload_secrets():
    """Load Streamlit secrets before stock_analyzer reads its environment."""
    try:
        secrets = dict(st.secrets)
    except Exception:
        return
    for key in (
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "ALPACA_LIVE_FEED",
        "ALPACA_HISTORICAL_FEED",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ANALYZER_REFRESH_SECONDS",
    ):
        value = secrets.get(key)
        if value is not None and str(value).strip():
            os.environ[key] = str(value).strip()


def run():
    _preload_secrets()

    import stock_analyzer as sa
    from historical_integration import install_historical_analysis
    from historical_ui import render_historical_setup

    # Install the setup-specific historical layer before the UI imports
    # `analyze` from stock_analyzer.
    install_historical_analysis(sa)

    target = Path(__file__).with_name("analyzer_ui_core.py")
    if not target.exists():
        raise FileNotFoundError("analyzer_ui_core.py is missing from the repository root.")

    ns = runpy.run_path(str(target), run_name="__main__")
    result = ns.get("r") or {}
    card = ns.get("card")
    pp = ns.get("pp")

    # Always render the section, even when a ticker has too little history;
    # in that case the UI clearly says there are not enough comparable days.
    if card and pp:
        render_historical_setup(st, pd, result, card, pp)
