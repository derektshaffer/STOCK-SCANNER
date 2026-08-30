import html
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

SCAN_FILE = Path("scan_logs/latest_scan.json")
AUTO_SCAN_SECONDS = 120
AUTO_STATUS_REFRESH_SECONDS = 15

st.set_page_config(
    page_title="Momentum Scanner",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
:root{
  --bg:#0b1220;--panel:#111b2e;--panel2:#16233a;--text:#f4f7fb;
  --muted:#9fb0c9;--line:#2b3b56;--green:#22c55e;--amber:#f59e0b;
  --red:#ef4444;--blue:#38bdf8
}
.stApp{background:var(--bg);color:var(--text)}
.block-container{padding-top:1.1rem;padding-bottom:3rem;max-width:1500px}
.header{border:1px solid var(--line);background:linear-gradient(135deg,#101a2c,#0d1728);
  padding:20px 24px;border-radius:18px;margin-bottom:16px}
.title{font-size:34px;font-weight:900;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:14px;margin-top:5px}
.pill,.badge{display:inline-block;border-radius:999px;font-size:12px;font-weight:850;
  padding:6px 9px;margin:4px 5px 4px 0;border:1px solid transparent}
.green{color:#b8f7ca;background:rgba(34,197,94,.13);border-color:rgba(34,197,94,.34)}
.amber{color:#ffe0a0;background:rgba(245,158,11,.14);border-color:rgba(245,158,11,.34)}
.red{color:#ffc1c1;background:rgba(239,68,68,.14);border-color:rgba(239,68,68,.34)}
.blue{color:#bfeaff;background:rgba(56,189,248,.12);border-color:rgba(56,189,248,.34)}
.section{font-size:22px;font-weight:900;margin:24px 0 4px}
.section-sub{color:var(--muted);font-size:14px;margin-bottom:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:15px 17px;min-height:100px}
.stat-k{color:var(--muted);font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}
.stat-v{font-size:31px;font-weight:950;margin-top:5px}
.stat-n{color:var(--muted);font-size:12px;margin-top:7px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;
  padding:18px;margin-bottom:12px;min-height:370px;box-shadow:0 8px 24px rgba(0,0,0,.12)}
.card-a{border-top:5px solid var(--green)}
.card-b{border-top:5px solid var(--blue)}
.card-c{border-top:5px solid var(--amber)}
.card-r{border-top:5px solid var(--red)}
.trow{display:flex;justify-content:space-between;gap:12px}
.ticker{font-size:30px;font-weight:950}
.score{font-size:29px;font-weight:950}
.score small{display:block;color:var(--muted);font-size:10px;text-align:right;letter-spacing:.08em}
.price{font-size:18px;font-weight:800;margin:3px 0 10px}
.pos{color:#65e98d}.neg{color:#ff8181}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}
.metric{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:9px 10px}
.mk{color:var(--muted);font-size:10px;font-weight:850;letter-spacing:.06em}
.mv{font-size:17px;font-weight:900;margin-top:2px}
.note{background:rgba(255,255,255,.025);border-left:3px solid var(--line);
  padding:9px 10px;margin-top:10px;border-radius:6px}
.nk{color:var(--muted);font-size:10px;font-weight:850;letter-spacing:.06em}
.nv{font-size:13px;line-height:1.45;margin-top:3px}
.news-time{color:var(--muted);font-size:11px;font-weight:750;margin-top:6px}
.legend-box{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:14px 16px;margin:14px 0 20px}
.legend-title{font-size:13px;font-weight:900;letter-spacing:.06em;text-transform:uppercase;margin-bottom:9px}
.legend-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.legend-item{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.legend-term{font-size:12px;font-weight:900;margin-bottom:4px}
.legend-def{color:var(--muted);font-size:12px;line-height:1.4}
.auto-box{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:7px 10px;margin:2px 0 6px}
.market-box{display:flex;align-items:center;justify-content:space-between;gap:10px;
  min-height:38px;box-sizing:border-box;background:var(--panel);border:1px solid var(--line);
  border-radius:9px;padding:6px 10px;margin:0}
.market-box.open{border-left:4px solid var(--green)}
.market-box.closed{border-left:4px solid var(--amber)}
.market-main{font-size:13px;font-weight:900;white-space:nowrap}
.market-time{color:var(--muted);font-size:11px;font-weight:700;white-space:nowrap}
.auto-on{border-left:4px solid var(--green)}
.auto-wait{border-left:4px solid var(--amber)}
.auto-off{border-left:4px solid var(--muted)}
.bar-row{margin:9px 0 13px}
.bar-head{display:flex;justify-content:space-between;font-size:13px;font-weight:750}
.bar-track{height:9px;background:#17243a;border-radius:999px;overflow:hidden;
  margin-top:5px;border:1px solid #263750}
.bar-fill{height:100%;background:linear-gradient(90deg,#f59e0b,#ef4444);border-radius:999px}
div[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.stButton button{border-radius:8px;font-weight:850;min-height:38px;height:38px;padding-top:4px;padding-bottom:4px}
@media(max-width:900px){.title{font-size:28px}.ticker{font-size:27px}.grid{grid-template-columns:1fr}.legend-grid{grid-template-columns:1fr}}

/* COMPACT MOMENTUM SCANNER LAYOUT */
.block-container{
  padding-top:.35rem !important;
  padding-bottom:1.25rem !important;
}
.block-container [data-testid="stVerticalBlock"]{
  gap:.42rem !important;
}
.header{
  padding:10px 14px !important;
  border-radius:12px !important;
  margin-bottom:7px !important;
}
.title{
  font-size:25px !important;
}
.sub{
  font-size:12px !important;
  margin-top:3px !important;
}
.pill,.badge{
  font-size:10px !important;
  padding:4px 7px !important;
  margin:2px 4px 2px 0 !important;
}
.section{
  font-size:18px !important;
  margin:13px 0 2px !important;
}
.section-sub{
  font-size:12px !important;
  line-height:1.25 !important;
  margin-bottom:6px !important;
}
.stat{
  padding:9px 11px !important;
  min-height:72px !important;
  border-radius:10px !important;
}
.stat-k{
  font-size:9px !important;
}
.stat-v{
  font-size:24px !important;
  margin-top:2px !important;
}
.stat-n{
  font-size:10px !important;
  margin-top:3px !important;
}
.card{
  padding:11px !important;
  margin-bottom:7px !important;
  min-height:0 !important;
  border-radius:11px !important;
}
.card-a,.card-b,.card-c,.card-r{
  border-top-width:3px !important;
}
.ticker{
  font-size:24px !important;
}
.score{
  font-size:23px !important;
}
.score small{
  font-size:9px !important;
}
.price{
  font-size:15px !important;
  margin:2px 0 6px !important;
}
.grid{
  gap:6px !important;
  margin:7px 0 !important;
}
.metric{
  padding:6px 8px !important;
  border-radius:8px !important;
}
.mk{
  font-size:9px !important;
}
.mv{
  font-size:15px !important;
  margin-top:1px !important;
}
.note{
  padding:6px 8px !important;
  margin-top:6px !important;
}
.nk{
  font-size:9px !important;
}
.nv{
  font-size:11.5px !important;
  line-height:1.3 !important;
  margin-top:2px !important;
}
.news-time{
  font-size:10px !important;
  margin-top:3px !important;
}
.legend-box{
  padding:9px 10px !important;
  margin:8px 0 10px !important;
  border-radius:10px !important;
}
.legend-title{
  margin-bottom:6px !important;
}
.legend-grid{
  gap:6px !important;
}
.legend-item{
  padding:7px 8px !important;
  border-radius:8px !important;
}
.legend-term{
  font-size:11px !important;
  margin-bottom:2px !important;
}
.legend-def{
  font-size:11px !important;
  line-height:1.3 !important;
}
.bar-row{
  margin:6px 0 8px !important;
}
.bar-track{
  height:7px !important;
  margin-top:3px !important;
}
[data-testid="stExpander"]{
  margin:4px 0 !important;
}
div[data-testid="stAlert"]{
  padding-top:6px !important;
  padding-bottom:6px !important;
  margin:4px 0 !important;
}


/* SECOND COMPACT PASS */
.header{
  padding:8px 12px !important;
  margin-bottom:5px !important;
}
.stat{
  padding:7px 9px !important;
  min-height:64px !important;
}
.card{
  padding:9px !important;
  margin-bottom:5px !important;
}
.grid{
  margin:5px 0 !important;
  gap:5px !important;
}
.metric{
  padding:5px 7px !important;
}
.note{
  padding:5px 7px !important;
  margin-top:5px !important;
}
.section{
  margin:10px 0 2px !important;
}
.section-sub{
  margin-bottom:4px !important;
}
.top-candidates-section{
  font-size:24px !important;
  font-weight:950 !important;
  margin-top:20px !important;
  margin-bottom:5px !important;
  line-height:1.15 !important;
}
.legend-box{
  margin:6px 0 8px !important;
  padding:7px 8px !important;
}

</style>
""",
    unsafe_allow_html=True,
)


def secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return os.environ.get(name, "").strip()


def configured_live_feed():
    feed = (secret("ALPACA_LIVE_FEED") or "iex").strip().lower()
    return feed if feed in {"iex", "sip"} else "iex"


def get_tradier_token():
    return secret("TRADIER_ACCESS_TOKEN") or secret("TRADIER_TOKEN")


def configured_live_provider():
    return "tradier" if get_tradier_token() else "alpaca"


def configured_live_label():
    if configured_live_provider() == "tradier":
        return "TRADIER CONSOLIDATED"
    return f"ALPACA {configured_live_feed().upper()}"


def market_session_phase(now_et=None):
    now_et = now_et or datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return "closed"
    minutes = now_et.hour * 60 + now_et.minute
    if (4 * 60) <= minutes < (9 * 60 + 30):
        return "premarket"
    if (9 * 60 + 30) <= minutes < (16 * 60):
        return "regular"
    if (16 * 60) <= minutes < (20 * 60):
        return "afterhours"
    return "closed"


def live_feed_available(now_et):
    phase = market_session_phase(now_et)
    if phase == "closed":
        return False
    if configured_live_provider() == "tradier":
        return True
    if configured_live_feed() == "sip":
        return True
    minutes = now_et.hour * 60 + now_et.minute
    return (8 * 60) <= minutes < (17 * 60)


def market_is_open():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return live_feed_available(now_et), now_et


def run_scanner():
    key = secret("ALPACA_API_KEY")
    sec = secret("ALPACA_SECRET_KEY")
    tradier_token = get_tradier_token()
    has_alpaca = bool(key and sec)
    if not has_alpaca and not tradier_token:
        return (
            False,
            "No market-data provider is configured. Add either "
            "TRADIER_ACCESS_TOKEN (preferred) or both ALPACA_API_KEY and "
            "ALPACA_SECRET_KEY in Streamlit Secrets.",
        )

    env = os.environ.copy()
    if has_alpaca:
        env["ALPACA_API_KEY"] = key
        env["ALPACA_SECRET_KEY"] = sec
    else:
        env.pop("ALPACA_API_KEY", None)
        env.pop("ALPACA_SECRET_KEY", None)
    env["ALPACA_LIVE_FEED"] = configured_live_feed()
    if tradier_token:
        env["TRADIER_ACCESS_TOKEN"] = tradier_token
        # Keep the live Streamlit Scanner on the same broad candidate-discovery
        # process as the scheduled GitHub collector used for forward validation.
        env["SCANNER_TRADIER_DISCOVERY"] = "1"
        env["SCANNER_DISCOVERY_UNIVERSE_SIZE"] = "1200"

    started = time.perf_counter()
    try:
        p = subprocess.run(
            [sys.executable, "stock_scanner.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "The scanner exceeded its 3-minute timeout."

    st.session_state["scanner_out"] = p.stdout[-12000:]
    st.session_state["scanner_err"] = p.stderr[-6000:]

    if p.returncode != 0:
        error = p.stderr.strip() or p.stdout.strip() or "Unknown scanner error"
        return False, error[-3000:]

    elapsed = time.perf_counter() - started
    st.session_state["scanner_last_runtime_seconds"] = round(elapsed, 1)

    return (
        SCAN_FILE.exists(),
        f"Fresh scan complete in {elapsed:.1f}s."
        if SCAN_FILE.exists()
        else (
            "Scanner ran, but latest_scan.json was not created "
            f"(runtime {elapsed:.1f}s)."
        ),
    )


def load_scan():
    if not SCAN_FILE.exists():
        return None
    try:
        return json.loads(SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def f(v, d=1, suffix=""):
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{d}f}{suffix}"
    except Exception:
        return "—"


def metric(k, v, cls=""):
    return (
        f'<div class="metric"><div class="mk">{html.escape(k)}</div>'
        f'<div class="mv {cls}">{html.escape(v)}</div></div>'
    )


def ml_display(c):
    if not c.get("ml_validated"):
        status = str(c.get("ml_status") or "").lower()
        if status in {"failed_validation", "prediction_error", "error"}:
            return "NOT VALID", "neg"
        return "LEARNING", "muted"

    try:
        probability = float(c.get("ml_continuation_prob_pct"))
    except (TypeError, ValueError):
        return "—", "muted"

    cls = "pos" if probability >= 65 else ("muted" if probability >= 50 else "neg")
    return f"{probability:.0f}%", cls


def format_catalyst_time(news):
    """Return the catalyst publication timestamp in U.S. Eastern Time."""
    raw = news.get("published_at") or news.get("created_at")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            et = dt.astimezone(ZoneInfo("America/New_York"))
            stamp = et.strftime("%a %b %d, %Y · %I:%M %p ET").replace(" 0", " ")
            age = news.get("age_hours")
            if age is not None:
                try:
                    age = float(age)
                    if age < 1:
                        age_text = f"{max(1, round(age * 60))} min ago"
                    elif age < 24:
                        age_text = f"{age:.1f}h ago"
                    else:
                        age_text = f"{age / 24:.1f}d ago"
                    return f"Published {stamp} · {age_text}"
                except (TypeError, ValueError):
                    pass
            return f"Published {stamp}"
        except (TypeError, ValueError):
            pass

    age = news.get("age_hours")
    if age is not None:
        try:
            age = float(age)
            if age < 1:
                return f"Published about {max(1, round(age * 60))} min before this scan"
            if age < 24:
                return f"Published about {age:.1f}h before this scan"
            return f"Published about {age / 24:.1f}d before this scan"
        except (TypeError, ValueError):
            pass
    return "Publication time unavailable"


def card(c):
    grade = str(c.get("setup_grade") or "REJECT")
    label = str(c.get("setup_label") or "")
    card_cls = {"A": "card-a", "B": "card-b", "C": "card-c"}.get(grade, "card-r")
    badge_cls = {"A": "green", "B": "blue", "C": "amber"}.get(grade, "red")

    score = float(c.get("score") or 0)
    score_color = "#65e98d" if score >= 75 else ("#ffd166" if score >= 60 else "#ff8181")

    day = c.get("day_pct")
    day_cls = "pos" if (day or 0) >= 0 else "neg"

    passed = bool(c.get("passed_base_filters"))
    pass_badge = (
        '<span class="badge green">BASE FILTERS PASS</span>'
        if passed
        else '<span class="badge amber">FILTERED / NEAR MISS</span>'
    )
    vwap_badge = (
        '<span class="badge green">ABOVE VWAP</span>'
        if c.get("above_vwap")
        else '<span class="badge red">BELOW VWAP</span>'
    )

    alert = c.get("alert_tier")
    alert_badge = ""
    if alert:
        ac = "green" if alert == "HIGH" else ("blue" if alert == "WATCH" else "amber")
        alert_badge = f'<span class="badge {ac}">ALERT {html.escape(str(alert))}</span>'

    action = str(c.get("scanner_action") or "WATCH")
    action_reason = str(c.get("scanner_action_reason") or "")
    action_color = (
        "red"
        if action == "NO TRADE"
        else "amber"
        if action in {"CAUTION", "WAIT", "WAIT PULLBACK"}
        else "blue"
    )
    action_badge = (
        f'<span class="badge {action_color}">ACTION {html.escape(action)}</span>'
    )

    fit = str(c.get("timeframe_best_fit") or "UNKNOWN")
    fit_badge_cls = {
        "INTRADAY": "blue",
        "SWING": "green",
        "LONGER-TERM": "amber",
        "MIXED": "blue",
    }.get(fit, "amber")
    fit_badge = (
        f'<span class="badge {fit_badge_cls}">BEST FIT {html.escape(fit)}</span>'
    )
    fit_reason = str(c.get("timeframe_fit_reason") or "")

    ml_text, ml_cls = ml_display(c)

    spread = (
        c.get("live_spread_pct")
        if c.get("live_spread_pct") is not None
        else c.get("iex_spread_pct")
        if c.get("iex_spread_pct") is not None
        else c.get("spread_pct")
    )

    notes = (
        (c.get("grade_reasons") or [])
        + (c.get("failed_filters") or [])
        + (c.get("tradability_warnings") or [])
        + (c.get("setup_flags") or [])
    )
    note = " · ".join(str(x) for x in notes[:4]) or "No major issues flagged."

    news = c.get("news") or {}
    news_bits = [news.get("category"), news.get("headline")]
    news_text = " — ".join(str(x) for x in news_bits if x)
    news_time = format_catalyst_time(news)
    news_html = (
        f'<div class="note"><div class="nk">CATALYST</div>'
        f'<div class="nv">{html.escape(news_text[:220])}</div>'
        f'<div class="news-time">{html.escape(news_time)}</div></div>'
        if news_text
        else ""
    )

    return f"""
<div class="card {card_cls}">
  <div class="trow">
    <div>
      <div class="ticker">{html.escape(str(c.get("symbol") or "—"))}</div>
      <div class="price">${f(c.get("price"),2)}
        <span class="{day_cls}">&nbsp;{f(day,1,"%")} today</span>
      </div>
    </div>
    <div class="score" style="color:{score_color}">{score:.0f}<small>SCORE / 100</small></div>
  </div>
  <div>
    <span class="badge {badge_cls}">GRADE {html.escape(grade)} · {html.escape(label)}</span>
    {fit_badge}{action_badge}{pass_badge}{alert_badge}{vwap_badge}
  </div>
  <div class="note"><div class="nk">ACTION</div><div class="nv">{html.escape(action_reason[:260])}</div></div>
  <div class="note"><div class="nk">TIMEFRAME FIT</div><div class="nv">{html.escape(fit_reason[:260] or "Timeframe evidence is still limited.")}</div></div>
  <div class="grid">
    {metric("5 MIN",f(c.get("momentum_5m"),2,"%"),"pos" if (c.get("momentum_5m") or 0)>0 else "muted")}
    {metric("15 MIN",f(c.get("momentum_15m"),2,"%"),"pos" if (c.get("momentum_15m") or 0)>0 else "muted")}
    {metric("ML 60M",ml_text,ml_cls)}
    {metric("TOD VOL PACE",f(c.get("volume_pace"),2,"x"),"pos" if (c.get("volume_pace") or 0)>=1.5 else "muted")}
    {metric("NORMAL VOL BY NOW",f(c.get("expected_volume_fraction_pct"),1,"%"))}
    {metric("VWAP PRICE","$"+f(c.get("vwap"),2),"pos" if c.get("above_vwap") else "neg")}
    {metric("FROM HIGH",f(c.get("distance_from_high_pct"),2,"%"))}
    {metric("LIVE SPREAD",f(spread,2,"%"))}
    {metric("LIQUIDITY","$"+f((c.get("liquidity_dollar_volume") or 0)/1_000_000,1,"M"))}
  </div>
  <div class="note"><div class="nk">SETUP READ</div><div class="nv">{html.escape(note[:250])}</div></div>
  {news_html}
</div>
"""


def to_df(records):
    out = []
    for c in records:
        news = c.get("news") or {}
        fail = (c.get("failed_filters") or []) + (c.get("tradability_warnings") or [])
        out.append(
            {
                "Ticker": c.get("symbol"),
                "Grade": c.get("setup_grade"),
                "Status": c.get("setup_label"),
                "Action": c.get("scanner_action"),
                "Action Reason": c.get("scanner_action_reason"),
                "Best Fit": c.get("timeframe_best_fit") or "UNKNOWN",
                "Fit Confidence": c.get("timeframe_fit_confidence") or "—",
                "Intraday Fit": c.get("timeframe_intraday_score"),
                "Swing Fit": c.get("timeframe_swing_score"),
                "Longer Fit": c.get("timeframe_longer_term_score"),
                "Score": c.get("score"),
                "Opportunity": c.get("opportunity_score"),
                "ML 60m %": c.get("ml_continuation_prob_pct"),
                "ML Status": "VALIDATED" if c.get("ml_validated") else str(c.get("ml_status") or "learning").upper(),
                "Price": c.get("price"),
                "Day %": c.get("day_pct"),
                "5m %": c.get("momentum_5m"),
                "15m %": c.get("momentum_15m"),
                "TOD Vol Pace": c.get("volume_pace"),
                "Normal Vol by Now %": c.get("expected_volume_fraction_pct"),
                "Vol vs Expected %": c.get("volume_vs_expected_pct"),
                "Vol Profile Days": c.get("volume_profile_samples"),
                "From High %": c.get("distance_from_high_pct"),
                "VWAP $": c.get("vwap"),
                "VWAP Status": "ABOVE" if c.get("above_vwap") else "BELOW",
                "Liquidity $M": round(
                    (c.get("liquidity_dollar_volume") or 0) / 1_000_000, 2
                ),
                "Live Spread %": (
                    c.get("live_spread_pct")
                    if c.get("live_spread_pct") is not None
                    else c.get("iex_spread_pct")
                ),
                "Catalyst": news.get("category") or "—",
                "Failed / Warning": " | ".join(fail[:3]) or "PASS",
            }
        )
    return pd.DataFrame(out)


def styled(df):
    if df.empty:
        return df

    def score_style(v):
        try:
            x = float(v)
        except Exception:
            return ""
        if x >= 75:
            return "background-color:rgba(34,197,94,.20);font-weight:800"
        if x >= 60:
            return "background-color:rgba(245,158,11,.20);font-weight:800"
        return "background-color:rgba(239,68,68,.17);font-weight:800"

    def grade_style(v):
        return {
            "A": "background-color:rgba(34,197,94,.20);font-weight:900",
            "B": "background-color:rgba(56,189,248,.18);font-weight:900",
            "C": "background-color:rgba(245,158,11,.20);font-weight:900",
            "REJECT": "background-color:rgba(239,68,68,.17);font-weight:900",
        }.get(str(v), "")

    def vwap_style(v):
        return (
            "color:#65e98d;font-weight:850"
            if v == "ABOVE"
            else "color:#ff8181;font-weight:850"
        )

    def ml_style(v):
        try:
            x = float(v)
        except Exception:
            return "color:#9fb0c9;font-weight:800"
        if x >= 65:
            return "color:#65e98d;font-weight:900"
        if x >= 50:
            return "color:#ffd166;font-weight:900"
        return "color:#ff8181;font-weight:900"

    def fit_style(v):
        return {
            "INTRADAY": "color:#7dd3fc;font-weight:900",
            "SWING": "color:#65e98d;font-weight:900",
            "LONGER-TERM": "color:#ffd166;font-weight:900",
            "MIXED": "color:#c4b5fd;font-weight:900",
        }.get(str(v), "color:#9fb0c9;font-weight:800")

    return (
        df.style.map(score_style, subset=["Score", "Opportunity", "Intraday Fit", "Swing Fit", "Longer Fit"])
        .map(ml_style, subset=["ML 60m %"])
        .map(grade_style, subset=["Grade"])
        .map(fit_style, subset=["Best Fit"])
        .map(vwap_style, subset=["VWAP Status"])
        .format(
            {
                "Score": "{:.1f}",
                "Opportunity": lambda x: "—" if pd.isna(x) else f"{x:.1f}",
                "Intraday Fit": lambda x: "—" if pd.isna(x) else f"{x:.0f}",
                "Swing Fit": lambda x: "—" if pd.isna(x) else f"{x:.0f}",
                "Longer Fit": lambda x: "—" if pd.isna(x) else f"{x:.0f}",
                "ML 60m %": lambda x: "—" if pd.isna(x) else f"{x:.1f}%",
                "Price": "${:.2f}",
                "Day %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "5m %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "15m %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "TOD Vol Pace": lambda x: "—" if pd.isna(x) else f"{x:.2f}x",
                "From High %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "VWAP $": lambda x: "—" if pd.isna(x) else f"${x:.2f}",
                "Liquidity $M": "${:.2f}M",
                "Live Spread %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
            }
        )
    )


if "auto_scan_enabled" not in st.session_state:
    st.session_state["auto_scan_enabled"] = True
if "last_auto_scan_at" not in st.session_state:
    st.session_state["last_auto_scan_at"] = 0.0
if "last_auto_message" not in st.session_state:
    st.session_state["last_auto_message"] = ""


controls_mount = st.session_state.get("_scanner_controls_mount")
controls_context = controls_mount.container() if controls_mount is not None else st.container(key="scanner_controls_top")

with controls_context:
    flash_success = st.session_state.pop("_scanner_flash_success", None)
    if flash_success:
        st.success(str(flash_success))

    scan_col, auto_col, spacer_col, market_col = st.columns(
        [1.15, 1.45, 2.45, 1.55],
        vertical_alignment="center",
    )
    with scan_col:
        scan_button_slot = st.empty()
        clicked = scan_button_slot.button(
            "▶ Run Fresh Scan",
            type="primary",
            use_container_width=True,
        )
        if clicked:
            scan_button_slot.button(
                "Working…",
                type="primary",
                use_container_width=True,
                disabled=True,
                key="scan_working_button",
            )
    with auto_col:
        feed_name = configured_live_label()
        coverage = (
            "4:00 AM–8:00 PM ET"
            if configured_live_provider() == "tradier" or configured_live_feed() == "sip"
            else "8:00 AM–5:00 PM ET"
        )
        st.toggle(
            "Auto scan every 2 minutes",
            key="auto_scan_enabled",
            help=(
                f"Runs about every 2 minutes while the Momentum Scanner view is active. "
                f"Browser backgrounding can delay refreshes. Current live feed: "
                f"{feed_name}; live scanner coverage: {coverage} on weekdays."
            ),
        )
    with market_col:
        market_open, now_et = market_is_open()
        phase = market_session_phase(now_et)
        if phase == "premarket" and not market_open:
            market_label = "🟡 PRE-MKT · LIVE DATA OFF"
        elif phase == "afterhours" and not market_open:
            market_label = "🟡 AFTER-HRS · LIVE DATA OFF"
        else:
            market_label = {
                "premarket": "🔵 PRE-MARKET",
                "regular": "🟢 MARKET OPEN",
                "afterhours": "🟣 AFTER-HOURS",
                "closed": "🟡 MARKET CLOSED",
            }[phase]
        st.markdown(
            f'<div class="market-box {"open" if market_open else "closed"}">'
            f'<span class="market-main">{market_label}</span>'
            f'<span class="market-time">{now_et:%I:%M %p ET}</span></div>',
            unsafe_allow_html=True,
        )

if clicked:
    with st.spinner("Scanning movers and ranking live setups…"):
        ok, msg = run_scanner()
    if ok:
        st.session_state["last_auto_scan_at"] = time.time()
        st.session_state["last_auto_message"] = "Manual scan completed."
        st.session_state["_scanner_flash_success"] = msg
        # The combined app renders its compact one-click candidate list before
        # scanner_app.py runs. Refresh once so that list immediately reads the
        # newly written latest_scan.json instead of staying one scan behind.
        st.rerun()
    else:
        st.error(msg)


scanner_return_grace_until = float(
    st.session_state.get("_scanner_return_grace_until") or 0.0
)
scanner_return_grace_active = scanner_return_grace_until > time.time()
auto_run_every = (
    3 if st.session_state["auto_scan_enabled"] and scanner_return_grace_active
    else AUTO_STATUS_REFRESH_SECONDS if st.session_state["auto_scan_enabled"]
    else None
)


@st.fragment(run_every=auto_run_every)
def auto_scan_controller():
    enabled = st.session_state["auto_scan_enabled"]
    open_now, now_et = market_is_open()

    grace_until = float(st.session_state.get("_scanner_return_grace_until") or 0.0)
    now_ts = time.time()
    if enabled and grace_until > now_ts:
        seconds = max(1, int(round(grace_until - now_ts)))
        st.markdown(
            '<div class="auto-box auto-on"><b>🟢 SCANNER READY</b><br>'
            f'<span class="sub">Showing the latest completed scan now. '
            f'Auto-refresh resumes in about {seconds}s.</span></div>',
            unsafe_allow_html=True,
        )
        return
    if grace_until:
        st.session_state.pop("_scanner_return_grace_until", None)

    if not enabled:
        st.markdown(
            '<div class="auto-box auto-off"><b>⏸ AUTO SCAN OFF</b><br>'
            '<span class="sub">Use Run Fresh Scan anytime.</span></div>',
            unsafe_allow_html=True,
        )
        return

    if not open_now:
        feed_name = configured_live_label()
        coverage = (
            "4:00 AM–8:00 PM ET"
            if configured_live_provider() == "tradier" or configured_live_feed() == "sip"
            else "8:00 AM–5:00 PM ET"
        )
        st.markdown(
            '<div class="auto-box auto-wait"><b>🟡 AUTO SCAN ARMED</b><br>'
            f'<span class="sub">Paused because the {feed_name} live feed is outside its '
            f'supported scanner window. Current coverage: {coverage} on weekdays.</span></div>',
            unsafe_allow_html=True,
        )
        return

    elapsed = time.time() - float(st.session_state.get("last_auto_scan_at", 0.0))
    remaining = max(0, AUTO_SCAN_SECONDS - elapsed)

    if elapsed >= AUTO_SCAN_SECONDS:
        with st.spinner("Automatic 2-minute scan running…"):
            ok, msg = run_scanner()
        st.session_state["last_auto_scan_at"] = time.time()
        st.session_state["last_auto_message"] = msg
        if ok:
            st.rerun()
        else:
            st.error(f"Automatic scan failed: {msg}")
        return

    # Keep the actual scan controller lightweight, but let the browser update
    # the visible countdown every second without rerunning the Streamlit app.
    initial_remaining = max(0, int(round(remaining)))
    components.html(
        f"""
        <div class="auto-box auto-on">
          <b>🟢 AUTO SCAN ON</b><br>
          <span class="sub">Next fresh scan in <span id="live-countdown">--:--</span>.
          The dashboard refreshes itself after each scan.</span>
        </div>
        <style>
          html,body{{margin:0;padding:0;background:transparent;color:#f4f8ff;
            font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}}
          .auto-box{{box-sizing:border-box;width:100%;
            background:linear-gradient(145deg,rgba(12,29,48,.92),rgba(7,20,34,.86));
            border:1px solid rgba(105,151,197,.24);border-left:3px solid #37ef79;
            border-radius:10px;padding:7px 11px;margin:1px 0 5px;
            box-shadow:inset 0 1px 0 rgba(255,255,255,.035),0 8px 24px rgba(0,0,0,.12);}}
          b{{font-size:12px;font-weight:900;letter-spacing:.02em;color:#eff8ff;}}
          .sub{{display:inline-block;color:#91a9c5;font-size:11px;margin-top:2px;}}
          #live-countdown{{font-variant-numeric:tabular-nums;font-weight:900;color:#76f7a5;}}
        </style>
        <script>
          (() => {{
            const countdown = document.getElementById('live-countdown');
            const deadline = performance.now() + ({initial_remaining} * 1000);
            function tick() {{
              const msLeft = Math.max(0, deadline - performance.now());
              const total = Math.max(0, Math.ceil(msLeft / 1000));
              const mins = Math.floor(total / 60);
              const secs = total % 60;
              countdown.textContent = `${{mins}}:${{String(secs).padStart(2, '0')}}`;
              if (total <= 0) {{
                countdown.textContent = '0:00 — scan starting…';
              }}
            }}
            tick();
            setInterval(tick, 250);
          }})();
        </script>
        """,
        height=54,
        scrolling=False,
    )


status_mount = st.session_state.get("_scanner_status_mount")
status_context = status_mount.container() if status_mount is not None else st.container(key="scanner_auto_status_top")
with status_context:
    auto_scan_controller()

payload = load_scan()

if payload is None:
    st.markdown(
        '<div class="header"><div class="title">Momentum Scanner</div>'
        '<div class="sub">The dashboard is ready, but it does not have a scan to display yet.</div></div>',
        unsafe_allow_html=True,
    )
    st.info(
        "Click **Run Fresh Scan**. Automatic scanning will also start on its own "
        "during pre-market, regular hours, and after-hours while this app is open."
    )
    st.stop()

mode = payload.get("mode", "off_hours_test")
session_phase = str(payload.get("session_phase") or "")
live = mode in {"regular_market_session", "extended_market_session"}
scan_et = payload.get("scan_time_et")

scan_age_seconds = None
try:
    scan_dt = datetime.fromisoformat(str(scan_et).replace("Z", "+00:00"))
    if scan_dt.tzinfo is None:
        scan_dt = scan_dt.replace(tzinfo=ET)
    scan_age_seconds = max(
        0.0,
        (datetime.now(ET) - scan_dt.astimezone(ET)).total_seconds(),
    )
    when = scan_dt.astimezone(ET).strftime("%a %b %d · %I:%M:%S %p ET")
except Exception:
    when = scan_et or "Unknown"

_market_open_for_stale_check, _now_et_for_stale_check = market_is_open()
scan_is_stale = bool(
    _market_open_for_stale_check
    and (scan_age_seconds is None or scan_age_seconds > 4 * 60)
)
if scan_is_stale:
    age_text = (
        "unknown age"
        if scan_age_seconds is None
        else (
            f"{scan_age_seconds / 60.0:.1f} minutes old"
            if scan_age_seconds < 3600
            else f"{scan_age_seconds / 3600.0:.1f} hours old"
        )
    )
    st.warning(
        "⚠️ **STALE SCAN — do not treat these rankings as current.** "
        f"The displayed snapshot is {age_text}. A fresh scan is due; use "
        "**Run Fresh Scan** or wait for auto-scan to complete."
    )

if session_phase == "premarket":
    pill_cls = "blue"
    pill_text = "● PRE-MARKET SESSION"
elif session_phase == "afterhours":
    pill_cls = "blue"
    pill_text = "● AFTER-HOURS SESSION"
elif mode == "regular_market_session":
    pill_cls = "green"
    pill_text = "● REGULAR MARKET SESSION"
elif mode == "extended_data_unavailable":
    pill_cls = "amber"
    feed_name = str((payload.get("data") or {}).get("live_feed") or "iex").upper()
    pill_text = f"● EXTENDED HOURS · {feed_name} LIVE DATA UNAVAILABLE"
else:
    pill_cls = "amber"
    pill_text = "● MARKET CLOSED / PREVIEW"

payload_summary = payload.get("summary") or {}
data_meta = payload.get("data") or {}
live_provider = str(data_meta.get("live_provider") or "alpaca").lower()
if live_provider == "tradier":
    live_data_pill = "LIVE DATA · TRADIER CONSOLIDATED"
    live_data_pill_cls = "green"
else:
    live_data_pill = f"LIVE DATA · ALPACA {str(data_meta.get('live_feed') or 'iex').upper()}"
    live_data_pill_cls = "blue"

ml_model = payload_summary.get("ml_model") or {}
ml_status = str(ml_model.get("status") or "learning")
ml_samples = int(ml_model.get("samples") or 0)
ml_days = int(ml_model.get("trading_days") or 0)
ml_auc = ml_model.get("walk_forward_auc")

if ml_model.get("validated"):
    ml_pill_cls = "green"
    ml_pill_text = f"ML VALIDATED · AUC {ml_auc if ml_auc is not None else '—'} · n={ml_samples}"
elif ml_status == "failed_validation":
    ml_pill_cls = "amber"
    ml_pill_text = f"ML NOT VALIDATED · AUC {ml_auc if ml_auc is not None else '—'} · n={ml_samples}"
elif ml_status == "paused_extended_hours":
    ml_pill_cls = "amber"
    ml_pill_text = "ML REGULAR-HOURS MODEL PAUSED"
elif ml_status == "extended_live_data_unavailable":
    ml_pill_cls = "amber"
    ml_pill_text = "ML PAUSED · extended live data unavailable"
elif ml_status in {"skipped_off_hours", "skipped_market_closed"}:
    ml_pill_cls = "amber"
    ml_pill_text = "ML PAUSED · market closed"
else:
    ml_pill_cls = "blue"
    ml_pill_text = f"ML LEARNING · {ml_days}/3 days · n={ml_samples}"

stale_pill_html = (
    '<span class="pill amber">⚠ STALE SNAPSHOT</span>'
    if scan_is_stale else ''
)
st.markdown(
    f'<div class="header"><div class="title">Momentum Scanner</div>'
    '<div class="sub">Readable ranking of momentum, liquidity, VWAP position, '
    'catalysts, historical context, timeframe fit, and validated ML continuation.</div>'
    f'<span class="pill {pill_cls}">{pill_text}</span>'
    f'<span class="pill {live_data_pill_cls}">{html.escape(live_data_pill)}</span>'
    f'<span class="pill {ml_pill_cls}">{html.escape(ml_pill_text)}</span>'
    f'{stale_pill_html}'
    f'<div class="sub">Last scan: {html.escape(str(when))} · '
    f'Scanner v{html.escape(str(payload.get("scanner_version","—")))}</div></div>',
    unsafe_allow_html=True,
)

summary = payload_summary
records = payload.get("candidates") or []
grades = summary.get("grade_counts") or {}

timeframe_filter = st.selectbox(
    "Timeframe focus",
    ["ALL", "INTRADAY", "SWING", "LONGER-TERM", "MIXED"],
    index=0,
    help=(
        "Filters the displayed momentum candidates by scanner-level Best Fit. "
        "It does not change ranking or Scanner ACTION."
    ),
)

def _matches_timeframe(row, selected):
    if selected == "ALL":
        return True
    if str(row.get("timeframe_best_fit") or "") == selected:
        return True
    return selected in (row.get("timeframe_fit_horizons") or [])

filtered_records = [
    row for row in records
    if _matches_timeframe(row, timeframe_filter)
]
display_records = filtered_records[:15]
full_table_records = filtered_records[:30]

if timeframe_filter != "ALL":
    st.caption(
        f"Showing {len(filtered_records)} of {len(records)} ranked momentum candidates "
        f"with {timeframe_filter} timeframe evidence. Ranking itself is unchanged."
    )

vals = [
    ("ANALYZED", summary.get("candidates_analyzed", len(records)), "Common-stock mover candidates"),
    ("BASE PASSES", summary.get("base_filter_passes", 0), "Passed every base filter"),
    ("NEAR MISSES", grades.get("C", 0), "Grade C: one filter short"),
    ("EXCLUDED", len(summary.get("excluded_non_common_symbols") or []), "Likely warrants / rights / units"),
]

cols = st.columns(4)
for col, (k, v, n) in zip(cols, vals):
    with col:
        st.markdown(
            f'<div class="stat"><div class="stat-k">{k}</div>'
            f'<div class="stat-v">{v}</div><div class="stat-n">{n}</div></div>',
            unsafe_allow_html=True,
        )

top = [
    c
    for c in display_records
    if c.get("setup_grade") in {"A", "B"} and c.get("passed_base_filters")
]
if not top:
    top = [c for c in display_records if c.get("setup_grade") in {"A", "B"}]
top = top[:4]

st.markdown(
    '<div class="section top-candidates-section">🔥 Top Candidates</div>',
    unsafe_allow_html=True,
)

if top:
    for i in range(0, len(top), 2):
        pair = st.columns(2)
        for j, c in enumerate(top[i : i + 2]):
            with pair[j]:
                st.markdown(card(c), unsafe_allow_html=True)
else:
    st.warning("No A/B candidates are available in this scan.")

st.markdown(
    """
<div class="legend-box">
  <div class="legend-title">Quick metric guide</div>
  <div class="legend-grid">
    <div class="legend-item">
      <div class="legend-term">Best Fit — Intraday / Swing / Longer-Term</div>
      <div class="legend-def">
        This is a separate horizon classification, not a buy signal. <b>Intraday</b> emphasizes same-day momentum, VWAP, volume and execution. <b>Swing</b> emphasizes roughly 2–10 trading days of continuation and multi-session structure. <b>Longer-Term</b> is a technical screen for roughly 2–8 weeks and should be confirmed in Analyzer with fundamentals, dilution/filings and catalyst durability. <b>Mixed</b> means two horizons scored similarly. This label does not change the scanner's momentum ranking or ACTION.
      </div>
    </div>
    <div class="legend-item">
      <div class="legend-term">VWAP — Volume-Weighted Average Price</div>
      <div class="legend-def">
        VWAP is the stock's average traded price for the current session, but it gives more weight to prices where more shares actually traded. That makes it more useful than a simple average for judging where most of the day's trading activity has occurred.<br><br>
        <b>Price above VWAP:</b> buyers are generally controlling the session and the stock is trading above the average price paid today. For a momentum trade, holding above VWAP is usually constructive.<br><br>
        <b>Price below VWAP:</b> the stock is trading below the day's volume-weighted average. That can mean momentum is weakening or sellers have taken control, so the scanner treats it as a warning.<br><br>
        <b>VWAP can act like support or resistance:</b> a strong stock may pull back toward VWAP, hold it, and bounce. A weak stock may rally into VWAP and get rejected.<br><br>
        <b>Distance matters:</b> being above VWAP is good, but being far above it can mean the stock is extended and more vulnerable to a pullback. A cleaner momentum entry is often a stock above VWAP without being excessively stretched away from it.<br><br>
        Example: if VWAP is <b>$10.00</b> and the stock is <b>$10.30</b>, it is about 3% above VWAP. If it is <b>$12.00</b>, it is 20% above VWAP — much stronger, but also much more extended and potentially riskier to chase.
      </div>
    </div>
    <div class="legend-item">
      <div class="legend-term">Liquidity</div>
      <div class="legend-def">
        <b>Higher liquidity:</b> easier entry/exit, tighter spreads, less slippage, and generally more reliable price action.<br><br>
        <b>Lower liquidity:</b> harder exits, wider spreads, more violent swings, and a greater chance of getting stuck in a position.<br><br>
        <b>Very high liquidity + strong volume pace:</b> usually a better-quality momentum setup because there is real participation behind the move.<br><br>
        High liquidity by itself does not make a stock bullish. The combination the scanner prefers is <b>strong price momentum + high liquidity + rising volume pace + a tight spread</b>.
      </div>
    </div>
    <div class="legend-item">
      <div class="legend-term">Time-of-Day Volume Pace</div>
      <div class="legend-def">This now uses the stock's <b>own recent intraday volume pattern</b>, not a straight-line assumption. The scanner looks at recent completed sessions and estimates what percentage of a normal day's volume this ticker usually has traded by the current clock time. That automatically accounts for the fact that volume is normally much heavier near the open and often quieter in the middle of the day.<br><br><b>1.00x:</b> about normal for this ticker at this exact time. <b>2.00x:</b> about twice the volume normally expected by now. <b>Normal Vol by Now %</b> shows the historical percentage of a typical day's volume usually completed by this time. The scanner uses a median-based baseline so one unusually huge prior day does not distort the comparison. If a ticker does not yet have enough intraday history, it temporarily falls back to the older linear calculation.</div>
    </div>
    <div class="legend-item">
      <div class="legend-term">IEX Spread</div>
      <div class="legend-def">The percentage gap between the current IEX bid and ask. A smaller spread generally means cleaner entries/exits and less immediate slippage. A wide spread can make a trade expensive even when the chart looks good. This reflects IEX quotes, not the full consolidated SIP market.</div>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

near = [c for c in display_records if c.get("setup_grade") == "C"][:8]
st.markdown(
    '<div class="section">🟡 Near Misses</div>'
    '<div class="section-sub">Close enough to watch, but one base rule still failed.</div>',
    unsafe_allow_html=True,
)
if near:
    st.dataframe(
        styled(to_df(near)),
        use_container_width=True,
        hide_index=True,
        height=min(300, 38 + 30 * len(near)),
    )
else:
    st.info("No Grade C near misses in this scan.")

st.markdown(
    '<div class="section">📊 Full Ranked Table</div>'
    '<div class="section-sub">Top 30 ranked scanner results.</div>',
    unsafe_allow_html=True,
)
df = to_df(full_table_records)
if not df.empty:
    df.insert(0, "#", range(1, len(df) + 1))
    st.dataframe(
        styled(df),
        use_container_width=True,
        hide_index=True,
        height=min(520, 38 + 30 * len(df)),
    )
else:
    st.info("No ranked candidates were logged.")

rej = Counter(r for c in display_records for r in (c.get("failed_filters") or []))
st.markdown(
    '<div class="section">🚫 Rejection Pattern</div>'
    '<div class="section-sub">Counts below come from the candidates saved in this dashboard snapshot.</div>',
    unsafe_allow_html=True,
)
if rej:
    m = max(rej.values())
    for reason, count in rej.most_common(8):
        w = max(6, int(count / m * 100))
        st.markdown(
            f'<div class="bar-row"><div class="bar-head"><span>{html.escape(reason)}</span>'
            f'<span>{count}</span></div><div class="bar-track">'
            f'<div class="bar-fill" style="width:{w}%"></div></div></div>',
            unsafe_allow_html=True,
        )
else:
    st.success("No rejection reasons were logged for the displayed candidates.")

with st.expander("Tradier vs Alpaca IEX diagnostics"):
    tradier_token = get_tradier_token()
    if not tradier_token:
        st.caption(
            "Tradier live comparison is ready, but TRADIER_ACCESS_TOKEN is not configured yet. "
            "A live Tradier Brokerage token is required for real-time consolidated data."
        )
    else:
        st.caption(
            "The scanner now uses Tradier consolidated data for live ranking inputs. "
            "This diagnostic compares those values with Alpaca IEX for reference."
        )
        if st.button(
            "Run Tradier vs IEX comparison",
            key="run_tradier_compare",
            use_container_width=False,
        ):
            os.environ["TRADIER_ACCESS_TOKEN"] = tradier_token
            os.environ["ALPACA_API_KEY"] = secret("ALPACA_API_KEY")
            os.environ["ALPACA_SECRET_KEY"] = secret("ALPACA_SECRET_KEY")

            from tradier_compare import run_provider_comparison

            compare_symbols = [
                str(row.get("symbol") or "").upper().strip()
                for row in display_records[:15]
                if row.get("symbol")
            ]
            with st.spinner("Comparing Tradier consolidated data with Alpaca IEX…"):
                st.session_state["provider_compare_result"] = run_provider_comparison(
                    compare_symbols
                )

        comparison = st.session_state.get("provider_compare_result")
        if comparison:
            status = comparison.get("status")
            if status != "ok":
                st.warning(comparison.get("message") or f"Comparison status: {status}")
            else:
                summary_cmp = comparison.get("summary") or {}
                c1, c2, c3 = st.columns(3)
                c1.metric(
                    "Symbols compared",
                    summary_cmp.get("symbols_compared", 0),
                )
                volume_mult = summary_cmp.get(
                    "avg_consolidated_to_iex_session_volume_multiple"
                )
                c2.metric(
                    "Tradier / IEX volume",
                    "—" if volume_mult is None else f"{volume_mult:.2f}x",
                )
                spread_gain = summary_cmp.get("avg_spread_improvement_pct_points")
                c3.metric(
                    "Avg spread difference",
                    "—" if spread_gain is None else f"{spread_gain:+.3f} pts",
                )

                rows_cmp = []
                for item in comparison.get("rows") or []:
                    t = item.get("tradier") or {}
                    a = item.get("alpaca_iex") or {}
                    d = item.get("difference") or {}
                    rows_cmp.append(
                        {
                            "Ticker": item.get("symbol"),
                            "Tradier Price": t.get("price"),
                            "IEX Price": a.get("price"),
                            "Price Δ %": d.get("price_diff_pct"),
                            "Tradier Vol": t.get("session_volume_70m"),
                            "IEX Vol": a.get("session_volume_70m"),
                            "Vol Δ %": d.get("session_volume_diff_pct"),
                            "Tradier Spread %": t.get("spread_pct"),
                            "IEX Spread %": a.get("spread_pct"),
                            "Tradier 5m %": t.get("momentum_5m"),
                            "IEX 5m %": a.get("momentum_5m"),
                            "Tradier 15m %": t.get("momentum_15m"),
                            "IEX 15m %": a.get("momentum_15m"),
                        }
                    )

                if rows_cmp:
                    st.dataframe(
                        pd.DataFrame(rows_cmp),
                        use_container_width=True,
                        hide_index=True,
                    )
                if comparison.get("errors"):
                    st.caption(
                        f"{len(comparison['errors'])} symbol(s) could not be compared."
                    )

with st.expander("What the colors mean"):
    st.markdown(
        "**Green — strong/pass.**  **Blue — watch/neutral.**  "
        "**Amber — caution/near miss.**  **Red — reject/risk.**\n\n"
        "Color is never the only cue; labels such as **PASS**, **WATCH**, "
        "**ABOVE VWAP**, and **REJECT** are shown too."
    )

with st.expander("Scanner technical output"):
    if st.session_state.get("scanner_out"):
        st.code(st.session_state["scanner_out"])
    else:
        st.caption("Run a fresh scan from this page to capture the console output here.")
