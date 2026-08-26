from pathlib import Path
import os
import runpy

import pandas as pd
import streamlit as st

# Streamlit Cloud secrets normally get copied into environment variables by
# analyzer_app.py. Preload them here as well so the historical-pattern wrapper
# can safely import stock_analyzer before the main UI runs.
def _preload_secrets():
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
    ):
        value = secrets.get(key)
        if value is not None and str(value).strip():
            os.environ[key] = str(value).strip()

_preload_secrets()

import stock_analyzer as _sa
from historical_patterns import analyze_historical_patterns

# Keep one unwrapped base function across Streamlit reruns so wrappers never
# stack recursively.
if not hasattr(_sa, "_base_analyze_for_history"):
    _sa._base_analyze_for_history = _sa.analyze
_base_analyze = _sa._base_analyze_for_history
_sa.ANALYZER_ENGINE_VERSION = "trade-plan-v3-historical-patterns"

def _num(value):
    try:
        return float(value)
    except Exception:
        return None

def _clamp(value, lo, hi):
    return max(lo, min(hi, value))

def _enhanced_analyze(symbol):
    metrics = _base_analyze(symbol)
    old_score = float(metrics.get("score") or 50)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

    gap_pct = None
    try:
        snap = _sa.snapshot(symbol.upper().strip(), _sa.LIVE_FEED)
        day = snap.get("dailyBar") or {}
        day_open = _num(day.get("o"))
        prev_close = _num(metrics.get("prev_close"))
        if day_open and prev_close:
            gap_pct = _sa.pct(day_open, prev_close)
    except Exception:
        pass
    metrics["gap_pct"] = round(gap_pct, 2) if gap_pct is not None else None

    try:
        setup = analyze_historical_patterns(
            symbol=symbol,
            now=now,
            current_day_pct=metrics.get("day_pct"),
            current_gap_pct=gap_pct,
            current_volume_pace=metrics.get("volume_pace"),
            fetch_bars=_sa.try_sip_delayed_bars,
            et=_sa.ET,
        )
    except Exception as exc:
        setup = {
            "status": "unavailable",
            "sample_count": 0,
            "matches": [],
            "error": str(exc)[:160],
        }

    metrics["historical_setup"] = setup
    hist = metrics.get("historical_analogs")
    if isinstance(hist, dict):
        hist["setup_patterns"] = setup

    # Let same-ticker historical setup matches affect the actual setup score.
    new_score = old_score
    score_reasons = list(metrics.get("score_reasons") or [])
    if setup.get("status") == "ok" and int(setup.get("sample_count") or 0) >= 5:
        bias = _num(setup.get("bias_score"))
        if bias is not None:
            new_score += _clamp(bias * 0.35, -7, 7)
            if bias >= 6:
                score_reasons.append("bullish same-ticker setup history")
            elif bias <= -6:
                score_reasons.append("bearish same-ticker setup history")

        failure = _num(setup.get("breakout_failure_pct"))
        follow = _num(setup.get("breakout_follow_through_pct"))
        if failure is not None and failure >= 65 and (follow is None or follow < 45):
            new_score -= 3
            score_reasons.append("historical breakout failure risk")

    new_score = round(_clamp(new_score, 0, 100), 1)
    metrics["score"] = new_score
    metrics["score_reasons"] = score_reasons

    day_pct = _num(metrics.get("day_pct")) or 0
    vwap_ext = _num(metrics.get("vwap_extension_pct")) or 0
    chase = max(0, day_pct - 25) * 0.16 + max(0, vwap_ext - 5) * 0.5
    metrics["grade"] = "A" if new_score >= 78 else "B" if new_score >= 65 else "C" if new_score >= 52 else "REJECT"
    metrics["entry_quality"] = "FAVORABLE" if new_score >= 72 and chase < 10 else "WAIT / CONFIRM" if new_score >= 55 else "POOR / HIGH RISK"

    # Also feed the historical layer into the displayed trade-plan confidence
    # and breakout decision.
    plan = metrics.get("trade_plan") or {}
    if plan:
        plan["historical_setup"] = setup
        confidence = float(plan.get("confidence") or old_score)
        confidence += new_score - old_score

        failure = _num(setup.get("breakout_failure_pct"))
        follow = _num(setup.get("breakout_follow_through_pct"))
        elevated_breakout_risk = (
            setup.get("status") == "ok"
            and int(setup.get("breakout_test_count") or 0) >= 3
            and failure is not None
            and failure >= 60
            and (follow is None or follow <= 45)
        )
        if elevated_breakout_risk:
            confidence -= 5

        confidence = int(round(_clamp(confidence, 0, 95)))
        plan["confidence"] = confidence
        plan["confidence_label"] = "HIGH" if confidence >= 75 else "MODERATE" if confidence >= 58 else "LOW"

        reasons = list(plan.get("reasons") or [])
        if setup.get("bias_label") == "BULLISH":
            reasons.insert(0, "Same-ticker historical setup matches lean bullish.")
        elif setup.get("bias_label") == "BEARISH":
            reasons.insert(0, "Same-ticker historical setup matches lean bearish, so confirmation matters more.")
        if elevated_breakout_risk:
            reasons.append("Historical breakout-failure rate is elevated; prefer a hold/retest over a quick poke above resistance.")
            if plan.get("status") == "ENTRY AVAILABLE" and plan.get("preferred_plan") == "breakout":
                plan["status"] = "WAIT"
                plan["action"] = "WAIT FOR BREAKOUT TO HOLD — history shows frequent failures"
        plan["reasons"] = reasons

        plan["method_note"] = (
            "Rule-based long momentum decision support using VWAP, support/resistance, ATR, "
            "momentum, volume pace, spread/liquidity, same-ticker spike analogs plus setup-"
            "matched gap/run-vs-fade behavior, breakout failure/follow-through, VWAP reclaim "
            "tendencies, early pullback depth, time-of-day behavior and catalyst context. "
            "Targets are scenarios, not guarantees."
        )
        metrics["trade_plan"] = plan

    return metrics

_sa.analyze = _enhanced_analyze

# Streamlit Cloud is configured to launch app.py. Keep the main analyzer UI in
# analyzer_app.py, then append the new historical-pattern readout underneath.
target = Path(__file__).with_name("analyzer_app.py")
if not target.exists():
    raise FileNotFoundError(
        "analyzer_app.py was not found in the repository root. "
        "Upload analyzer_app.py next to app.py."
    )

ns = runpy.run_path(str(target), run_name="__main__")
r = ns.get("r") or {}
setup = r.get("historical_setup") or (r.get("historical_analogs") or {}).get("setup_patterns") or {}

st.markdown('<div class="section">Historical setup match</div>', unsafe_allow_html=True)
if setup.get("status") == "ok":
    card = ns.get("card")
    pp = ns.get("pp")
    intr = setup.get("intraday") or {}
    cols = st.columns(6)
    bias = setup.get("bias_score")
    if card:
        card(
            cols[0],
            "SETUP BIAS",
            setup.get("bias_label") or "MIXED",
            f"Score {bias:+.1f}" if bias is not None else "same-ticker history",
            "good" if setup.get("bias_label") == "BULLISH" else "bad" if setup.get("bias_label") == "BEARISH" else "warn",
        )
        card(cols[1], "SIMILAR DAYS", str(setup.get("sample_count", 0)), setup.get("setup_label") or "setup matches")
        gr, gf = setup.get("gap_run_pct"), setup.get("gap_fade_pct")
        card(cols[2], "GAP RUN / FADE", f"{gr:.0f}% / {gf:.0f}%" if gr is not None and gf is not None else "—", f'n={setup.get("gap_sample_count", 0)} gap analogs')
        bf, bfail = setup.get("breakout_follow_through_pct"), setup.get("breakout_failure_pct")
        card(cols[3], "BREAKOUT HOLD / FAIL", f"{bf:.0f}% / {bfail:.0f}%" if bf is not None and bfail is not None else "—", f'n={setup.get("breakout_test_count", 0)} tested')
        vr = intr.get("vwap_reclaim_follow_through_pct")
        card(cols[4], "VWAP RECLAIM", f"{vr:.0f}% follow" if vr is not None else "—", f'n={intr.get("sample_count", 0)} matched intraday days')
        pb = intr.get("median_first_pullback_pct")
        card(cols[5], "EARLY PULLBACK", pp(pb) if pp else str(pb or "—"), f'High most often: {intr.get("session_high_most_common") or "—"}')

    for note in (setup.get("notes") or [])[:5]:
        st.caption("• " + str(note))

    matches = pd.DataFrame(setup.get("matches") or [])
    if not matches.empty:
        show = [
            c for c in [
                "date",
                "pattern",
                "gap_pct",
                "day_pct",
                "relative_volume",
                "same_day_pullback_pct",
                "next_day_pct",
                "next_day_mfe_pct",
                "breakout_follow",
                "breakout_failed",
            ] if c in matches.columns
        ]
        st.dataframe(matches[show], width="stretch", hide_index=True)
    st.caption(
        "This historical setup layer is included in the setup score and trade-plan confidence. "
        "Similarity uses today's move size, opening gap and relative volume; matched recent "
        "5-minute sessions add VWAP-reclaim, early-pullback and time-of-day tendencies."
    )
elif setup.get("status") == "unavailable":
    st.caption("Historical setup matching is temporarily unavailable; the rest of the analyzer is still active.")
else:
    st.info("Not enough comparable same-ticker history for the setup-pattern layer yet.")
