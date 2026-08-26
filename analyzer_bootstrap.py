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
    from ml_integration import install_ml_analysis
    from ml_ui import render_ml_prediction

    # Layer order matters: the rule-based analyzer runs first, historical setup
    # matching enhances it second, and ML v1 reads that completed trade plan.
    install_historical_analysis(sa)
    install_ml_analysis(sa)

    target = Path(__file__).with_name("analyzer_ui_core.py")
    if not target.exists():
        raise FileNotFoundError("analyzer_ui_core.py is missing from the repository root.")

    # Reserve a slot immediately before the existing Trade plan details
    # expander. Historical Setup Match and ML v1 are populated there after the
    # core UI finishes and its result/card helpers become available.
    original_expander = st.expander
    analysis_slot = {"placeholder": None}

    def _expander_with_analysis_slot(label, *args, **kwargs):
        if (
            analysis_slot["placeholder"] is None
            and str(label).startswith("Trade plan details")
        ):
            analysis_slot["placeholder"] = st.empty()
        return original_expander(label, *args, **kwargs)

    st.expander = _expander_with_analysis_slot
    try:
        ns = runpy.run_path(str(target), run_name="__main__")
    finally:
        st.expander = original_expander

    result = ns.get("r") or {}
    card = ns.get("card")
    pp = ns.get("pp")

    if card and pp:
        slot = analysis_slot.get("placeholder")
        if slot is not None:
            with slot.container():
                render_historical_setup(st, pd, result, card, pp)
                render_ml_prediction(st, pd, result, card)
        else:
            # Fallback if the core UI changes and the expander label no longer
            # matches. Better to show the analysis layers at the end than hide them.
            render_historical_setup(st, pd, result, card, pp)
            render_ml_prediction(st, pd, result, card)
